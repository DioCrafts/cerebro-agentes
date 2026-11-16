"""Browser tasks shared through the cases' database: a restart does not lose them, two processes agree.

Two ``GestorTareas`` on the same file stand for two service processes. No browser starts: the run itself
(``_correr``) is replaced by a stand-in that waits to be stopped, so what is tested is the persistence —
claiming, service-wide limits, remote stop/close, orphans after a restart, erasure with the case.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
import sqlite3
import time
from typing import Any

import pytest

from asistente.capacidades.navegador import persistencia, tareas
from asistente.capacidades.navegador.persistencia import MENSAJE_HUERFANA
from asistente.capacidades.navegador.tareas import GestorTareas, Servicios, Tarea, TareaError
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.modulos import registro as registro_mod
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.plataforma import settings
from tests.navegador.simulacion import Sitios
from tests.navegador.test_ejecutor import PARAMS_DATADIS
from tests.proactive import make_case

HOY = date(2026, 11, 16)


def _correr_simulado(self: GestorTareas, tarea: Tarea, spec: Any, servicios: Any, sesion: Any) -> None:
    """Stand-in for the browser run: «works» until stopped (or 10 s), then ends like the real one."""
    with tarea.lock:
        tarea.estado = "trabajando"
    tarea.cancelar.wait(10)
    with tarea.lock:
        tarea.estado = "cancelada" if tarea.cancelar.is_set() else "descargado"
        tarea.terminada = datetime.now(UTC)
        tarea.parametros = {}


@dataclass
class Dos:
    db: str
    a: GestorTareas
    b: GestorTareas
    servicios: Servicios
    cases: SQLiteCaseStateStore

    def preparar(self, gestor: GestorTareas, case_id: str = "c1", **params: Any) -> Tarea:
        return gestor.preparar(
            case_id=case_id, clave="datadis_consumo", parametros={**PARAMS_DATADIS, **params}, hoy=HOY
        )

    def iniciar(self, gestor: GestorTareas, tarea: Tarea, user_id: str | None = None) -> Tarea:
        return gestor.iniciar(tarea, modo="ventana", servicios=self.servicios, user_id=user_id)

    def fila(self, tarea_id: str) -> dict[str, Any]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            fila = conn.execute("SELECT * FROM navegador_tareas WHERE id = ?", (tarea_id,)).fetchone()
            return dict(fila) if fila is not None else {}
        finally:
            conn.close()


@pytest.fixture
def dos(tmp_path, monkeypatch: pytest.MonkeyPatch, entorno: Sitios) -> Iterator[Dos]:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "todos")
    monkeypatch.setattr(GestorTareas, "_correr", _correr_simulado)
    monkeypatch.setattr(persistencia, "LATIDO_S", 0.05)
    monkeypatch.setattr(tareas, "LATIDO_S", 0.05)
    registro_mod.reiniciar()
    db = str(tmp_path / "state.sqlite3")
    cases = SQLiteCaseStateStore(db)
    for case_id in ("c1", "c2", "c3"):
        cases.save_case(make_case(case_id))
    servicios = Servicios(cases=cases, acciones=AccionStore(db))
    a, b = GestorTareas(db_path=db), GestorTareas(db_path=db)
    try:
        yield Dos(db=db, a=a, b=b, servicios=servicios, cases=cases)
    finally:
        for gestor in (a, b):
            for tarea in list(gestor._tareas.values()):
                gestor.cerrar(tarea)
                gestor.esperar(tarea, 15)
        registro_mod.reiniciar()


def _esperar(condicion: Any, timeout: float = 10) -> None:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return
        time.sleep(0.05)
    raise AssertionError("no se cumplió a tiempo")


def test_a_task_prepared_in_one_process_is_seen_and_started_by_another(dos: Dos) -> None:
    tarea = dos.preparar(dos.a)
    # The preparing process does not keep it in memory: the database is the only copy.
    assert tarea.id not in dos.a._tareas
    vista = dos.b.get(tarea.id)
    assert vista is not None and vista.estado == "preparada"
    assert vista.parametros_visibles()["cups"] != PARAMS_DATADIS["cups"]  # personal: masked, as in the owner
    assert [t.id for t in dos.b.de_caso("c1")] == [tarea.id]

    dos.iniciar(dos.b, vista)
    assert tarea.id in dos.b._tareas and dos.fila(tarea.id)["propietario"] == dos.b.propietario
    # The parameters are no longer at rest once the task started.
    assert dos.fila(tarea.id)["parametros_json"] is None
    # The other process cannot start it a second time.
    with pytest.raises(TareaError) as exc:
        dos.iniciar(dos.a, tarea)
    assert exc.value.codigo == 409
    _esperar(lambda: (dos.a.get(tarea.id) or tarea).estado == "trabajando")


def test_the_service_wide_limits_hold_across_processes(dos: Dos, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "NAVEGADOR_MAX_SIMULTANEAS", 1)
    primera = dos.iniciar(dos.a, dos.preparar(dos.a, "c1"))
    # Same «user» (no accounts: the case) in the other process: one task at a time.
    with pytest.raises(TareaError) as exc:
        dos.iniciar(dos.b, dos.preparar(dos.b, "c1"))
    assert exc.value.codigo == 429 and "en curso" in str(exc.value)
    # Another case: the service-wide maximum (1) is reached in the OTHER process.
    with pytest.raises(TareaError) as exc:
        dos.iniciar(dos.b, dos.preparar(dos.b, "c2"))
    assert exc.value.codigo == 429 and "máximo de tareas a la vez" in str(exc.value)
    # The 24-hour count is shared too.
    assert dos.b.usadas(dos.b.clave_usuario(None, "c1")) == 1
    monkeypatch.setattr(settings, "NAVEGADOR_MAX_TAREAS_DIA", 1)
    dos.a.detener(primera)
    dos.a.esperar(primera, 15)
    with pytest.raises(TareaError) as exc:
        dos.iniciar(dos.b, dos.preparar(dos.b, "c1"))
    assert exc.value.codigo == 429 and "24 horas" in str(exc.value)


def test_stop_and_close_from_another_process_reach_the_owner(dos: Dos) -> None:
    tarea = dos.iniciar(dos.a, dos.preparar(dos.a))
    _esperar(lambda: dos.fila(tarea.id)["estado"] == "trabajando")
    with tarea.lock:
        tarea.captura = b"png"  # only in the owner's memory
    remota = dos.b.get(tarea.id)
    assert remota is not None and remota.captura is None
    dos.b.detener(remota)
    _esperar(lambda: dos.fila(tarea.id)["estado"] == "cancelada")
    vista = dos.b.get(tarea.id)
    assert vista is not None and vista.estado == "cancelada" and vista.terminada is not None
    # Close from B: the owner drops what only it held (the screenshot) next time it is asked.
    dos.b.cerrar(vista)
    assert dos.fila(tarea.id)["estado"] == "cerrada"
    propia = dos.a.get(tarea.id)
    assert propia is not None and propia.estado == "cerrada" and propia.captura is None


def test_a_prepared_task_survives_a_restart(dos: Dos) -> None:
    tarea = dos.preparar(dos.a)
    nuevo = GestorTareas(db_path=dos.db)  # the service restarted
    recuperada = nuevo.get(tarea.id)
    assert recuperada is not None and recuperada.parametros["hasta"] == date(2026, 9, 3)
    nuevo.iniciar(recuperada, modo="ventana", servicios=dos.servicios, user_id=None)
    _esperar(lambda: dos.fila(tarea.id)["estado"] == "trabajando")
    nuevo.cerrar(recuperada)
    nuevo.esperar(recuperada, 15)


def test_a_running_task_of_a_dead_process_is_closed_as_failed_and_audited(dos: Dos) -> None:
    tarea = dos.preparar(dos.a)
    viejo = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    conn = sqlite3.connect(dos.db)
    with conn:
        conn.execute(
            "UPDATE navegador_tareas SET estado = 'trabajando', propietario = 'otro:1:muerto', latido = ?, "
            "parametros_json = NULL WHERE id = ?",
            (viejo, tarea.id),
        )
    conn.close()
    reiniciado = GestorTareas(db_path=dos.db)
    vista = reiniciado.get(tarea.id)
    assert vista is not None and vista.estado == "fallida" and vista.mensaje == MENSAJE_HUERFANA
    eventos = [e for e in dos.cases.list_audit_events("c1") if e.get("tarea_id") == tarea.id]
    assert eventos[-1]["event_type"] == "navegador_tarea_terminada" and eventos[-1]["motivo"] == "servicio_reiniciado"


def test_old_rows_are_purged(dos: Dos) -> None:
    tarea = dos.preparar(dos.a)
    viejo = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    conn = sqlite3.connect(dos.db)
    with conn:
        conn.execute("UPDATE navegador_tareas SET creada = ? WHERE id = ?", (viejo, tarea.id))
    conn.close()
    assert GestorTareas(db_path=dos.db).get(tarea.id) is None
    assert dos.fila(tarea.id) == {}


def test_erasing_the_case_erases_its_tasks(dos: Dos) -> None:
    tarea = dos.iniciar(dos.a, dos.preparar(dos.a, "c3"))
    dos.a.detener(tarea)
    dos.a.esperar(tarea, 15)
    _esperar(lambda: dos.fila(tarea.id).get("estado") == "cancelada")
    dos.cases.delete_case("c3")
    assert dos.fila(tarea.id) == {}
    conn = sqlite3.connect(dos.db)
    try:
        filas = conn.execute("SELECT case_id, tarea_id FROM navegador_inicios").fetchall()
        texto = json.dumps(conn.execute("SELECT * FROM navegador_inicios").fetchall())
    finally:
        conn.close()
    # The 24-hour count stays (a hashed key and a time), with nothing pointing at the erased case.
    assert filas == [(None, None)] and '"c3"' not in texto and tarea.id not in texto


def test_without_a_database_the_tasks_stay_in_memory(monkeypatch: pytest.MonkeyPatch, entorno: Sitios) -> None:
    gestor = GestorTareas()
    tarea = gestor.preparar(case_id="c1", clave="datadis_consumo", parametros=dict(PARAMS_DATADIS), hoy=HOY)
    assert gestor.get(tarea.id) is tarea


def test_the_kill_switch_keeps_an_unopened_store_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", False)
    db = tmp_path / "nunca.sqlite3"
    gestor = GestorTareas(db_path=str(db))
    assert gestor.de_caso("c1") == [] and gestor.get("nav_x") is None
    assert not db.exists()


def test_the_process_wide_manager_uses_the_cases_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", str(tmp_path / "x.sqlite3"))
    monkeypatch.setattr(tareas, "_gestor", [])
    gestor = tareas.gestor()
    assert gestor._almacen is not None and gestor._almacen.db_path == str(tmp_path / "x.sqlite3")

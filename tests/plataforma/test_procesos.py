"""The process model: schedules, one run per slot across workers, the registry other packages plug into, the
worker's services and heartbeat, the API's dispatch switch and its start-up migration."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
import sqlite3
import sys
import threading
import types

from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hacienda_gpt import settings
from hacienda_gpt.api.routers import plataforma as plataforma_router
from hacienda_gpt.cli.plataforma import cli
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.eventos import despacho
from hacienda_gpt.plataforma import procesos
from hacienda_gpt.plataforma.migraciones import estado
from hacienda_gpt.plataforma.procesos import (
    ZONA,
    ContextoServicio,
    EstadoCompartido,
    Planificador,
    Programacion,
    Registro,
    Servicio,
    Trabajo,
    Worker,
    describir,
    salud,
)
from hacienda_gpt.plataforma.trabajos import TRABAJOS, registrar_incorporados, servicio_eventos


def _madrid(*args: int) -> datetime:
    return datetime(*args, tzinfo=ZONA)  # type: ignore[misc]


@pytest.fixture(autouse=True)
def _registro_limpio() -> Iterator[None]:
    procesos.reiniciar_registro()
    yield
    procesos.reiniciar_registro()


# --------------------------------------------------------------------------- schedules


def test_daily_weekly_monthly_and_interval_slots() -> None:
    diario = Programacion(hora=time(7, 0))
    assert diario.ultimo_turno(_madrid(2026, 11, 16, 6, 59)) == _madrid(2026, 11, 15, 7, 0)
    assert diario.ultimo_turno(_madrid(2026, 11, 16, 7, 0)) == _madrid(2026, 11, 16, 7, 0)
    assert diario.siguiente_turno(_madrid(2026, 11, 16, 7, 0)) == _madrid(2026, 11, 17, 7, 0)
    lunes = Programacion(hora=time(7, 30), dias_semana=frozenset({0}))
    assert lunes.ultimo_turno(_madrid(2026, 11, 18, 12, 0)) == _madrid(2026, 11, 16, 7, 30)  # 16-11-2026 is Monday
    mensual = Programacion(hora=time(6, 20), dias_mes=frozenset({4, 19}))
    assert mensual.siguiente_turno(_madrid(2026, 11, 16, 0, 0)) == _madrid(2026, 11, 19, 6, 20)
    cada = Programacion(cada_minutos=30)
    assert cada.ultimo_turno(_madrid(2026, 11, 16, 10, 47)) == _madrid(2026, 11, 16, 10, 30)
    # Spanish time whatever the clock's zone: 06:00 UTC on a winter day is 07:00 in Madrid.
    assert diario.ultimo_turno(datetime(2026, 11, 16, 6, 0, tzinfo=UTC)) == _madrid(2026, 11, 16, 7, 0)
    with pytest.raises(ValueError):
        Programacion()
    with pytest.raises(ValueError):
        Trabajo("x", "x", diario)


def test_the_built_in_jobs_are_the_documented_cron_lines() -> None:
    por_nombre = {t.nombre: t for t in TRABAJOS}
    assert {"run-daily", "run-weekly", "boe-watch", "boe-novedades", "hipoteca-indices", "correo-sincronizar"} <= set(
        por_nombre
    )
    argv = por_nombre["run-daily"].argv()
    assert argv[1:] == [
        "-m",
        "hacienda_gpt.cli.ops",
        "run",
        "run-daily",
        "--",
        sys.executable,
        "-m",
        "hacienda_gpt.cli.proactive",
        "run-daily",
    ]
    # deliver records itself (as in the cron line).
    assert por_nombre["deliver"].argv()[1:] == ["-m", "hacienda_gpt.cli.ops", "deliver"]
    # Off by default: what writes into the repository's data, or needs the corpus and its models.
    assert not por_nombre["params-autopilot"].por_defecto and not por_nombre["indice-corpus"].por_defecto


def test_the_active_jobs_follow_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = Registro()
    registrar_incorporados(reg)
    activos = {t.nombre for t in reg.trabajos_activos()}
    assert "run-daily" in activos and "params-autopilot" not in activos
    monkeypatch.setattr(settings, "PLATAFORMA_TRABAJOS_DESACTIVADOS", "correo-sincronizar, doctor")
    assert not {"correo-sincronizar", "doctor"} & {t.nombre for t in reg.trabajos_activos()}
    monkeypatch.setattr(settings, "PLATAFORMA_TRABAJOS", "run-daily,params-autopilot,no-existe")
    assert [t.nombre for t in reg.trabajos_activos()] == ["run-daily", "params-autopilot"]
    monkeypatch.setattr(settings, "PLATAFORMA_TRABAJOS", "ninguno")
    assert reg.trabajos_activos() == []


# --------------------------------------------------------------------------- one run per slot


def test_two_workers_run_each_slot_once(tmp_path) -> None:
    db = str(tmp_path / "s.sqlite3")
    corridas: list[str] = []
    lock = threading.Lock()

    def ejecutar(trabajo: Trabajo) -> int:
        with lock:
            corridas.append(trabajo.nombre)
        return 0

    trabajo = Trabajo("diario", "d", Programacion(hora=time(7, 0)), funcion=lambda: 0)
    a = Planificador(EstadoCompartido(db), [trabajo], propietario="w1", ejecutar=ejecutar)
    b = Planificador(EstadoCompartido(db), [trabajo], propietario="w2", ejecutar=ejecutar)
    ahora = _madrid(2026, 11, 16, 7, 1)
    assert a.comprobar(ahora, esperar=True) == ["diario"]
    assert b.comprobar(ahora, esperar=True) == []
    assert a.comprobar(ahora + timedelta(hours=1), esperar=True) == []
    assert corridas == ["diario"]
    # The next day's slot runs once more, in whichever worker gets there first.
    assert b.comprobar(ahora + timedelta(days=1), esperar=True) == ["diario"]
    assert corridas == ["diario", "diario"]
    fila = a.estado.trabajos()[0]
    assert fila["propietario"] == "w2" and fila["codigo"] == 0 and fila["fin"] is not None


def test_a_missed_slot_is_recovered_only_within_the_window(tmp_path) -> None:
    db = str(tmp_path / "s.sqlite3")
    trabajo = Trabajo("diario", "d", Programacion(hora=time(7, 0)), funcion=lambda: 0)
    plan = Planificador(EstadoCompartido(db), [trabajo], propietario="w", recuperar=timedelta(hours=6))
    assert plan.comprobar(_madrid(2026, 11, 16, 14, 0), esperar=True) == []  # 7 h late: wait for tomorrow
    assert plan.comprobar(_madrid(2026, 11, 17, 12, 59), esperar=True) == ["diario"]


def test_a_failing_job_is_recorded_and_does_not_stop_the_others(tmp_path) -> None:
    db = str(tmp_path / "s.sqlite3")

    def rompe() -> int:
        raise RuntimeError("roto")

    trabajos = [
        Trabajo("roto", "r", Programacion(cada_minutos=30), funcion=rompe),
        Trabajo("bien", "b", Programacion(cada_minutos=30), funcion=lambda: 0),
    ]
    plan = Planificador(EstadoCompartido(db), trabajos, propietario="w")
    assert sorted(plan.comprobar(datetime.now(UTC), esperar=True)) == ["bien", "roto"]
    filas = {f["nombre"]: f for f in plan.estado.trabajos()}
    assert filas["roto"]["codigo"] == 1 and filas["bien"]["codigo"] == 0


# --------------------------------------------------------------------------- registry and extensions


def test_other_packages_plug_services_and_jobs_through_an_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    extension = types.ModuleType("extension_de_prueba")

    def registrar_en_plataforma(registro_: Registro) -> None:
        registro_.registrar_servicio(Servicio("motor", "Motor de tareas", arrancar=lambda c: None))

    extension.registrar_en_plataforma = registrar_en_plataforma  # type: ignore[attr-defined]
    rota = types.ModuleType("extension_rota")
    rota.registrar_en_plataforma = lambda r: 1 / 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "extension_de_prueba", extension)
    monkeypatch.setitem(sys.modules, "extension_rota", rota)
    monkeypatch.setattr(settings, "PLATAFORMA_EXTENSIONES", "extension_de_prueba, extension_rota, no_instalada")
    reg = procesos.registro()
    assert {"eventos", "motor"} <= set(reg.servicios)
    assert reg.extensiones["extension_de_prueba"] == "cargada"
    assert reg.extensiones["extension_rota"].startswith("error")
    assert reg.extensiones["no_instalada"] == "no instalado"
    assert {s["nombre"] for s in describir(reg)["servicios"]} >= {"eventos", "motor"}


# --------------------------------------------------------------------------- the worker


def test_the_worker_migrates_runs_services_and_jobs_and_beats(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PLATAFORMA_LATIDO_SEGUNDOS", 1)
    db = str(tmp_path / "s.sqlite3")
    arrancado = threading.Event()
    hechos: list[str] = []

    def servicio(contexto: ContextoServicio) -> None:
        arrancado.set()
        contexto.parar.wait()

    reg = Registro()
    reg.registrar_servicio(Servicio("motor", "m", arrancar=servicio))
    reg.registrar_trabajo(
        Trabajo("cada", "c", Programacion(cada_minutos=30), funcion=lambda: hechos.append("cada") or 0)
    )
    worker = Worker(db, registro_=reg, nombre="worker-prueba")
    hilo = threading.Thread(target=worker.ejecutar)
    hilo.start()
    try:
        assert arrancado.wait(10)
        limite = datetime.now(UTC) + timedelta(seconds=10)
        while datetime.now(UTC) < limite and not salud(db)["worker_vivo"]:
            threading.Event().wait(0.1)
        datos = salud(db)
        assert datos["worker_vivo"] and datos["migraciones_pendientes"] == {}
        assert datos["workers"][0]["detalle"]["servicios"] == {"motor": True}
    finally:
        worker.parar.set()
        hilo.join(30)
    assert not hilo.is_alive() and hechos == ["cada"]
    # It says goodbye: no stale heartbeat left behind.
    assert salud(db)["workers"] == []
    assert all(not e.pendientes for e in estado(db))


def test_a_crashing_service_is_restarted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "s.sqlite3")
    intentos: list[int] = []
    listo = threading.Event()

    def fragil(contexto: ContextoServicio) -> None:
        intentos.append(1)
        if len(intentos) == 1:
            raise RuntimeError("primer fallo")
        listo.set()
        contexto.parar.wait()

    reg = Registro()
    reg.registrar_servicio(Servicio("fragil", "f", arrancar=fragil))
    worker = Worker(db, registro_=reg, trabajos=False)
    monkeypatch.setattr(worker.parar, "wait", lambda timeout=None: threading.Event.wait(worker.parar, 0.01))
    worker.preparar()
    worker._arrancar_servicios()
    assert listo.wait(10) and len(intentos) == 2
    worker.cerrar()


def test_the_event_bus_service_beats_in_the_bus_table(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EVENTOS_INTERVALO_SEGUNDOS", 0.5)
    db = str(tmp_path / "s.sqlite3")
    parar = threading.Event()
    hilo = threading.Thread(target=servicio_eventos, args=(ContextoServicio(db, "w1", parar),))
    hilo.start()
    try:
        limite = datetime.now(UTC) + timedelta(seconds=10)
        nombres: list[str] = []
        while datetime.now(UTC) < limite and not nombres:
            threading.Event().wait(0.1)
            conn = sqlite3.connect(db)
            try:
                nombres = [f[0] for f in conn.execute("SELECT nombre FROM eventos_trabajadores")]
            except sqlite3.OperationalError:
                nombres = []
            finally:
                conn.close()
        assert nombres == ["w1-eventos"]
    finally:
        parar.set()
        hilo.join(10)


def test_the_cli_runs_one_pass_and_reports_health(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "s.sqlite3")
    reg = Registro()
    reg.registrar_trabajo(Trabajo("cada", "c", Programacion(cada_minutos=30), funcion=lambda: 0))
    reg.registrar_servicio(Servicio("eventos", "bus", arrancar=servicio_eventos))  # the one-pass mode runs the bus once
    procesos.reiniciar_registro(reg)
    runner = CliRunner()
    resultado = runner.invoke(cli, ["worker", "--db-path", db, "--una-vez"])
    assert resultado.exit_code == 0, resultado.output
    assert '"trabajos": [\n    "cada"\n  ]' in resultado.output and '"reconciliados"' in resultado.output
    resultado = runner.invoke(cli, ["salud", "--db-path", db])
    assert resultado.exit_code == 0, resultado.output
    # No worker is running now: a health check that requires one fails.
    assert runner.invoke(cli, ["salud", "--db-path", db, "--exigir-worker"]).exit_code == 1
    resultado = runner.invoke(cli, ["procesos"])
    assert resultado.exit_code == 0 and '"cada"' in resultado.output


# --------------------------------------------------------------------------- the API side


def test_the_api_can_leave_event_dispatch_to_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    assert procesos.despacho_en_api()
    monkeypatch.setattr(settings, "EVENTOS_DESPACHO_EN_API", False)
    assert not procesos.despacho_en_api()
    assert not despacho.habilitar("x.db")
    assert not despacho.habilitado()


def test_the_api_migrates_its_database_at_start_up(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "api.sqlite3")
    store_original = SQLiteCaseStateStore(str(tmp_path / "otra.sqlite3"))
    privado = SQLiteCaseStateStore(db)

    def get_case_store() -> SQLiteCaseStateStore:
        return store_original

    mini = FastAPI()
    mini.include_router(plataforma_router.build_router(get_case_store=get_case_store))
    mini.dependency_overrides[get_case_store] = lambda: privado
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    with TestClient(mini) as client:
        # The overridden (private) database was migrated, every owner included.
        assert all(not e.pendientes for e in estado(db))
        respuesta = client.get("/plataforma/salud")
        assert respuesta.status_code == 200 and respuesta.json()["migraciones_pendientes"] == {}
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.delenv("HACIENDA_OPERATOR_KEY", raising=False)
    with TestClient(mini) as client:
        assert client.get("/plataforma/salud").status_code == 404  # the operator's, not the users'

"""The worker's service ``mensajeria`` (channels + gateway + chores), its registration, and the CLI."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import threading
import time

from click.testing import CliRunner
import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import Notification, NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.cli.mensajeria import cli
from asistente.plataforma.mensajeria import servicio as servicio_mod
from asistente.plataforma.mensajeria.almacen import almacen
from asistente.plataforma.mensajeria.emparejamiento import nuevo_codigo
from asistente.plataforma.mensajeria.pasarela import Pasarela
from asistente.plataforma.mensajeria.prueba import CanalPrueba
from asistente.plataforma.procesos import ContextoServicio, Registro, Worker
from asistente.plataforma.trabajos import registrar_incorporados
from tests.cerebro.fakes import caso


@pytest.fixture
def db(tmp_path, entorno_mensajeria: None) -> str:
    ruta = str(tmp_path / "estado.sqlite3")
    casos = SQLiteCaseStateStore(ruta)
    casos.save_case(caso())
    casos.close()
    return ruta


def _hasta(condicion, segundos: float = 10.0) -> bool:  # type: ignore[no-untyped-def]
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def test_the_service_runs_the_channel_with_the_gateway_and_its_chores_then_stops_cleanly(db: str) -> None:
    canal = CanalPrueba()
    pasarela = Pasarela(db, canales=[canal], agente=lambda: None)
    servicio = servicio_mod.ServicioMensajeria(db, canales=[canal], pasarela=pasarela, intervalo_avisos=5.0, tick=0.02)
    parar = threading.Event()
    hilo = threading.Thread(target=servicio.arrancar, args=(parar,))
    hilo.start()
    try:
        assert canal.iniciado.wait(5)
        # The channel's state reaches the database (the API reads it from there).
        assert _hasta(lambda: bool(almacen(db).estados()))
        estado, _momento = almacen(db).estados()[0]
        assert estado.conectado and estado.direccion == canal.direccion
        # Messages flow through the gateway: pairing.
        codigo = nuevo_codigo(
            almacen(db), canal="prueba", user_id=None, case_id="case-cerebro", momento=datetime.now(UTC)
        )
        canal.recibir("c1", codigo.codigo)
        assert _hasta(lambda: almacen(db).vinculo_de("prueba", "c1") is not None)
        # A disconnection queued by the API is carried out.
        vinculo = almacen(db).vinculo_de("prueba", "c1")
        assert vinculo is not None
        almacen(db).desvincular(vinculo.id, motivo="app", encolar=True, momento=datetime.now(UTC))
        assert _hasta(lambda: canal.desconectados == ["c1"])
    finally:
        parar.set()
        hilo.join(10)
    assert not hilo.is_alive() and servicio.vueltas > 0
    # The channel was stopped and the gateway closed.
    assert not canal.estado().conectado


def test_the_service_delivers_notices_on_its_interval(db: str) -> None:
    canal = CanalPrueba()
    servicio = servicio_mod.ServicioMensajeria(db, canales=[canal], intervalo_avisos=0.05, tick=0.02)
    servicio.pasarela._agente = lambda: None  # type: ignore[method-assign]
    parar = threading.Event()
    hilo = threading.Thread(target=servicio.arrancar, args=(parar,))
    hilo.start()
    try:
        assert canal.iniciado.wait(5)
        codigo = nuevo_codigo(
            almacen(db), canal="prueba", user_id=None, case_id="case-cerebro", momento=datetime.now(UTC)
        )
        canal.recibir("c1", codigo.codigo)
        assert _hasta(lambda: almacen(db).vinculo_de("prueba", "c1") is not None)
        store = NotificationStore(db)
        store.add(
            Notification(
                case_id="case-cerebro", kind="plazo", key="k1", title="Plazo", body="Mañana.", severity="urgente"
            )
        )
        store.close()
        assert _hasta(lambda: any(t.startswith("*Aviso: Plazo*") for t in canal.textos("c1")))
    finally:
        parar.set()
        hilo.join(10)


def test_a_channel_that_dies_is_started_again(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(servicio_mod, "_PAUSA_REINICIO_S", 0.0)
    intentos: list[int] = []

    class CanalFragil(CanalPrueba):
        def iniciar(self, manejador) -> None:  # type: ignore[no-untyped-def]
            intentos.append(1)
            if len(intentos) == 1:
                raise RuntimeError("se cae al arrancar")
            super().iniciar(manejador)

    canal = CanalFragil()
    servicio = servicio_mod.ServicioMensajeria(db, canales=[canal], tick=0.02)
    parar = threading.Event()
    hilo = threading.Thread(target=servicio.arrancar, args=(parar,))
    hilo.start()
    try:
        assert canal.iniciado.wait(5) and len(intentos) >= 2
    finally:
        parar.set()
        hilo.join(10)


def test_channels_are_built_from_the_settings(monkeypatch: pytest.MonkeyPatch, entorno_mensajeria: None) -> None:
    monkeypatch.setattr(settings, "MENSAJERIA_CANALES", " prueba, nada ,prueba")
    assert servicio_mod.canales_configurados() == ["prueba"]
    (canal,) = servicio_mod.construir_canales()
    assert isinstance(canal, CanalPrueba)
    # The default channel is the SimpleX adapter, built from its own settings (nothing connects until it starts).
    from asistente.plataforma.mensajeria.simplex import CanalSimplex

    monkeypatch.setattr(settings, "MENSAJERIA_CANALES", "simplex")
    (simplex,) = servicio_mod.construir_canales()
    assert isinstance(simplex, CanalSimplex) and simplex.nombre == "simplex"
    assert simplex.capacidades.edicion and simplex.capacidades.formato == "simplex"
    assert not simplex.estado().conectado
    monkeypatch.setattr(settings, "MENSAJERIA_CANALES", "prueba")
    monkeypatch.setitem(servicio_mod.ADAPTADORES, "prueba", "asistente.plataforma.mensajeria.no_existe:Nada")
    assert servicio_mod.construir_canales() == []  # logged and skipped


def test_the_service_is_registered_in_the_worker_and_on_only_with_the_setting(
    monkeypatch: pytest.MonkeyPatch, db: str
) -> None:
    for activa in (False, True):
        monkeypatch.setattr(settings, "MENSAJERIA_ACTIVA", activa)
        reg = Registro()
        registrar_incorporados(reg)
        assert "mensajeria" in reg.servicios
        assert ("mensajeria" in {s.nombre for s in reg.servicios_activos()}) is activa
    # Off: the service function returns at once.
    monkeypatch.setattr(settings, "MENSAJERIA_ACTIVA", False)
    parar = threading.Event()
    servicio_mod.ejecutar(ContextoServicio(db_path=db, nombre_proceso="w", parar=parar))


def test_the_worker_runs_the_gateway_with_the_configured_channel(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MENSAJERIA_ACTIVA", True)
    monkeypatch.setattr(settings, "MENSAJERIA_CANALES", "prueba")
    reg = Registro()
    registrar_incorporados(reg)
    solo = Registro()
    solo.registrar_servicio(reg.servicios["mensajeria"])
    worker = Worker(db, registro_=solo, trabajos=False, nombre="worker-mensajeria")
    worker.preparar()
    worker._arrancar_servicios()
    try:
        assert _hasta(lambda: bool(almacen(db).estados()))
        assert worker.detalle()["servicios"] == {"mensajeria": True}
    finally:
        worker.cerrar(10)
    assert worker.detalle()["servicios"] == {"mensajeria": False}


def test_the_cli_reports_aggregates_and_generates_a_code(db: str) -> None:
    runner = CliRunner()
    salida = runner.invoke(cli, ["estado", "--db-path", db])
    assert salida.exit_code == 0, salida.output
    estado = json.loads(salida.output)
    assert estado["activa"] is True and estado["canales_configurados"] == ["prueba"]
    assert estado["chats_vinculados"] == {}

    salida = runner.invoke(cli, ["emparejar-codigo", "--db-path", db, "--case", "case-cerebro"])
    assert salida.exit_code == 0, salida.output
    datos = json.loads(salida.output)
    assert datos["canal"] == "prueba" and len(datos["codigo"]) == 14
    assert runner.invoke(cli, ["emparejar-codigo", "--db-path", db, "--case", "no-existe"]).exit_code == 1

    canal = CanalPrueba()
    pasarela = Pasarela(db, canales=[canal], agente=lambda: None)
    canal.iniciar_en_hilo(pasarela.manejar)
    canal.recibir("c1", datos["codigo"])
    assert pasarela.esperar(10)
    canal.parar()
    pasarela.cerrar(5)
    estado = json.loads(runner.invoke(cli, ["estado", "--db-path", db]).output)
    assert estado["chats_vinculados"] == {"prueba": 1} and "c1" not in json.dumps(estado)

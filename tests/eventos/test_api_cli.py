"""End to end: the API emits, the bus wakes the module the user switched on, erasure leaves no trace;
the in-process dispatcher, the router's start-up wiring, the worker CLI and ``ops doctor``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import sqlite3
import time

from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hacienda_gpt import settings
from hacienda_gpt.api.api import app, get_agent, get_case_store, get_notification_store
from hacienda_gpt.api.routers import eventos as eventos_router
from hacienda_gpt.api.routers.auth import get_login_limiter
from hacienda_gpt.auth.deps import get_auth_store
from hacienda_gpt.auth.ratelimit import LoginRateLimiter
from hacienda_gpt.auth.store import AuthStore
from hacienda_gpt.cli import ops as ops_module
from hacienda_gpt.cli.eventos import cli as eventos_cli
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.eventos import despacho, productores
from hacienda_gpt.eventos.bus import Bus
from hacienda_gpt.eventos.esquema import TABLAS
from hacienda_gpt.eventos.tipos import MODULO_ACTIVADO
from hacienda_gpt.proactive.store import NotificationStore
from tests.proactive import SALARIED, make_case
from .ayudas import HOY, Llamadas, agua, aviso, contador, crear_bus, instalar, seguros

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}


@dataclass
class Env:
    client: TestClient
    db_path: str
    cases: SQLiteCaseStateStore
    notifications: NotificationStore

    def register(self, email: str) -> dict:
        response = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def new_case(self, headers: dict) -> str:
        response = self.client.post("/cases", json={"user_id": "x", "tax_period": "2026"}, headers=headers)
        assert response.status_code == 200, response.text
        return str(response.json()["case_id"])


@pytest.fixture
def env(tmp_path, monkeypatch) -> Iterator[Env]:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    db_path = str(tmp_path / "state.sqlite3")
    llamadas = Llamadas()
    instalar(agua(llamadas, avisos_fijos=[aviso("factura", titulo="Factura del agua")]), seguros(llamadas))
    productores.instalar()  # what the API's start-up does
    environment = Env(
        client=TestClient(app),
        db_path=db_path,
        cases=SQLiteCaseStateStore(db_path),
        notifications=NotificationStore(db_path),
    )
    auth = AuthStore(db_path)
    overrides = {
        get_auth_store: lambda: auth,
        get_case_store: lambda: environment.cases,
        get_notification_store: lambda: environment.notifications,
        get_login_limiter: lambda: LoginRateLimiter(50, 900),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield environment
    finally:
        for dependency in [*overrides, get_agent]:
            app.dependency_overrides.pop(dependency, None)


def _volcado(db: str) -> str:
    with sqlite3.connect(db) as conn:
        return "\n".join(str(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in TABLAS)


def test_switching_a_module_on_and_editing_its_fact_wake_only_it_and_erasure_leaves_nothing(env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    assert env.client.put("/me/modulos/agua", json={"activo": True}, headers=ana).status_code == 200
    respuesta = env.client.put(f"/cases/{case_id}/facts/agua_personas", json={"value": 3}, headers=ana)
    assert respuesta.status_code == 200, respuesta.text

    bus = Bus(env.db_path, hoy=lambda: HOY)  # the real routing: the owner's choices in the auth store
    bus.drenar()
    entregas = [(f["tipo"], f["modulo"], f["estado"]) for f in bus._leer("SELECT * FROM eventos_entregas")]
    assert entregas == [(MODULO_ACTIVADO, "agua", "hecho"), ("perfil.cambiado", "agua", "hecho")]
    assert [n.key for n in env.notifications.list(case_id)] == ["modulo:agua:factura"]
    assert "3.0" not in str(bus._leer("SELECT payload_json FROM eventos"))

    # Off again: the timer and the pending work of the module go.
    bus.programar(case_id, "agua", HOY)
    assert env.client.put("/me/modulos/agua", json={"activo": False}, headers=ana).status_code == 200
    bus.avanzar_consumidores()
    assert bus.temporizadores() == []

    assert env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    bus.close()
    assert case_id not in _volcado(env.db_path)


def test_the_health_endpoint_reports_aggregates(env) -> None:
    respuesta = env.client.get("/eventos/salud")
    assert respuesta.status_code == 200
    assert respuesta.json()["salud"] == {"instalado": False}
    bus = crear_bus(env.db_path, modulos=[])
    bus.emitir(MODULO_ACTIVADO, case_id="c-secreto", payload={"modulo": "agua"})
    cuerpo = env.client.get("/eventos/salud").json()
    assert cuerpo["salud"]["instalado"] is True and cuerpo["salud"]["sin_repartir"] == {"modulos": 1}
    assert "c-secreto" not in json.dumps(cuerpo)


def test_the_router_wires_the_bus_at_start_up_and_stops_it_at_shutdown(tmp_path) -> None:
    store = SQLiteCaseStateStore(str(tmp_path / "e.sqlite3"))
    mini = FastAPI()
    mini.include_router(eventos_router.build_router(get_case_store=lambda: store))
    assert not productores.instalado()
    with TestClient(mini):
        assert productores.instalado() and despacho.habilitado()
    assert not despacho.habilitado()


def test_in_process_dispatch_wakes_the_module_within_seconds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "todos")
    monkeypatch.setattr(settings, "EVENTOS_INTERVALO_SEGUNDOS", 0.5)
    m = contador("agua")
    instalar(m)
    db = str(tmp_path / "e.sqlite3")
    casos, notas = SQLiteCaseStateStore(db), NotificationStore(db)
    productores.instalar()
    assert despacho.habilitar(db)
    casos.save_case(make_case("c1", agua_dato=1))
    limite = time.monotonic() + 10
    while not notas.list("c1") and time.monotonic() < limite:
        time.sleep(0.05)
    assert [n.key for n in notas.list("c1")] == ["modulo:agua:aviso:c1"]
    despacho.detener()
    assert not despacho.habilitado()


def test_in_process_dispatch_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "EVENTOS_DESPACHO_EN_PROCESO", False)
    assert not despacho.habilitar("x.db")
    despacho.despertar("x.db")  # no thread, no error
    assert not despacho.habilitado()


def test_the_worker_runs_once_from_cron(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    db = str(tmp_path / "e.sqlite3")
    productores.instalar()
    SQLiteCaseStateStore(db).save_case(make_case("c1", **SALARIED))
    resultado = CliRunner().invoke(eventos_cli, ["worker", "--una-vez", "--db-path", db])
    assert resultado.exit_code == 0, resultado.output
    resumen = json.loads(resultado.output)
    assert resumen["entregas"] == 1 and resumen["hecho"] == 1 and resumen["reconciliados"] == 1
    assert resumen["purgado"] == {"entregas": 0, "eventos": 0}  # nothing is 30 days old yet

    estado = json.loads(CliRunner().invoke(eventos_cli, ["estado", "--db-path", db]).output)
    assert estado["metricas"][0]["modulo"] == "fiscal" and estado["cola_de_errores"] == []
    assert json.loads(CliRunner().invoke(eventos_cli, ["reintentar", "--db-path", db]).output) == {"reencoladas": 0}
    assert json.loads(CliRunner().invoke(eventos_cli, ["reconciliar", "--db-path", db]).output)["temporizadores"] == 1
    assert CliRunner().invoke(eventos_cli, ["purgar", "--db-path", db]).exit_code == 0


def test_ops_doctor_shows_the_bus_and_its_dead_letters(tmp_path, monkeypatch) -> None:
    db = str(tmp_path / "state.sqlite3")
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", db)
    monkeypatch.setattr(settings, "JOBS_STATE_PATH", str(tmp_path / "_jobs.json"))
    monkeypatch.setattr(settings, "HEALTHCHECK_URL", "")
    monkeypatch.setattr(ops_module, "canales_configurados", lambda: [])
    sin_tareas = [a for n in ops_module.MAX_HORAS_POR_DEFECTO for a in ("--max-horas", f"{n}=0")]

    vacio = CliRunner().invoke(ops_module.cli, ["doctor", "--no-notify", *sin_tareas])
    assert vacio.exit_code == 0 and "eventos" not in json.loads(vacio.output)  # never used: nothing new shown

    m = contador("agua", fallos=99)
    instalar(m)
    SQLiteCaseStateStore(db).save_case(make_case("c1"))
    bus = crear_bus(db, modulos=[m], max_intentos=1)
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.drenar()
    resultado = CliRunner().invoke(ops_module.cli, ["doctor", "--no-notify", *sin_tareas])
    assert resultado.exit_code == 1
    informe = json.loads(resultado.output)
    assert informe["eventos"]["muertas"] == 1
    assert any("Bus de eventos" in p for p in informe["problemas"])

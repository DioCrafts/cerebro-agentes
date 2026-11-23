"""``GET /navegador/tareas/{id}/eventos``: a task followed live over Server-Sent Events (no browser is started: the
task is placed in the manager and moved by hand, as the executor would)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from asistente.capacidades.navegador.tareas import GestorTareas, Tarea
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.plataforma import settings
from asistente.plataforma.api.routers import navegador as navegador_router
from asistente.plataforma.api.sse import leer_eventos


@pytest.fixture
def gestor() -> GestorTareas:
    return GestorTareas()


@pytest.fixture
def cliente(tmp_path, monkeypatch: pytest.MonkeyPatch, gestor: GestorTareas) -> TestClient:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", True)
    monkeypatch.setattr(navegador_router, "INTERVALO_FLUJO_SEGUNDOS", 0.05)
    db = str(tmp_path / "estado.sqlite3")
    app = FastAPI()
    app.include_router(
        navegador_router.build_router(
            get_case_store=lambda: SQLiteCaseStateStore(db),
            get_accion_store=lambda: AccionStore(db),
            get_gestor=lambda: gestor,
            get_reparador=lambda: None,
        )
    )
    return TestClient(app)


def _tarea(gestor: GestorTareas, estado: str) -> Tarea:
    tarea = Tarea(
        id="t1",
        case_id="c1",
        clave="datadis",
        version=1,
        titulo="Descargar consumos",
        parametros={},
        personales=frozenset(),
    )
    tarea.estado = estado  # type: ignore[assignment]
    gestor._tareas[tarea.id] = tarea
    return tarea


def _eventos(cliente: TestClient, ruta: str, **cabeceras: str) -> list[dict[str, Any]]:
    respuesta = cliente.get(ruta, headers=cabeceras)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    return leer_eventos(respuesta.text)


def test_the_task_stream_sends_each_change_and_ends_when_the_task_stops(
    cliente: TestClient, gestor: GestorTareas
) -> None:
    tarea = _tarea(gestor, "trabajando")

    def avanzar() -> None:
        tarea.anotar("paso", "Abriendo la página de consumos")
        threading.Event().wait(0.2)
        with tarea.lock:
            tarea.estado = "descargado"

    hilo = threading.Timer(0.2, avanzar)
    hilo.start()
    try:
        eventos = _eventos(cliente, "/navegador/tareas/t1/eventos")
    finally:
        hilo.join()
    tareas = [e for e in eventos if e["event"] == "tarea"]
    assert tareas[0]["data"]["estado"] == "trabajando" and tareas[-1]["data"]["estado"] == "descargado"
    assert any(e["data"]["eventos"] for e in tareas)
    assert eventos[-1]["event"] == "fin" and eventos[-1]["data"] == {"motivo": "terminada"}

    # A reconnection with the last id it saw gets nothing new; a task that is not there is a 404.
    assert [
        e["event"] for e in _eventos(cliente, "/navegador/tareas/t1/eventos", **{"Last-Event-ID": tareas[-1]["id"]})
    ] == ["fin"]
    assert cliente.get("/navegador/tareas/nada/eventos").status_code == 404


def test_a_task_discarded_while_followed_ends_its_stream(cliente: TestClient, gestor: GestorTareas) -> None:
    _tarea(gestor, "esperando_login")
    hilo = threading.Timer(0.2, lambda: gestor._tareas.pop("t1"))
    hilo.start()
    try:
        eventos = _eventos(cliente, "/navegador/tareas/t1/eventos")
    finally:
        hilo.join()
    assert [e["event"] for e in eventos] == ["tarea", "fin"]
    assert eventos[-1]["data"] == {"motivo": "no_encontrada"}


def test_the_stream_answers_503_while_the_browser_agent_is_off(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", False)
    assert cliente.get("/navegador/tareas/t1/eventos").status_code == 503

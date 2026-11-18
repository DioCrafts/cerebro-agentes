"""``GET /cases/{case_id}/repaso``: the structured digest the inbox card renders, with each proposal's current
state; somebody else's case reads like one that does not exist."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.repaso import servicio
from asistente.plataforma.api.routers.repaso import build_router
from asistente.plataforma.auth.deps import CaseAccess, get_case_access
from tests.proactive import make_case
from .conftest import HOY, Guionizado, accion, aviso, valida


@pytest.fixture
def cliente(db: str) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(get_case_store=lambda: SQLiteCaseStateStore(db)))
    return TestClient(app)


def test_the_card_gets_points_reasons_sources_and_live_proposals(db, store, acciones, cliente) -> None:
    aviso(store, "modulo:luz:ahorro:oferta", title="Podrías ahorrar con otra tarifa", amount=Decimal("142.30"))
    aviso(store, "plazo:130", kind="plazo", title="Modelo 130", due_date=date(2026, 11, 20))
    pendiente = accion(acciones)
    resultado = servicio.repasar(make_case("c1"), hoy=HOY, db_path=db, store=store, proveedor=Guionizado(valida))

    respuesta = cliente.get(f"/cases/c1/repaso?notification_id={resultado.notification_id}")

    assert respuesta.status_code == 200, respuesta.text
    repaso = respuesta.json()["repaso"]
    assert repaso["periodo"] == "2026-W47" and repaso["origen"] == "modelo"
    assert repaso["titular"] == "Esta semana hay cosas que conviene mirar con calma."
    puntos = repaso["puntos"]
    assert [p["rango"] for p in puntos] == [1, 2, 3] and puntos[0]["titulo"] == "Modelo 130"
    assert puntos[0]["urgente"] and puntos[0]["motivos"][0] == "Vence el 20/11/2026 (en 2 días)"
    assert puntos[0]["fuentes"][0]["nombre_modulo"] == "Impuestos" and puntos[0]["fuentes"][0]["notification_id"]
    propuestas = {p["tipo"]: p for punto in puntos for p in punto["propuestas"]}
    assert propuestas["accion"]["ref"] == pendiente.id and propuestas["accion"]["estado"] == "pendiente"
    assert propuestas["plantilla"]["ref"] == "cambiar_tarifa_luz" and propuestas["plantilla"]["estado"] == "pendiente"

    # Decided elsewhere since: the card shows it as decided (its endpoint would answer 409 anyway).
    acciones.transicion(pendiente.id, desde="pendiente", hacia="rechazada")
    repaso = cliente.get("/cases/c1/repaso").json()["repaso"]
    estados = {p["tipo"]: p["estado"] for punto in repaso["puntos"] for p in punto["propuestas"]}
    assert estados["accion"] == "decidida" and estados["plantilla"] == "pendiente"


def test_no_pass_unknown_notification_and_unknown_case_are_404(db, store, cliente) -> None:
    assert cliente.get("/cases/c1/repaso").status_code == 404
    aviso(store, "plazo:130", kind="plazo", title="Modelo 130", due_date=date(2026, 11, 20))
    servicio.repasar(make_case("c1"), hoy=HOY, db_path=db, store=store, proveedor=None)
    assert cliente.get("/cases/c1/repaso?notification_id=otra").status_code == 404
    assert cliente.get("/cases/nadie/repaso").status_code == 404


def test_somebody_elses_case_reads_like_a_missing_one(db, store, cliente) -> None:
    aviso(store, "plazo:130", kind="plazo", title="Modelo 130", due_date=date(2026, 11, 20))
    servicio.repasar(make_case("c1"), hoy=HOY, db_path=db, store=store, proveedor=None)
    ajeno = CaseAccess(user=SimpleNamespace(id="otra"), store=SimpleNamespace(owns_case=lambda case_id, user_id: False))  # type: ignore[arg-type]
    cliente.app.dependency_overrides[get_case_access] = lambda: ajeno  # type: ignore[attr-defined]
    respuesta = cliente.get("/cases/c1/repaso")
    assert respuesta.status_code == 404 and respuesta.json()["detail"] == "case not found"

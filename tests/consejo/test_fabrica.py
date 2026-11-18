"""The production wiring (``FabricaPorDefecto``) with scripted providers in place of the real ones: every call goes
through ``MeteredProvider`` with the session's own store and budget, so the session's cost is exactly what the usage
log recorded for it, and the cap the user accepted stops the session before the call that would exceed it."""

from __future__ import annotations

from decimal import Decimal
import sqlite3
from typing import Any

import pytest

from asistente.nucleo.consejo import servicio as servicio_mod
from asistente.nucleo.consejo.servicio import ServicioConsejo
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings
from tests.consejo.motor.fakes import ProveedorConsejo, por_esquema
from .fakes import HOY, respuesta_planteamiento

PREGUNTA = "¿Me conviene amortizar la hipoteca o invertir en un fondo indexado?"


def _responder(peticion: dict[str, Any]) -> dict[str, Any]:
    if peticion["schema_name"] == "planteamiento":
        return respuesta_planteamiento()
    return por_esquema({})(peticion)


@pytest.fixture
def proveedores(monkeypatch: pytest.MonkeyPatch) -> list[ProveedorConsejo]:
    """``create_provider`` hands out scripted council providers that report usage priced at Sonnet's rates."""
    creados: list[ProveedorConsejo] = []

    def crear(name: str | None = None, *, model: str | None = None, effort: str | None = None) -> ProveedorConsejo:
        proveedor = ProveedorConsejo([], por_defecto=_responder, uso=(20_000, 2_000), name="anthropic", model="m")
        creados.append(proveedor)
        return proveedor

    monkeypatch.setattr("asistente.nucleo.agent.providers.create_provider", crear)
    monkeypatch.setattr(servicio_mod, "_buscador_del_corpus", lambda: (_ for _ in ()).throw(RuntimeError("sin índice")))
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "LLM_DAILY_BUDGET_USD", "")
    monkeypatch.setattr(settings, "LLM_DAILY_BUDGET_USD_PER_USER", "")
    monkeypatch.setattr(settings, "MEMORIA_CONVERSACIONES", False)
    return creados


def _sesion(db: str) -> tuple[ServicioConsejo, str]:
    servicio = ServicioConsejo(db, en_segundo_plano=False, hoy=lambda: HOY)
    case = SQLiteCaseStateStore(db).get_case("c1")
    assert case is not None
    sesion = servicio.convocar(case=case, pregunta=PREGUNTA)
    servicio.confirmar(sesion.id, case_id="c1")
    return servicio, sesion.id


def _registrado(db: str) -> tuple[int, Decimal]:
    conn = sqlite3.connect(db)
    try:
        filas = conn.execute("SELECT cost_usd FROM llm_usage WHERE case_id = 'c1'").fetchall()
    finally:
        conn.close()
    return len(filas), sum((Decimal(str(f[0])) for f in filas if f[0] is not None), Decimal(0))


def test_the_session_cost_is_exactly_what_the_usage_log_recorded(db: str, proveedores: list[Any]) -> None:
    servicio, sesion_id = _sesion(db)
    servicio.ejecutar_pendientes()
    final = servicio.obtener(sesion_id, case_id="c1").sesion
    assert final.estado == "terminada", final.error
    assert proveedores  # the configured provider was built through the factory
    llamadas, usd = _registrado(db)
    assert llamadas > 0 and final.coste.llamadas == llamadas
    assert final.coste.usd == usd.quantize(Decimal("0.00000001")) or abs(final.coste.usd - usd) < Decimal("0.000001")
    assert final.coste.usd <= final.tope_usd
    # Every council call was metered under its own purpose.
    conn = sqlite3.connect(db)
    try:
        propositos = {f[0] for f in conn.execute("SELECT DISTINCT purpose FROM llm_usage WHERE case_id = 'c1'")}
    finally:
        conn.close()
    assert "consejo:expediente" in propositos and "consejo:presidente" in propositos
    assert any(p.startswith("consejo:miembro:") for p in propositos)
    assert NotificationStore(db).list("c1")[0].title == "Dictamen del consejo listo"


def test_the_cap_the_user_accepted_stops_the_session(
    db: str, proveedores: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each call now costs more than the whole cap: the first one is allowed (nothing spent yet), the next is refused.
    def crear(name: str | None = None, *, model: str | None = None, effort: str | None = None) -> ProveedorConsejo:
        return ProveedorConsejo([], por_defecto=_responder, uso=(5_000_000, 0), name="anthropic", model="m")

    monkeypatch.setattr("asistente.nucleo.agent.providers.create_provider", crear)
    servicio, sesion_id = _sesion(db)
    servicio.ejecutar_pendientes()
    final = servicio.obtener(sesion_id, case_id="c1").sesion
    assert final.estado == "fallida"
    assert final.coste.usd > final.tope_usd and final.coste.llamadas == 1
    assert "tope de coste" in (final.error or "") or "tope" in (final.error or "")
    assert final.dictamen is None
    aviso = NotificationStore(db).list("c1")[0]
    assert aviso.title == "El consejo no ha podido terminar" and aviso.severity == "info"

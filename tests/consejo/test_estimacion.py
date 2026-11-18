"""The cost estimate shown before confirming: from the price table and the routing, in Decimal; the cap; the time."""

from __future__ import annotations

from decimal import Decimal

import pytest

from asistente.nucleo.consejo.estimacion import (
    estimar,
    estimar_coste,
    formatear_usd,
    precio_de,
    tokens_aprox,
)
from asistente.plataforma import settings


@pytest.fixture(autouse=True)
def precios_conocidos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "")
    for nivel in ("SMALL", "MEDIUM", "LARGE"):
        monkeypatch.setattr(settings, f"LLM_MODEL_{nivel}", "")
    monkeypatch.setattr(settings, "CONSEJO_FACTOR_TOPE", "2.0")
    monkeypatch.setattr(settings, "CONSEJO_TOPE_MAX_USD", "3.00")
    monkeypatch.setattr(settings, "CONSEJO_FAMILIAS", "")


def test_the_estimate_adds_up_the_phases_in_decimal() -> None:
    e = estimar(4, 3000, especialistas=["hipoteca", "fiscal"])
    assert isinstance(e.usd, Decimal) and e.usd > 0
    assert set(e.desglose) == {"instruccion", "propuestas", "criticas", "revision", "sintesis", "puertas"}
    assert e.usd >= sum(e.desglose.values()) - Decimal("0.01")
    assert e.precios_conocidos and not e.excede_maximo
    assert e.tope_usd == min(e.usd * 2, Decimal("3.00"))
    assert e.minutos >= 1 and e.llamadas > 4 * 3


def test_more_members_rounds_or_specialists_cost_more() -> None:
    base = estimar(3, 3000)
    assert estimar(5, 3000).usd > base.usd
    assert estimar(3, 9000).usd > base.usd
    assert estimar(3, 3000, rondas=1).usd < base.usd
    assert estimar(3, 3000, especialistas=["fiscal"]).usd > base.usd
    assert estimar_coste(3, 3000) == (base.usd, base.tope_usd)


def test_the_cap_has_an_absolute_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONSEJO_TOPE_MAX_USD", "0.50")
    e = estimar(7, 20000, especialistas=["hipoteca", "fiscal"])
    assert e.excede_maximo and e.tope_usd == Decimal("0.50")


def test_a_cheaper_routing_profile_lowers_the_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    caro = estimar(4, 3000).usd
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    assert precio_de("consejo:expediente").modelo == "claude-haiku-5-5"
    assert precio_de("consejo:miembro:prudente").modelo == "claude-sonnet-5-5"
    assert precio_de("consejo:presidente").modelo == "claude-opus-5-5"
    assert estimar(4, 3000).usd < caro


def test_an_unknown_model_is_priced_as_the_most_expensive_known(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "modelo-sin-precio")
    from asistente.plataforma.ops.consumo import cargar_precios

    tabla = cargar_precios().modelos.values()
    precio = precio_de("consejo:presidente")
    assert not precio.conocido
    assert precio.entrada == max(p.input for p in tabla if p.input is not None)
    assert precio.salida == max(p.output for p in tabla if p.output is not None)
    assert not estimar(3, 3000).precios_conocidos


def test_a_local_model_costs_nothing() -> None:
    e = estimar(3, 3000, familias_miembros=["local"])
    assert precio_de("consejo:miembro:x", "local").entrada == 0
    assert e.desglose["propuestas"] == 0


def test_helpers() -> None:
    assert tokens_aprox("") == 0 and tokens_aprox("abcd" * 10) == 10
    assert formatear_usd(Decimal("0.4")) == "0,40 USD"
    assert formatear_usd(Decimal("1.234")) == "1,24 USD"

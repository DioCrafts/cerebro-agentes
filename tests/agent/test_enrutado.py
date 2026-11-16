"""Routing by purpose: the table, the defaults (identical to before), the profile, overrides and escalation.

Real SDK over a fake HTTP transport where the request bodies matter; no network, no key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from asistente.nucleo.agent import enrutado
from asistente.nucleo.agent.providers.anthropic_provider import AnthropicProvider
from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.agent.providers.openai_provider import OpenAIProvider
from asistente.plataforma import settings
from asistente.plataforma.ops import consumo
from asistente.plataforma.ops.consumo import ConsumoStore, MeteredProvider, cargar_precios
from tests.agent.fakes import ScriptedProvider
from tests.agent.fakes_http import FakeAnthropicAPI, FakeOpenAIAPI

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
AJUSTES = (
    "LLM_ROUTING_PROFILE",
    "LLM_MODEL_SMALL",
    "LLM_MODEL_MEDIUM",
    "LLM_MODEL_LARGE",
    "LLM_EFFORT_SMALL",
    "LLM_EFFORT_MEDIUM",
    "LLM_EFFORT_LARGE",
    "LLM_ROUTES",
    *enrutado.AJUSTES_PREVIOS.values(),
)


@pytest.fixture(autouse=True)
def sin_enrutado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every routing setting at its default, whatever the environment of the test run says."""
    for nombre in AJUSTES:
        monkeypatch.setattr(settings, nombre, "")
    monkeypatch.setattr(settings, "LLM_ESCALATION", True)
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5-5")
    for variable in list(os.environ):
        if variable.startswith("LLM_ROUTE_"):
            monkeypatch.delenv(variable)


@pytest.fixture
def store(tmp_path: Path) -> ConsumoStore:
    return ConsumoStore(str(tmp_path / "state.sqlite3"), precios=cargar_precios(consumo.PRECIOS_PATH))


def _filas(store: ConsumoStore) -> list[dict[str, Any]]:
    return [dict(row) for row in store._conn.execute("SELECT * FROM llm_usage ORDER BY id")]


PROPOSITOS = [
    *(fila.patron.replace("*", "x") for fila in enrutado.TABLA),
    "especialista:luz",
    "algo-nuevo",
]


# --------------------------------------------------------------------------- #
# The table and the defaults
# --------------------------------------------------------------------------- #


def test_the_table_maps_purposes_to_tiers() -> None:
    assert enrutado.nivel_de_tabla("equipo:planificador") == "pequeno"
    assert enrutado.nivel_de_tabla("hechos") == "pequeno"
    assert enrutado.nivel_de_tabla("ayudas:clasificador") == "pequeno"
    assert enrutado.nivel_de_tabla("especialista:hipoteca") == "mediano"
    assert enrutado.nivel_de_tabla("equipo:investigador") == "mediano"
    assert enrutado.nivel_de_tabla("recodificacion") == "mediano"
    for grande in ("agente", "verificador", "equipo:critico", "recodificacion-revisor", "formatos-revisor"):
        assert enrutado.nivel_de_tabla(grande) == "grande"
    # A purpose nobody classified gets the strongest tier, never a weaker one.
    assert enrutado.nivel_de_tabla("algo-nuevo") == "grande"


@pytest.mark.parametrize("proposito", PROPOSITOS)
def test_without_routing_settings_every_purpose_keeps_the_providers_model(proposito: str, store) -> None:
    provider = AnthropicProvider(model="claude-opus-5-5", client=object())
    ruta = enrutado.resolver(proposito, proveedor="anthropic")
    assert (ruta.modelo, ruta.effort, ruta.origen) == (None, None, "por_defecto")
    medido = MeteredProvider(provider, store, purpose="agente").con_proposito(proposito)
    # The very same provider object: same model, same effort, same client, same requests.
    assert medido.wrapped is provider and medido.model == "claude-opus-5-5" and medido.escalado() is None


def test_without_routing_settings_the_requests_are_byte_identical_to_the_unrouted_provider(store) -> None:
    directo, medido = FakeAnthropicAPI(lambda p: '{"a": 1}'), FakeAnthropicAPI(lambda p: '{"a": 1}')
    sistema = "Instrucciones. " * 300
    AnthropicProvider(model="claude-opus-5-5", client=directo.client()).complete_json(
        system=sistema, user="u", schema=SCHEMA, schema_name="x"
    )
    MeteredProvider(
        AnthropicProvider(model="claude-opus-5-5", client=medido.client()), store, purpose="equipo:planificador"
    ).complete_json(system=sistema, user="u", schema=SCHEMA, schema_name="x")
    assert directo.peticiones == medido.peticiones
    assert _filas(store)[0]["model"] == "claude-opus-5-5" and _filas(store)[0]["tier"] == "pequeno"


def test_effective_table_lists_every_purpose() -> None:
    filas = enrutado.describir("anthropic", "claude-opus-5-5")
    assert {fila["modelo"] for fila in filas} == {"claude-opus-5-5"}
    assert [fila["proposito"] for fila in filas] == [fila.patron for fila in enrutado.TABLA]


# --------------------------------------------------------------------------- #
# The recommended profile and the overrides
# --------------------------------------------------------------------------- #


def test_recommended_profile_moves_small_and_medium_purposes_to_cheaper_claude_models(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    planificador = enrutado.resolver("equipo:planificador", proveedor="anthropic")
    assert (planificador.nivel, planificador.modelo, planificador.effort) == ("pequeno", "claude-haiku-5-5", "low")
    especialista = enrutado.resolver("especialista:luz", proveedor="anthropic")
    assert (especialista.modelo, especialista.effort) == ("claude-sonnet-5-5", None)
    assert enrutado.resolver("agente", proveedor="anthropic").modelo == "claude-opus-5-5"
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-fable-5-2")
    assert enrutado.resolver("verificador", proveedor="claude").modelo == "claude-fable-5-2"
    # The profile only names Claude models: other providers keep theirs.
    assert enrutado.resolver("equipo:planificador", proveedor="openai").modelo is None


def test_routed_purpose_sends_the_routed_model_and_meters_it_at_its_price(monkeypatch, store) -> None:
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    api = FakeAnthropicAPI(lambda p: '{"a": 1}')
    base = AnthropicProvider(model="claude-opus-5-5", client=api.client())
    agente = MeteredProvider(base, store, purpose="agente")
    planificador = agente.con_proposito("equipo:planificador")
    especialista = agente.con_proposito("especialista:luz")

    planificador.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")
    especialista.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")
    agente.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")

    haiku, sonnet, opus = api.cuerpos()
    assert haiku["model"] == "claude-haiku-5-5" and haiku["output_config"]["effort"] == "low"
    assert "fallbacks" not in haiku  # Haiku has no server-side refusal fallback
    assert sonnet["model"] == "claude-sonnet-5-5" and sonnet["output_config"]["effort"] == "high"
    assert opus["model"] == "claude-opus-5-5"
    # Siblings share the HTTP client and are cached: a purpose always gets the same provider object.
    assert agente.con_proposito("equipo:planificador").wrapped is planificador.wrapped
    assert planificador.wrapped._ensure_client() is base._ensure_client()
    filas = _filas(store)
    assert [(f["purpose"], f["model"], f["tier"]) for f in filas] == [
        ("equipo:planificador", "claude-haiku-5-5", "pequeno"),
        ("especialista:luz", "claude-sonnet-5-5", "mediano"),
        ("agente", "claude-opus-5-5", "grande"),
    ]
    # 1000 input + 100 output tokens at each model's price.
    assert [f["cost_usd"] for f in filas] == pytest.approx([0.00015, 0.003, 0.006])


def test_a_purpose_specific_setting_that_already_existed_wins_over_the_profile(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    monkeypatch.setattr(settings, "RECODE_MODEL", "claude-opus-5-5")
    ruta = enrutado.resolver("recodificacion", proveedor="anthropic")
    assert (ruta.modelo, ruta.origen) == (None, "RECODE_MODEL")  # the provider built with RECODE_MODEL is kept
    # An explicit route for the purpose still wins over that.
    monkeypatch.setenv("LLM_ROUTE_RECODIFICACION", "claude-sonnet-5-5")
    assert enrutado.resolver("recodificacion", proveedor="anthropic").modelo == "claude-sonnet-5-5"


def test_overrides_by_environment_json_and_tier(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL_SMALL", "claude-haiku-5-5")
    assert enrutado.resolver("hechos", proveedor="anthropic").modelo == "claude-haiku-5-5"
    assert enrutado.resolver("agente", proveedor="anthropic").modelo is None

    monkeypatch.setattr(settings, "LLM_ROUTES", json.dumps({"especialista:*": "pequeno", "verificador": "mediano"}))
    especialista = enrutado.resolver("especialista:luz", proveedor="anthropic")
    assert (especialista.nivel, especialista.modelo, especialista.origen) == (
        "pequeno",
        "claude-haiku-5-5",
        "LLM_ROUTES",
    )
    monkeypatch.setenv("LLM_ROUTE_ESPECIALISTA_LUZ", "claude-sonnet-5-5")
    luz = enrutado.resolver("especialista:luz", proveedor="anthropic")
    assert (luz.modelo, luz.origen) == ("claude-sonnet-5-5", "LLM_ROUTE_ESPECIALISTA_LUZ")
    assert enrutado.resolver("especialista:agua", proveedor="anthropic").origen == "LLM_ROUTES"


def test_invalid_routing_settings_are_refused(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "barato")
    with pytest.raises(ValueError, match="LLM_ROUTING_PROFILE"):
        enrutado.resolver("agente", proveedor="anthropic")
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "")
    monkeypatch.setattr(settings, "LLM_ROUTES", "{no es json")
    with pytest.raises(ValueError, match="LLM_ROUTES"):
        enrutado.resolver("agente", proveedor="anthropic")


def test_openai_routes_through_its_own_sibling_and_providers_without_variants_stay_put(monkeypatch, store) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL_SMALL", "gpt-mini-x")
    api = FakeOpenAIAPI('{"a": 1}')
    base = OpenAIProvider(model="gpt-grande", client=api.client())
    MeteredProvider(base, store, purpose="hechos").complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")
    assert api.cuerpos[0]["model"] == "gpt-mini-x"

    guion = ScriptedProvider([], [{"a": 1}])
    medido = MeteredProvider(guion, store, purpose="hechos")
    assert medido.wrapped is guion and medido.complete_json(system="S", user="u", schema={}, schema_name="x")


def test_team_roles_get_their_own_purposes() -> None:
    guion = ScriptedProvider([])
    assert enrutado.para(guion, "equipo:critico") is guion  # unmetered: as is

    class Medido:
        def con_proposito(self, proposito: str) -> str:
            return f"medido:{proposito}"

    assert enrutado.para(Medido(), "equipo:critico") == "medido:equipo:critico"


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #


def _escalable(monkeypatch, store, respuestas: list[str]) -> tuple[MeteredProvider, FakeAnthropicAPI]:
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    cola = list(respuestas)
    api = FakeAnthropicAPI(lambda p: cola.pop(0))
    base = AnthropicProvider(model="claude-opus-5-5", client=api.client())
    return MeteredProvider(base, store, purpose="hechos"), api


def test_an_unusable_output_of_the_small_model_is_retried_once_on_the_next_tier(monkeypatch, store) -> None:
    hechos, api = _escalable(monkeypatch, store, ["esto no es json", '{"a": 2}'])
    assert hechos.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x") == {"a": 2}
    assert [c["model"] for c in api.cuerpos()] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    filas = _filas(store)
    assert [(f["model"], f["tier"], f["escalated"]) for f in filas] == [
        ("claude-haiku-5-5", "pequeno", 0),
        ("claude-sonnet-5-5", "mediano", 1),
    ]
    assert all(f["purpose"] == "hechos" and f["cost_usd"] is not None for f in filas)


def test_an_output_that_breaks_the_schema_or_the_callers_check_is_escalated(monkeypatch, store) -> None:
    hechos, api = _escalable(monkeypatch, store, ['{"a": "dos"}', '{"a": 2}'])
    assert hechos.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x") == {"a": 2}

    hechos, api = _escalable(monkeypatch, store, ['{"a": 1}', '{"a": 2}'])
    par = hechos.complete_json(
        system="S", user="u", schema=SCHEMA, schema_name="x", validar=lambda d: None if d["a"] % 2 == 0 else "impar"
    )
    assert par == {"a": 2} and len(api.cuerpos()) == 2


def test_escalation_happens_once_and_then_fails_as_before(monkeypatch, store) -> None:
    hechos, api = _escalable(monkeypatch, store, ["mal", "peor"])
    with pytest.raises(ProviderError, match="JSON no válido"):
        hechos.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")
    assert [c["model"] for c in api.cuerpos()] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    # A wrong-but-valid answer of the escalated call is returned as is: the caller's gate decides, as before.
    hechos, api = _escalable(monkeypatch, store, ['{"a": "x"}', '{"a": "y"}'])
    assert hechos.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x") == {"a": "y"}


def test_no_escalation_without_another_model_above_or_when_switched_off(monkeypatch, store) -> None:
    api = FakeAnthropicAPI(lambda p: "mal")
    sin_perfil = MeteredProvider(
        AnthropicProvider(model="claude-opus-5-5", client=api.client()), store, purpose="hechos"
    )
    with pytest.raises(ProviderError):
        sin_perfil.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")
    assert len(api.cuerpos()) == 1  # no retry on the same model: exactly the old behaviour

    hechos, api = _escalable(monkeypatch, store, ["mal", '{"a": 1}'])
    monkeypatch.setattr(settings, "LLM_ESCALATION", False)
    with pytest.raises(ProviderError):
        hechos.complete_json(system="S", user="u", schema=SCHEMA, schema_name="x")
    assert len(api.cuerpos()) == 1


def test_the_next_tier_skips_tiers_that_run_the_same_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL_SMALL", "m-pequeno")
    monkeypatch.setattr(settings, "LLM_MODEL_MEDIUM", "m-pequeno")
    ruta = enrutado.resolver("hechos", proveedor="anthropic")
    siguiente = enrutado.siguiente(ruta, proveedor="anthropic", modelo_base="m-grande")
    assert siguiente is not None and (siguiente.nivel, siguiente.modelo) == ("grande", "m-grande")
    grande = enrutado.resolver("agente", proveedor="anthropic")
    assert enrutado.siguiente(grande, proveedor="anthropic", modelo_base="m-grande") is None


def test_the_aid_calls_classifier_purpose_is_routed_to_the_small_tier() -> None:
    from asistente.ambitos.ayudas.convocatorias import PROPOSITO_CLASIFICADOR

    assert PROPOSITO_CLASIFICADOR.startswith("ayudas:")
    assert enrutado.nivel_de_tabla(PROPOSITO_CLASIFICADOR) == "pequeno"
    # With no routing setting, it keeps the provider's model (nothing changes).
    assert enrutado.resolver(PROPOSITO_CLASIFICADOR, proveedor="anthropic").modelo is None

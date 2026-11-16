"""Prompt caching (where the breakpoints go, what each kind of cached token costs) and the usage store.

Real SDKs over fake HTTP transports: the assertions read the JSON bodies the APIs would receive.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
import sqlite3
from typing import Any

import pytest

from asistente.nucleo.agent.providers.anthropic_provider import AnthropicProvider, usage_from_response
from asistente.nucleo.agent.providers.base import ToolSpec, Usage
from asistente.nucleo.agent.providers.openai_provider import OpenAIProvider
from asistente.plataforma.ops import consumo
from asistente.plataforma.ops.consumo import ConsumoStore, calcular_coste, cargar_precios
from tests.agent.fakes_http import FakeAnthropicAPI, FakeOpenAIAPI, mensaje

LARGO = "Eres el asesor fiscal. " + "Instrucción estable del sistema. " * 120  # well over the cacheable minimum
TOOLS = [ToolSpec(name="buscar_fuentes", description="busca", input_schema={"type": "object", "properties": {}})]
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
TABLA = cargar_precios(consumo.PRECIOS_PATH)


def _api(texto: str = '{"a": 1}', **kw: Any) -> FakeAnthropicAPI:
    return FakeAnthropicAPI(lambda params: texto, **kw)


# --------------------------------------------------------------------------- #
# Breakpoint placement (Claude)
# --------------------------------------------------------------------------- #


def test_long_system_prompt_gets_an_explicit_breakpoint_and_the_tool_loop_keeps_automatic_caching() -> None:
    api = _api("hola")
    provider = AnthropicProvider(model="claude-haiku-5-5", client=api.client())
    provider.start(system=LARGO, tools=TOOLS, history=[], user_message="¿IVA?").step()

    (cuerpo,) = api.cuerpos()
    # The marker on the (only) system block caches tools + system: the stable prefix of every request.
    assert cuerpo["system"] == [{"type": "text", "text": LARGO, "cache_control": {"type": "ephemeral"}}]
    assert cuerpo["tools"][0]["name"] == "buscar_fuentes" and "cache_control" not in cuerpo["tools"][0]
    # The growing conversation tail is still cached automatically (two breakpoints of the four allowed).
    assert cuerpo["cache_control"] == {"type": "ephemeral"}
    assert cuerpo["messages"] == [{"role": "user", "content": "¿IVA?"}]


def test_short_system_prompt_is_sent_exactly_as_before() -> None:
    api = _api("hola")
    AnthropicProvider(model="claude-haiku-5-5", client=api.client()).start(
        system="S", tools=[], history=[], user_message="x"
    ).step()
    (cuerpo,) = api.cuerpos()
    assert cuerpo["system"] == "S" and cuerpo["cache_control"] == {"type": "ephemeral"}


def test_one_shot_json_caches_the_stable_system_and_not_the_unique_tail() -> None:
    api = _api()
    provider = AnthropicProvider(model="claude-haiku-5-5", client=api.client())
    assert provider.complete_json(system=LARGO, user="texto de la ley…", schema=SCHEMA, schema_name="x") == {"a": 1}

    (cuerpo,) = api.cuerpos()
    assert cuerpo["system"][0]["cache_control"] == {"type": "ephemeral"}
    # No automatic breakpoint: it would land after the unique user text and only add write cost.
    assert "cache_control" not in cuerpo
    assert cuerpo["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}}


def test_one_hour_ttl_marks_both_breakpoints_and_a_bad_ttl_is_refused() -> None:
    api = _api("hola")
    provider = AnthropicProvider(model="claude-haiku-5-5", client=api.client(), cache_ttl="1h")
    provider.start(system=LARGO, tools=[], history=[], user_message="x").step()
    (cuerpo,) = api.cuerpos()
    assert cuerpo["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert cuerpo["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    with pytest.raises(ValueError, match="LLM_CACHE_TTL"):
        AnthropicProvider(model="claude-haiku-5-5", cache_ttl="2h")


def test_usage_separates_cache_reads_writes_and_one_hour_writes() -> None:
    usage = {
        "input_tokens": 50,
        "output_tokens": 10,
        "cache_read_input_tokens": 9000,
        "cache_creation_input_tokens": 700,
        "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 500},
    }
    api = FakeAnthropicAPI(lambda params: mensaje('{"a": 1}', model=params["model"], usage=usage))
    provider = AnthropicProvider(model="claude-haiku-5-5", client=api.client())
    vistos: list[Usage] = []
    provider.on_usage = vistos.append
    provider.complete_json(system=LARGO, user="u", schema=SCHEMA, schema_name="x")
    assert vistos == [
        Usage(
            "anthropic",
            "claude-haiku-5-5",
            input_tokens=50,
            output_tokens=10,
            cache_read_tokens=9000,
            cache_write_tokens=700,
            cache_write_1h_tokens=500,
        )
    ]
    # A response without the breakdown counts every write at the 5-minute price.
    sin_desglose = type("R", (), {"usage": type("U", (), {"cache_creation_input_tokens": 7})(), "model": "m"})()
    assert usage_from_response(sin_desglose, provider="anthropic", model="m").cache_write_1h_tokens == 0


# --------------------------------------------------------------------------- #
# OpenAI: prompt_cache_key to OpenAI itself, nothing new to local servers
# --------------------------------------------------------------------------- #


def test_openai_requests_carry_a_stable_prompt_cache_key_and_local_servers_do_not() -> None:
    api = FakeOpenAIAPI()
    provider = OpenAIProvider(model="gpt-4o-mini", client=api.client())
    provider.complete_json(system=LARGO, user="uno", schema=SCHEMA, schema_name="x")
    provider.complete_json(system=LARGO, user="dos", schema=SCHEMA, schema_name="x")
    provider.start(system=LARGO, tools=TOOLS, history=[], user_message="tres").step()
    claves = [cuerpo["prompt_cache_key"] for cuerpo in api.cuerpos]
    assert claves[0] == claves[1] and claves[0].startswith("hgpt-") and len(claves[0]) <= 64
    assert claves[2] != claves[0]  # another stable prefix (with tools): another key

    local = FakeOpenAIAPI()
    OpenAIProvider(model="qwen", client=local.client(), name="local").complete_json(
        system=LARGO, user="u", schema=SCHEMA, schema_name="x"
    )
    assert "prompt_cache_key" not in local.cuerpos[0]


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #


def _u(model: str, **kw: Any) -> Usage:
    return Usage(provider="anthropic", model=model, **kw)


def test_cached_input_is_priced_separately_with_one_hour_writes_at_their_own_price() -> None:
    opus = TABLA.modelos["claude-opus-5-5"]
    usage = _u(
        "claude-opus-5-5",
        input_tokens=1_000_000,
        cache_read_tokens=10_000_000,
        cache_write_tokens=3_000_000,
        cache_write_1h_tokens=1_000_000,
    )
    esperado = opus.input + 10 * opus.cache_read + 2 * opus.cache_write + opus.cache_write_1h
    assert calcular_coste(usage, TABLA) == esperado == Decimal("4") + Decimal("2") + Decimal("10") + Decimal("8")
    # Reads are what caching saves: 10M read tokens cost what 0.5M uncached ones do on Opus 5.5.
    assert calcular_coste(_u("claude-opus-5-5", cache_read_tokens=10_000_000), TABLA) == Decimal("2.00")


def test_haiku_long_prompts_switch_the_whole_request_to_the_long_context_rate() -> None:
    corto = calcular_coste(_u("claude-haiku-5-5", input_tokens=100_000, output_tokens=1000), TABLA)
    largo = calcular_coste(_u("claude-haiku-5-5", input_tokens=100_001, output_tokens=1000), TABLA)
    assert corto == Decimal("0.0105")  # 0.1 × 0.1 + 0.001 × 0.5
    assert largo == Decimal("0.0525005")  # 0.100001 × 0.5 + 0.001 × 2.5
    # Cached tokens count towards the prompt length.
    assert calcular_coste(_u("claude-haiku-5-5", input_tokens=10, cache_read_tokens=100_000), TABLA) == (
        Decimal(10) * Decimal("0.50") + Decimal(100_000) * Decimal("0.05")
    ) / Decimal(1_000_000)


def test_batch_calls_cost_the_table_factor_and_need_one() -> None:
    normal = _u("claude-sonnet-5-5", input_tokens=2_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    lote = replace(normal, batch=True)
    assert calcular_coste(normal, TABLA) == Decimal("5.1")
    assert calcular_coste(lote, TABLA) == Decimal("2.55")
    sin_factor = consumo.TablaPrecios(
        consultado_el=date(2026, 1, 1),
        moneda="USD",
        unidad="por millón de tokens",
        nota="n",
        fuente="f",
        modelos={"m": consumo.Precio(input=Decimal(1))},
    )
    assert calcular_coste(Usage("x", "m", input_tokens=10, batch=True), sin_factor) is None


def test_bundled_table_prices_the_claude_5_family_from_its_source() -> None:
    sonnet, haiku, fable = (TABLA.modelos[m] for m in ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"))
    assert (sonnet.input, sonnet.output, sonnet.cache_read) == (Decimal("2.00"), Decimal("10.00"), Decimal("0.10"))
    assert (haiku.input, haiku.output) == (Decimal("0.10"), Decimal("0.50"))
    assert haiku.contexto_largo is not None and haiku.contexto_largo.desde_tokens == 100_000
    assert (fable.input, fable.output, fable.cache_read) == (Decimal("8.00"), Decimal("40.00"), Decimal("0.20"))
    for nombre, precio in TABLA.modelos.items():
        if precio.input is None:
            continue
        # Derived figures follow the stated rules, and each note says which figures are derived.
        assert precio.cache_write == precio.input * Decimal("1.25") and precio.cache_write_1h == precio.input * 2
        assert precio.factor_lote == Decimal("0.50") and "derivada" in precio.nota, nombre


# --------------------------------------------------------------------------- #
# Store: new columns on an existing database, and the new aggregates
# --------------------------------------------------------------------------- #


def test_an_existing_usage_table_is_migrated_and_the_report_shows_cache_share_batches_and_escalations(
    tmp_path,
) -> None:
    db = tmp_path / "state.sqlite3"
    viejo = sqlite3.connect(db)
    viejo.execute(
        "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id TEXT, case_id TEXT,"
        " purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER, output_tokens"
        " INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL)"
    )
    ahora = datetime.now(UTC)
    viejo.execute(
        "INSERT INTO llm_usage (ts, purpose, provider, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, cost_usd) VALUES (?, 'agente', 'anthropic', 'claude-opus-5-5', 100, 10, 0, 0, 0.0006)",
        (ahora.isoformat(timespec="microseconds"),),
    )
    viejo.commit()
    viejo.close()

    store = ConsumoStore(str(db), precios=TABLA)
    lote = _u(
        "claude-haiku-5-5",
        input_tokens=100,
        cache_read_tokens=800,
        cache_write_tokens=100,
        purpose="recodificacion",
        batch=True,
        tier="pequeno",
    )
    store.registrar(lote, ts=ahora)
    store.registrar(Usage("anthropic", "claude-sonnet-5-5", 10, 1, purpose="recodificacion", escalated=True), ts=ahora)

    hoy = consumo.hoy_madrid(ahora)
    resumen = store.resumen(desde=hoy, hasta=hoy)
    assert resumen.total.llamadas == 3 and resumen.total.llamadas_lote == 1 and resumen.total.llamadas_escaladas == 1
    recodificacion = next(a for a in resumen.por_proposito if a.clave == "recodificacion")
    assert recodificacion.proporcion_cache == Decimal("0.7921")  # 800 / (100 + 800 + 100 + 10)
    assert {a.clave for a in resumen.por_modelo} == {"claude-opus-5-5", "claude-haiku-5-5", "claude-sonnet-5-5"}
    filas = [dict(r) for r in store._conn.execute("SELECT * FROM llm_usage ORDER BY id")]
    assert (filas[0]["batch"], filas[0]["escalated"], filas[0]["tier"]) == (0, 0, None)  # the old row
    assert (filas[1]["batch"], filas[1]["tier"]) == (1, "pequeno")
    # Batch row priced at half: (100 × 0.10 + 800 × 0.01 + 100 × 0.125) / 1e6 × 0.5
    assert filas[1]["cost_usd"] == pytest.approx(0.00001525)

"""The bounded sub-agent loop (``team.run_subagent``): tools, last step, refusals, errors.

These scenarios used to compare the in-house loop with the OpenHands runtime step for step; ADR 0006 kept only the
in-house loop, and the scenarios stay as its behavioural contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from asistente.nucleo.agent import team
from asistente.nucleo.agent.providers.base import ModelTurn, ProviderError, ToolCall, ToolSpec
from asistente.nucleo.agent.tools import ToolOutcome
from .fakes import ScriptedProvider, text_turn, tool_turn

SPEC = ToolSpec(
    name="sumar",
    description="Suma dos enteros.",
    input_schema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
)


class Caja:
    """A toolbox with one tool that records every call it receives."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def specs(self) -> list[ToolSpec]:
        return [SPEC]

    def execute(self, call: ToolCall) -> ToolOutcome:
        self.calls.append(call)
        if call.name != "sumar":
            return ToolOutcome(content=f"Herramienta desconocida: {call.name}", summary="desconocida", is_error=True)
        if call.arguments_error:
            return ToolOutcome(content=call.arguments_error, summary="argumentos", is_error=True)
        total = call.arguments["a"] + call.arguments["b"]
        return ToolOutcome(content=f"suma={total}", summary=f"suma {total}")


def correr(runner: Any, turns: list[ModelTurn], *, max_steps: int = 4) -> tuple[Any, ScriptedProvider, Caja, list]:
    provider = ScriptedProvider(turns)
    caja = Caja()
    eventos: list[tuple[str, dict[str, Any]]] = []
    run = runner(
        provider,
        agent="prueba",
        system="Eres un especialista.",
        toolbox=caja,
        user_message="¿Cuánto es 2+3?",
        max_steps=max_steps,
        emit=lambda tipo, **datos: eventos.append((tipo, datos)),
    )
    return run, provider, caja, eventos


RUNNERS = [team.run_subagent]


def uno(turns: list[ModelTurn], **kw: Any) -> tuple[Any, ScriptedProvider, Caja, list]:
    return correr(team.run_subagent, [*turns], **kw)


def test_a_tool_then_a_memo_gives_the_memo() -> None:
    oh = uno([tool_turn(("sumar", {"a": 2, "b": 3})), text_turn("Son 5.")])
    assert oh[0].memo == "Son 5."
    assert oh[1].tool_results[0].content == "suma=5"


def test_several_calls_in_one_turn_run_in_the_models_order() -> None:
    turns = [tool_turn(("sumar", {"a": 1, "b": 1}), ("sumar", {"a": 2, "b": 2})), text_turn("Hecho.")]
    oh = uno(turns)
    assert [r.content for r in oh[1].tool_results] == ["suma=2", "suma=4"]


def test_tools_switch_off_on_the_last_step_and_late_calls_get_the_no_steps_error() -> None:
    turns = [tool_turn(("sumar", {"a": 1, "b": 1})), tool_turn(("sumar", {"a": 2, "b": 2}))]
    oh = uno(turns, max_steps=2)
    assert oh[1].allow_tools_log == [True, False]
    assert oh[0].memo == ""
    assert oh[1].tool_results[-1].is_error and "Sin más pasos" in oh[1].tool_results[-1].content
    assert len(oh[2].calls) == 1  # the late call never reached the toolbox


@pytest.mark.parametrize(
    "ultimo",
    [
        ModelTurn(text="", tool_calls=[], stop_reason="refusal"),
        ModelTurn(text="", tool_calls=[ToolCall(id="x", name="sumar", arguments={"a": 1})], stop_reason="max_tokens"),
    ],
)
def test_refusal_or_truncated_call_ends_without_memo_and_without_tools(ultimo: ModelTurn) -> None:
    oh = uno([ultimo, text_turn("no debería leerse")])
    assert oh[0].memo == "" and oh[2].calls == []


def test_an_empty_final_text_is_an_empty_memo_and_the_provider_is_not_asked_again() -> None:
    oh = uno([text_turn(""), text_turn("sobra")])
    assert oh[1].turns == [text_turn("sobra")]


def test_unknown_tool_and_bad_arguments_reach_the_toolbox_like_before() -> None:
    malo = ModelTurn(
        text="",
        tool_calls=[
            ToolCall(id="a", name="borrar_todo", arguments={}),
            ToolCall(id="b", name="sumar", arguments={}, arguments_error="JSON inválido"),
        ],
        stop_reason="tool_use",
    )
    oh = uno([malo, text_turn("Vale.")])
    assert [c.name for c in oh[2].calls] == ["borrar_todo", "sumar"]
    assert oh[2].calls[1].arguments_error == "JSON inválido"


class ProveedorQueFalla(ScriptedProvider):
    def start(self, **kw: Any) -> Any:
        raise ProviderError("sin red", retryable=True)


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_provider_error_degrades_to_an_empty_memo(runner: Any) -> None:
    run = runner(
        ProveedorQueFalla([]),
        agent="prueba",
        system="s",
        toolbox=Caja(),
        user_message="u",
        max_steps=3,
        emit=lambda *a, **k: None,
    )
    assert run.memo == "" and run.steps == 0


class ProveedorRoto(ScriptedProvider):
    def start(self, **kw: Any) -> Any:
        raise RuntimeError("bug")


@pytest.mark.parametrize("runner", RUNNERS)
def test_any_other_error_still_propagates(runner: Any) -> None:
    with pytest.raises(RuntimeError, match="bug"):
        runner(
            ProveedorRoto([]),
            agent="p",
            system="s",
            toolbox=Caja(),
            user_message="u",
            max_steps=3,
            emit=lambda *a, **k: None,
        )


def test_sin_indice_el_buscador_no_tumba_el_cerebro(tmp_path, monkeypatch):
    from asistente.nucleo.agent.search import FaissCorpusSearcher, SearchFilters, SinCorpus
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "FAISS_INDEX_PATH", str(tmp_path / "no-existe"))
    buscador = FaissCorpusSearcher.from_settings()
    assert isinstance(buscador, SinCorpus)
    assert buscador.search("deducción alquiler", k=5, filters=SearchFilters()) == []

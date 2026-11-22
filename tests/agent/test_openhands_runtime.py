"""The OpenHands runtime of the sub-agent loop: same behaviour as the in-house loop, step for step.

Every test runs the same scenario through both runtimes and compares the results; a few check what only the
OpenHands side can get wrong (unknown tools, the SDK's own scripted LLM, the dispatch flag).
"""

from __future__ import annotations

from typing import Any

import pytest

from asistente.nucleo.agent import openhands_runtime, team
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


RUNNERS = [team.run_subagent_propio, openhands_runtime.run_subagent]


def ambos(turns: list[ModelTurn], **kw: Any) -> list[tuple[Any, ScriptedProvider, Caja, list]]:
    return [correr(runner, [*turns], **kw) for runner in RUNNERS]


def comparable(resultado: tuple[Any, ScriptedProvider, Caja, list]) -> dict[str, Any]:
    run, provider, caja, eventos = resultado
    return {
        "memo": run.memo,
        "steps": run.steps,
        "trace": run.trace,
        "usage": run.usage,
        "tool_results": provider.tool_results,
        "user_messages": provider.user_messages,
        "allow_tools": provider.allow_tools_log,
        "started": {k: provider.started[k] for k in ("system", "tools", "user_message")},
        "calls": caja.calls,
        "eventos": eventos,
        "turnos_sobrantes": len(provider.turns),
    }


def test_a_tool_then_a_memo_is_identical_in_both_runtimes() -> None:
    propio, oh = ambos([tool_turn(("sumar", {"a": 2, "b": 3})), text_turn("Son 5.")])
    assert comparable(oh) == comparable(propio)
    assert oh[0].memo == "Son 5."
    assert oh[1].tool_results[0].content == "suma=5"


def test_several_calls_in_one_turn_run_in_the_models_order() -> None:
    turns = [tool_turn(("sumar", {"a": 1, "b": 1}), ("sumar", {"a": 2, "b": 2})), text_turn("Hecho.")]
    propio, oh = ambos(turns)
    assert comparable(oh) == comparable(propio)
    assert [r.content for r in oh[1].tool_results] == ["suma=2", "suma=4"]


def test_tools_switch_off_on_the_last_step_and_late_calls_get_the_no_steps_error() -> None:
    turns = [tool_turn(("sumar", {"a": 1, "b": 1})), tool_turn(("sumar", {"a": 2, "b": 2}))]
    propio, oh = ambos(turns, max_steps=2)
    assert comparable(oh) == comparable(propio)
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
    propio, oh = ambos([ultimo, text_turn("no debería leerse")])
    assert comparable(oh) == comparable(propio)
    assert oh[0].memo == "" and oh[2].calls == []


def test_an_empty_final_text_is_an_empty_memo_and_the_provider_is_not_asked_again() -> None:
    propio, oh = ambos([text_turn(""), text_turn("sobra")])
    assert comparable(oh) == comparable(propio)
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
    propio, oh = ambos([malo, text_turn("Vale.")])
    assert comparable(oh) == comparable(propio)
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


def test_the_sdks_own_scripted_llm_drives_our_tools() -> None:
    """Without our provider at all: the SDK's TestLLM calls a toolbox tool and the memo is its last text."""
    from openhands.sdk import Message, TextContent
    from openhands.sdk.llm import MessageToolCall
    from openhands.sdk.testing import TestLLM

    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[MessageToolCall(id="c1", name="sumar", arguments='{"a": 2, "b": 3}', origin="completion")],
            ),
            Message(role="assistant", content=[TextContent(text="Son 5.")]),
        ]
    )
    caja = Caja()
    run = openhands_runtime.run_subagent(
        ScriptedProvider([]),
        agent="prueba",
        system="s",
        toolbox=caja,
        user_message="2+3",
        max_steps=4,
        emit=lambda *a, **k: None,
        llm_factory=lambda _agent: llm,
    )
    assert [c.arguments for c in caja.calls] == [{"a": 2, "b": 3}]
    assert run.trace[0]["summary"] == "suma 5"


@pytest.mark.parametrize(("valor", "esperado"), [("openhands", "openhands"), ("propio", "propio"), ("", "openhands")])
def test_the_flag_picks_the_runtime(monkeypatch: pytest.MonkeyPatch, valor: str, esperado: str) -> None:
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "CEREBRO_RUNTIME", valor, raising=False)
    assert openhands_runtime.runtime_activo() == esperado
    llamados: list[str] = []
    monkeypatch.setattr(openhands_runtime, "run_subagent", lambda *a, **k: llamados.append("openhands"))
    monkeypatch.setattr(team, "run_subagent_propio", lambda *a, **k: llamados.append("propio"))
    team.run_subagent(
        ScriptedProvider([]), agent="a", system="s", toolbox=Caja(), user_message="u", max_steps=1, emit=print
    )
    assert llamados == [esperado]

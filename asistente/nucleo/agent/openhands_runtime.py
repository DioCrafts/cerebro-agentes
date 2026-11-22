"""The bounded agent loop of specialists, researchers and critic, run by the OpenHands SDK.

``team.run_subagent`` is the one loop every sub-agent of the brain goes through (the household specialists in
``especialistas.py`` and ``cerebro/colaboracion/modulos.py``, the team's researchers and its critic). With
``CEREBRO_RUNTIME=openhands`` (the default) that loop is an OpenHands ``Agent`` + ``Conversation``:

* the SDK owns the loop: turns, dispatching tool calls to tools, events, iteration cap;
* the existing tools stay exactly what they were: every ``ToolSpec`` of the sub-agent's toolbox becomes one SDK
  ``ToolDefinition`` (:class:`HerramientaCerebro`) whose executor calls ``toolbox.execute`` with the ORIGINAL
  ``ToolCall`` — so the ledger, the ``actua``/``prepara`` rules, ``pedir_dato`` and every module guard still apply;
* the model behind the agent is our own ``LLMProvider`` through :class:`LLMPuente`, an SDK ``LLM`` whose
  ``completion`` drives the provider's conversation. Metering (``medir``), per-question budgets, providers,
  batching and retries therefore stay ours and unchanged; OpenHands never calls a model API by itself.

Behaviour is the in-house loop's, step for step (``tests/agent/test_openhands_runtime.py`` checks it): tools are
switched off on the last step and any call then gets the «sin más pasos» error; a refusal or a tool call cut by
``max_tokens`` ends the run without a memo and without running the tools; a ``ProviderError`` yields an empty memo
(the team degrades instead of failing); the memo is the last text turn that asked for no tools.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import logging
import os
import tempfile
import threading
from typing import TYPE_CHECKING, Any
import uuid

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from openhands.sdk import (  # noqa: E402
    LLM,
    Action,
    Agent,
    Conversation,
    Message,
    Observation,
    TextContent,
    Tool,
    ToolDefinition,
    register_tool,
)
from openhands.sdk.llm import LLMResponse, MessageToolCall  # noqa: E402
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage  # noqa: E402
from openhands.sdk.tool import ToolExecutor  # noqa: E402
from pydantic import Field, PrivateAttr  # noqa: E402

from asistente.nucleo.agent.providers.base import (  # noqa: E402
    LLMProvider,
    ModelTurn,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSpec,
)

if TYPE_CHECKING:
    from asistente.nucleo.agent.team import Emit, SubagentRun, ToolRunner

logger = logging.getLogger(__name__)

SIN_PASOS = "Sin más pasos: entrega ya tu informe con lo que tienes."
_TOOLSET = "CerebroToolbox"
# What the SDK sees when a turn ends with no text (it needs a non-empty final message to stop); never the memo.
_FIN = "."
# The SDK-side name of a call to a tool the toolbox does not offer: it still reaches the toolbox, which rejects it.
_DESCONOCIDA = "herramienta_desconocida"


# --------------------------------------------------------------------------------------------------------------
# One run's state, shared by the bridge LLM and the tool executors
# --------------------------------------------------------------------------------------------------------------


class _Ejecucion:
    """What one sub-agent run knows: its toolbox, its step budget, the provider conversation and the outcome."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        agent: str,
        system: str,
        toolbox: ToolRunner,
        user_message: str,
        max_steps: int,
        emit: Emit,
        run: SubagentRun,
    ) -> None:
        self.provider = provider
        self.agent = agent
        self.system = system
        self.toolbox = toolbox
        self.specs = toolbox.specs()
        self.nombres = {spec.name for spec in self.specs}
        self.user_message = user_message
        self.max_steps = max_steps
        self.emit = emit
        self.run = run
        self.conversation: Any = None
        self.allow_tools = True
        self.cortado = False  # refusal / truncated tool call: no memo, tools never run
        self.error: BaseException | None = None
        self.llamadas: dict[str, ToolCall] = {}  # call id -> the provider's original call (arguments_error kept)
        self.orden: list[str] = []  # call ids of the last turn, in the model's order
        self.resultados: dict[str, ToolResult] = {}
        self.usuarios_vistos = 0

    # -- tools -------------------------------------------------------------------------------------------------

    def ejecutar(self, call_id: str, name: str, arguments: dict[str, Any]) -> ToolResult:
        call = self.llamadas.get(call_id) or ToolCall(id=call_id, name=name, arguments=arguments)
        if self.allow_tools:
            self.emit("tool_call", tool=call.name, arguments=call.arguments, agent=self.agent)
            outcome = self.toolbox.execute(call)
            content, summary, is_error = outcome.content, outcome.summary, outcome.is_error
        else:
            content = summary = SIN_PASOS
            is_error = True
        self.emit("tool_result", tool=call.name, summary=summary, is_error=is_error, agent=self.agent)
        self.run.trace.append(
            {
                "step": self.run.steps,
                "tool": call.name,
                "arguments": call.arguments,
                "summary": summary,
                "is_error": is_error,
                "agent": self.agent,
            }
        )
        result = ToolResult(call_id=call_id, content=content, is_error=is_error)
        self.resultados[call_id] = result
        return result

    # -- model -------------------------------------------------------------------------------------------------

    def entregar_resultados(self, messages: Sequence[Message] = ()) -> None:
        """Answer every call of the provider's last turn, in its order (also after the last step, as before)."""
        pendientes = list(self.orden)
        if not pendientes or self.conversation is None:
            return
        textos = {m.tool_call_id: _texto(m) for m in messages if m.role == "tool" and m.tool_call_id}
        self.conversation.add_tool_results(
            [
                self.resultados.get(cid)
                or ToolResult(call_id=cid, content=textos.get(cid, "Error en la herramienta."), is_error=True)
                for cid in pendientes
            ]
        )
        self.orden = []  # call ids may repeat across turns: each turn is answered once, then forgotten

    def siguiente_turno(self, messages: Sequence[Message]) -> ModelTurn | None:
        """Feed the provider what happened since its last turn, then ask it for the next one (None: stop)."""
        if self.cortado or self.run.steps >= self.max_steps:
            return None
        if self.conversation is None:
            self.conversation = self.provider.start(
                system=self.system, tools=self.specs, history=[], user_message=self.user_message
            )
            self.usuarios_vistos = sum(1 for m in messages if m.role == "user")
        else:
            self.entregar_resultados(messages)
            usuarios = [m for m in messages if m.role == "user"]
            for mensaje in usuarios[self.usuarios_vistos :]:
                self.conversation.add_user_message(_texto(mensaje))
            self.usuarios_vistos = len(usuarios)
        self.allow_tools = self.run.steps < self.max_steps - 1
        turn: ModelTurn = self.conversation.step(allow_tools=self.allow_tools)
        self.run.steps += 1
        for key, value in turn.usage.items():
            self.run.usage[key] = self.run.usage.get(key, 0) + value
        if turn.stop_reason == "refusal" or (turn.tool_calls and turn.stop_reason == "max_tokens"):
            self.cortado = True
            return None
        if not turn.tool_calls:
            self.run.memo = turn.text.strip()
            self.cortado = True  # the run is over: the SDK may ask again, the provider is never asked again
        self.llamadas = {call.id: call for call in turn.tool_calls}
        self.resultados = {}
        self.orden = [call.id for call in turn.tool_calls]
        return turn


def _texto(message: Message) -> str:
    return "\n".join(item.text for item in message.content if isinstance(item, TextContent))


# A run's state is looked up by id when the SDK resolves the toolset (the registry is global, runs are not).
_EJECUCIONES: dict[str, _Ejecucion] = {}
_LOCK = threading.Lock()


# --------------------------------------------------------------------------------------------------------------
# Tools: every ToolSpec of the toolbox as an SDK tool
# --------------------------------------------------------------------------------------------------------------


class AccionCerebro(Action):
    """The raw arguments of a call; the toolbox validates them, as it always has."""

    data: dict[str, Any] = Field(default_factory=dict, description="Arguments of the tool call")


class ObservacionCerebro(Observation):
    """What the toolbox returned (``Observation.content`` holds the text, ``is_error`` the flag)."""


class _Ejecutor(ToolExecutor[AccionCerebro, ObservacionCerebro]):
    def __init__(self, ejecucion: _Ejecucion, nombre: str) -> None:
        self._ejecucion = ejecucion
        self._nombre = nombre

    def __call__(self, action: AccionCerebro, conversation: Any = None) -> ObservacionCerebro:  # noqa: ARG002
        call_id = str(action.data.pop("__call_id__", "")) or f"oh_{uuid.uuid4().hex[:8]}"
        result = self._ejecucion.ejecutar(call_id, self._nombre, dict(action.data))
        return ObservacionCerebro.from_text(result.content, is_error=result.is_error)


class HerramientaCerebro(ToolDefinition[AccionCerebro, ObservacionCerebro]):
    """One ``ToolSpec`` of a sub-agent's toolbox (its name and JSON schema are the spec's, untouched)."""

    spec_name: str = ""
    spec_parameters: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.spec_name

    @classmethod
    def create(cls, conv_state: Any = None, **params: Any) -> Sequence[HerramientaCerebro]:  # noqa: ARG003
        ejecucion = _EJECUCIONES[params["ejecucion"]]
        return [
            cls(
                description=spec.description,
                action_type=AccionCerebro,
                observation_type=ObservacionCerebro,
                executor=_Ejecutor(ejecucion, spec.name),
                spec_name=spec.name,
                spec_parameters=dict(spec.input_schema),
            )
            for spec in [*ejecucion.specs, ToolSpec(_DESCONOCIDA, "", {"type": "object", "properties": {}})]
        ]

    def action_from_arguments(self, arguments: dict[str, Any]) -> AccionCerebro:
        return AccionCerebro(data=dict(arguments))

    def _get_tool_schema(self, add_security_risk_prediction: bool = False, action_type: Any = None) -> dict[str, Any]:  # noqa: ARG002
        return json.loads(json.dumps(self.spec_parameters))


register_tool(_TOOLSET, HerramientaCerebro)


# --------------------------------------------------------------------------------------------------------------
# The model: our provider behind an SDK LLM
# --------------------------------------------------------------------------------------------------------------


class LLMPuente(LLM):
    """An SDK ``LLM`` whose every completion is one ``step`` of our provider's conversation."""

    _ejecucion: _Ejecucion | None = PrivateAttr(default=None)

    def vincular(self, ejecucion: _Ejecucion) -> LLMPuente:
        self._ejecucion = ejecucion
        return self

    def uses_responses_api(self) -> bool:
        return False

    def completion(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,  # noqa: ARG002
        add_security_risk_prediction: bool = False,  # noqa: ARG002
        on_token: Any = None,  # noqa: ARG002
        call_context: Any = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> LLMResponse:
        ejecucion = self._ejecucion
        assert ejecucion is not None, "LLMPuente used without a run"
        try:
            turn = ejecucion.siguiente_turno(messages)
        except Exception as exc:
            ejecucion.error = exc
            ejecucion.cortado = True
            turn = None
        if turn is None:
            message = Message(role="assistant", content=[TextContent(text=_FIN)])
        else:
            message = Message(
                role="assistant",
                content=[TextContent(text=turn.text or ("" if turn.tool_calls else _FIN))],
                tool_calls=[
                    MessageToolCall(
                        id=call.id,
                        name=call.name if call.name in ejecucion.nombres else _DESCONOCIDA,
                        # The call id travels inside the arguments so the executor can find the original call.
                        arguments=json.dumps({**call.arguments, "__call_id__": call.id}, ensure_ascii=False),
                        origin="completion",
                    )
                    for call in turn.tool_calls
                ]
                or None,
            )
        return LLMResponse(message=message, metrics=self._metricas(), raw_response=_respuesta_cruda(self.model, message))

    def _metricas(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            model_name=self.model,
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=TokenUsage(model=self.model, prompt_tokens=0, completion_tokens=0),
        )


def _respuesta_cruda(model: str, message: Message) -> Any:
    from litellm.types.utils import Choices, ModelResponse
    from litellm.types.utils import Message as LiteLLMMessage

    datos: dict[str, Any] = {"role": "assistant", "content": _texto(message)}
    if message.tool_calls:
        datos["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in message.tool_calls
        ]
    return ModelResponse(
        id=f"cerebro-{uuid.uuid4().hex[:8]}",
        choices=[Choices(message=LiteLLMMessage(**datos), index=0, finish_reason="stop")],
        created=0,
        model=model,
        object="chat.completion",
    )


# --------------------------------------------------------------------------------------------------------------
# The entry point team.run_subagent dispatches to
# --------------------------------------------------------------------------------------------------------------


def run_subagent(
    provider: LLMProvider,
    *,
    agent: str,
    system: str,
    toolbox: ToolRunner,
    user_message: str,
    max_steps: int,
    emit: Emit,
    llm_factory: Callable[[str], LLM] | None = None,
) -> SubagentRun:
    """``team.run_subagent`` on the OpenHands SDK: same arguments, same :class:`SubagentRun`, same guarantees."""
    from asistente.nucleo.agent.team import SubagentRun

    run = SubagentRun(agent=agent)
    if max_steps <= 0:
        return run
    ejecucion = _Ejecucion(
        provider,
        agent=agent,
        system=system,
        toolbox=toolbox,
        user_message=user_message,
        max_steps=max_steps,
        emit=emit,
        run=run,
    )
    clave = uuid.uuid4().hex
    with _LOCK:
        _EJECUCIONES[clave] = ejecucion
    try:
        llm = (llm_factory or _llm_por_defecto)(agent)
        if isinstance(llm, LLMPuente):
            llm.vincular(ejecucion)
        oh_agent = Agent(
            llm=llm,
            tools=[Tool(name=_TOOLSET, params={"ejecucion": clave})],
            system_prompt=system,
            include_default_tools=[],  # no `finish`/`think`: a text turn without tools ends the run, as before
            tool_concurrency_limit=1,  # tools run in the model's order (ledger ids stay deterministic)
        )
        with tempfile.TemporaryDirectory(prefix="cerebro-oh-") as workspace:
            conversation = Conversation(
                agent=oh_agent,
                workspace=workspace,
                max_iteration_per_run=max_steps + 1,
                stuck_detection=False,
                visualizer=None,
            )
            try:
                conversation.send_message(user_message)
                conversation.run()
            except Exception as exc:  # the SDK wraps what the model raised; the run's own record decides below
                if ejecucion.error is None:
                    ejecucion.error = exc
            finally:
                conversation.close()
        if ejecucion.error is None:
            ejecucion.entregar_resultados()
    finally:
        with _LOCK:
            _EJECUCIONES.pop(clave, None)
    error = ejecucion.error
    if error is not None:
        if isinstance(error, ProviderError):
            logger.warning("subagent %s failed: %s", agent, error)
        elif isinstance(error, Exception) and not _es_cierre_esperado(error):
            raise error
    return run


def _es_cierre_esperado(error: Exception) -> bool:
    """The SDK stops a run at its iteration cap: that is our step budget doing its job, not a failure."""
    return "max_iteration" in str(error).lower() or "maximum iterations" in str(error).lower()


def _llm_por_defecto(agent: str) -> LLM:
    return LLMPuente(model="cerebro-puente", usage_id=f"cerebro:{agent}")


def runtime_activo() -> str:
    """``openhands`` (default) or ``propio``; anything else reads as ``openhands``."""
    from asistente.plataforma import settings

    valor = str(getattr(settings, "CEREBRO_RUNTIME", "openhands") or "openhands").strip().lower()
    return "propio" if valor == "propio" else "openhands"


__all__ = ["HerramientaCerebro", "LLMPuente", "run_subagent", "runtime_activo"]

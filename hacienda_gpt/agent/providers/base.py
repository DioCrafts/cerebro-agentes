"""Provider-neutral contract for the tool-using agent.

The agent loop (:mod:`hacienda_gpt.agent.agent`) never talks to an SDK. It
drives a :class:`Conversation` obtained from an :class:`LLMProvider`, so the
same loop runs on Claude (default), OpenAI, or any local model served behind an
OpenAI-compatible endpoint (Ollama, LM Studio, ``mlx_lm.server``, vLLM…).

Each provider keeps the conversation in its *native* wire format. That matters:
Claude's assistant turns carry thinking blocks that must be replayed verbatim,
and OpenAI's carry ``tool_calls`` objects — flattening either into a common
shape and rebuilding it would lose information the API needs back.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import logging
from typing import Any, Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

StopReason = Literal["end", "tool_use", "max_tokens", "refusal"]


class ProviderError(RuntimeError):
    """An LLM call failed after the SDK's own retries.

    ``retryable`` tells the caller whether trying again later can help (rate
    limit, 5xx, network) or not (bad request, auth, unknown model).
    """

    def __init__(self, message: str, *, retryable: bool = False, salida_invalida: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        # The model answered, but its output is unusable (not JSON, not an
        # object, truncated): a stronger model may do better (see enrutado.py).
        self.salida_invalida = salida_invalida


@dataclass(frozen=True)
class Attachment:
    """A file the model must read itself: a PDF or an image (scans, photos)."""

    media_type: str  # "application/pdf", "image/png", "image/jpeg", "image/webp" or "image/gif"
    data: bytes
    filename: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``input_schema`` is a JSON Schema object."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Set when the provider could not parse the model's arguments (OpenAI-style
    # APIs deliver them as a JSON string). The agent answers such a call with an
    # error result instead of executing the tool on garbage.
    arguments_error: str | None = None


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: dict[str, int] = field(default_factory=dict)


# Receives the model's visible text as it is generated, chunk by chunk.
TextCallback = Callable[[str], None]


@dataclass(frozen=True)
class Usage:
    """Tokens billed for ONE call to a model API, as the API itself reported them.

    ``input_tokens`` excludes the cached part of the prompt, which is counted in
    ``cache_read_tokens`` (served from the cache) and ``cache_write_tokens``
    (written to it). ``reported`` is False when the API gave no usage data: the
    call happened but its size is unknown.
    """

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # What the call was for ("agente", "verificador", "documentos"…); set by the metering wrapper.
    purpose: str = ""
    reported: bool = True
    # Part of ``cache_write_tokens`` written with the 1-hour TTL (priced higher than the 5-minute one).
    cache_write_1h_tokens: int = 0
    # Served by a batch API (asynchronous, discounted); set by the batch collector.
    batch: bool = False
    # Routing tier of the purpose ("pequeno" | "mediano" | "grande"; "" = not routed) and whether
    # this call is the one-off retry of an invalid output on the next tier. Set by the metering wrapper.
    tier: str = ""
    escalated: bool = False


# Optional hook a provider exposes as ``provider.on_usage``: called once per API
# call that reported its token usage. Not part of the :class:`LLMProvider`
# protocol — providers (and test doubles) without it keep working.
UsageCallback = Callable[[Usage], None]


def report_usage(provider: Any, build: Callable[[], Usage | None]) -> None:
    """Hand the usage of one API call to ``provider.on_usage``, if there is one.

    Never raises: measuring spend must not break an answer the user already paid
    for. ``build`` returns None when the response carried no usage data.
    """
    callback = getattr(provider, "on_usage", None)
    if callback is None:
        return
    try:
        usage = build()
        if usage is not None:
            callback(usage)
    except Exception:  # a malformed usage object or a failing callback
        logger.warning("could not report LLM usage", exc_info=True)


def token_count(source: Any, name: str) -> int:
    """``source.name`` as a non-negative int; 0 when missing or not a number."""
    value = getattr(source, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


@dataclass(frozen=True)
class EstadoLote:
    """Where a submitted batch stands. ``terminado``: every request has a final result."""

    id: str
    terminado: bool
    en_proceso: int = 0
    correctas: int = 0
    con_error: int = 0
    canceladas: int = 0
    caducadas: int = 0


@dataclass(frozen=True)
class ResultadoLote:
    """The result of one request of a batch, keyed by the ``custom_id`` it was sent with.

    Exactly one of ``datos`` (the parsed JSON object) and ``error`` is set. ``salida_invalida``:
    the model answered but its output was unusable. ``reintentable``: sending it again may work
    (server error, expired, cancelled), as opposed to a request the API rejected.
    """

    custom_id: str
    datos: dict[str, Any] | None = None
    error: str | None = None
    salida_invalida: bool = False
    reintentable: bool = False
    usage: Usage | None = None


@runtime_checkable
class ProveedorDeLotes(Protocol):
    """A provider that can run ``complete_json`` requests through an asynchronous batch API.

    ``peticion_json`` builds the provider-native request of one ``complete_json`` call (the
    same request the synchronous call sends, minus what batches reject); the other three
    submit, poll and collect. Implemented by Claude; OpenAI-compatible backends do not.
    """

    name: str
    model: str

    def peticion_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]: ...

    def crear_lote(self, peticiones: Sequence[tuple[str, dict[str, Any]]]) -> str: ...

    def estado_lote(self, lote_id: str) -> EstadoLote: ...

    def resultados_lote(self, lote_id: str) -> list[ResultadoLote]: ...


class Conversation(Protocol):
    def step(self, *, allow_tools: bool = True, on_text: TextCallback | None = None) -> ModelTurn:
        """Ask the model for its next turn and record it in the history.

        With ``on_text`` the request is streamed and each text chunk is passed
        to the callback as it arrives; the returned turn is the same either way.
        """
        ...

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Answer every tool call of the last turn (all of them, in one go)."""
        ...

    def add_user_message(self, text: str) -> None: ...


class LLMProvider(Protocol):
    name: str
    model: str

    def start(
        self,
        *,
        system: str,
        tools: list[ToolSpec],
        history: list[dict[str, str]],
        user_message: str,
    ) -> Conversation: ...

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        attachments: Sequence[Attachment] | None = None,
    ) -> dict[str, Any]:
        """Single-shot call that returns an object matching ``schema``.

        ``attachments`` are PDFs or images sent alongside ``user`` for the model
        to read (it needs a vision-capable model).
        """
        ...


def normalize_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Reduce caller-supplied chat history to clean user/assistant text turns.

    Drops anything that is not a plain-text user/assistant message, and any
    leading assistant turns (every supported API requires the first message to
    come from the user).
    """
    cleaned: list[dict[str, str]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content})
    while cleaned and cleaned[0]["role"] != "user":
        cleaned.pop(0)
    return cleaned


def rasterizar_pdf(data: bytes, *, max_pages: int, scale: float = 2.0) -> list[bytes]:
    """Render each page of a PDF as PNG, for backends that read images but not PDFs."""
    import io

    import pypdfium2

    pdf = pypdfium2.PdfDocument(data)
    try:
        pages: list[bytes] = []
        for index in range(min(len(pdf), max_pages)):
            buffer = io.BytesIO()
            pdf[index].render(scale=scale).to_pil().save(buffer, format="PNG")
            pages.append(buffer.getvalue())
        return pages
    finally:
        pdf.close()

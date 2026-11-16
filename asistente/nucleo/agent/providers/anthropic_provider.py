"""Claude provider (default) — official ``anthropic`` SDK, Messages API."""

from __future__ import annotations

import base64
from collections.abc import Sequence
import json
from typing import Any

from hacienda_gpt.agent.providers.base import (
    Attachment,
    EstadoLote,
    ModelTurn,
    ProviderError,
    ResultadoLote,
    StopReason,
    TextCallback,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    UsageCallback,
    normalize_history,
    report_usage,
    token_count,
)

# Non-streaming requests: 16k keeps a full answer + thinking comfortably under
# the SDK's HTTP timeout. Streamed requests have no such ceiling, so they get
# room to finish a long answer instead of being cut off mid-thought.
_MAX_TOKENS = 16000
_MAX_TOKENS_STREAMING = 64000

# Models whose safety classifiers can decline a request with
# ``stop_reason="refusal"``; for these we opt into the server-side fallback so a
# false positive on a tax question is re-run on another model inside the same
# call instead of surfacing as a dead end.
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# A ``cache_control`` marker on a prefix shorter than the model's minimum cacheable
# length (512 tokens on the current models) is silently ignored. Spanish prose runs at
# roughly 3.5 characters per token, so below this size an explicit marker on the
# system prompt is a wasted breakpoint and is not sent.
_CACHE_MIN_CARACTERES = 2048
CACHE_TTLS = ("5m", "1h")

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "pause_turn": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def _usage_dict(usage: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            out[key] = value
    return out


def usage_from_response(response: Any, *, provider: str, model: str, batch: bool = False) -> Usage | None:
    """Billed tokens of a Messages response; None when it carries no usage.

    The model is the one that served the request (``response.model``), which
    differs from the configured one after a refusal fallback.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    served = getattr(response, "model", None)
    escritos = token_count(usage, "cache_creation_input_tokens")
    return Usage(
        provider=provider,
        model=served if isinstance(served, str) and served else model,
        input_tokens=token_count(usage, "input_tokens"),
        output_tokens=token_count(usage, "output_tokens"),
        cache_read_tokens=token_count(usage, "cache_read_input_tokens"),
        cache_write_tokens=escritos,
        # The breakdown by TTL; never more than the total it breaks down.
        cache_write_1h_tokens=min(
            token_count(getattr(usage, "cache_creation", None), "ephemeral_1h_input_tokens"), escritos
        ),
        batch=batch,
    )


def _wrap_errors(exc: Exception) -> ProviderError:
    import anthropic

    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(f"Claude: límite de peticiones alcanzado ({exc})", retryable=True)
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return ProviderError(f"Claude: credenciales no válidas o sin permiso ({exc})")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(f"Claude: modelo o endpoint no encontrado ({exc})")
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderError(f"Claude: error {exc.status_code} ({exc})", retryable=exc.status_code >= 500)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(f"Claude: error de red ({exc})", retryable=True)
    return ProviderError(f"Claude: {exc}")


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        effort: str = "high",
        client: Any | None = None,
        refusal_fallback: bool = True,
        cache_ttl: str = "5m",
    ) -> None:
        if cache_ttl not in CACHE_TTLS:
            raise ValueError(f"LLM_CACHE_TTL debe ser uno de {', '.join(CACHE_TTLS)}, no {cache_ttl!r}.")
        self.model = model
        self.effort = effort
        self.cache_ttl = cache_ttl
        self._client = client
        self._fallback_pedido = refusal_fallback
        self._refusal_fallback = refusal_fallback and model.startswith(_FALLBACK_MODEL_PREFIXES)
        # Sibling providers on other models (routing by purpose) share this one's client.
        self._padre: AnthropicProvider | None = None
        self._variantes: dict[tuple[str, str], AnthropicProvider] = {}
        # Called with the token usage of every API call (see ops/consumo.py).
        self.on_usage: UsageCallback | None = None

    def con_modelo(self, model: str, *, effort: str | None = None) -> AnthropicProvider:
        """The same provider on another model (and effort), sharing the HTTP client.

        Used by the routing table (``agent/enrutado.py``). Returns ``self`` when nothing
        changes; the variants are cached, so a purpose always gets the same object.
        """
        effort = effort or self.effort
        if model == self.model and effort == self.effort:
            return self
        raiz = self._padre or self
        if model == raiz.model and effort == raiz.effort:
            return raiz
        clave = (model, effort)
        variante = raiz._variantes.get(clave)
        if variante is None:
            variante = AnthropicProvider(
                model=model,
                effort=effort,
                client=raiz._client,
                refusal_fallback=raiz._fallback_pedido,
                cache_ttl=raiz.cache_ttl,
            )
            variante._padre = raiz
            raiz._variantes[clave] = variante
        return variante

    def _report(self, response: Any) -> Any:
        report_usage(self, lambda: usage_from_response(response, provider=self.name, model=self.model))
        return response

    def _ensure_client(self) -> Any:
        if self._client is None:
            if self._padre is not None:
                self._client = self._padre._ensure_client()
            else:
                import anthropic

                # Zero-arg: resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an
                # `ant auth login` profile, in that order.
                self._client = anthropic.Anthropic()
        return self._client

    def _cache_control(self) -> dict[str, str]:
        # The 5-minute TTL is the API's default: sent without ``ttl``.
        return {"type": "ephemeral", "ttl": "1h"} if self.cache_ttl == "1h" else {"type": "ephemeral"}

    def _system(self, system: str) -> str | list[dict[str, Any]]:
        """The system prompt, with an explicit cache breakpoint when it is long enough to cache.

        The breakpoint on the (last) system block caches tools + system together, which
        is the long stable prefix of every request: the agent's and the specialists'
        instructions with the tool schemas, the recodificación rules and schema, the
        verifier's and the formats' instructions. It holds whatever happens later in
        ``messages``, and it is shared by independent requests with the same prefix.
        """
        if len(system) < _CACHE_MIN_CARACTERES:
            return system
        return [{"type": "text", "text": system, "cache_control": self._cache_control()}]

    def _params(self, kwargs: dict[str, Any], *, max_tokens: int, cache_automatica: bool = True) -> dict[str, Any]:
        """Request parameters with adaptive thinking, effort and caching set."""
        output_config = {"effort": self.effort, **kwargs.pop("output_config", {})}
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
        }
        if cache_automatica:
            # Caches the growing prefix (tools → system → messages) so each
            # iteration of the tool loop re-reads the previous ones at the
            # cache-read price. Not for one-shot requests: there the automatic
            # breakpoint lands after the unique tail and only adds write cost.
            params["cache_control"] = self._cache_control()
        return {**params, **kwargs}

    def _create(self, *, cache_automatica: bool = True, **kwargs: Any) -> Any:
        """One non-streamed Messages call."""
        import anthropic

        client = self._ensure_client()
        params = self._params(kwargs, max_tokens=_MAX_TOKENS, cache_automatica=cache_automatica)
        try:
            if self._refusal_fallback:
                return self._report(client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **params))
            return self._report(client.messages.create(**params))
        except anthropic.APIError as exc:
            raise _wrap_errors(exc) from exc

    def _stream(self, on_text: TextCallback, **kwargs: Any) -> Any:
        """One streamed Messages call: forwards text chunks, returns the final message."""
        import anthropic

        client = self._ensure_client()
        params = self._params(kwargs, max_tokens=_MAX_TOKENS_STREAMING)
        try:
            if self._refusal_fallback:
                manager = client.beta.messages.stream(betas=[_FALLBACK_BETA], fallbacks="default", **params)
            else:
                manager = client.messages.stream(**params)
            with manager as stream:
                for text in stream.text_stream:
                    on_text(text)
                # The final message accumulates the usage of the whole stream.
                return self._report(stream.get_final_message())
        except anthropic.APIError as exc:
            raise _wrap_errors(exc) from exc
        except ValueError as exc:
            # With eager input streaming the API no longer validates tool input;
            # the SDK raises ValueError from the iterator for JSON it cannot
            # parse at all. Nothing was appended to the history, so the caller
            # may simply retry the step.
            raise ProviderError(f"Claude emitió una llamada a herramienta ilegible ({exc})", retryable=True) from exc

    def start(
        self,
        *,
        system: str,
        tools: list[ToolSpec],
        history: list[dict[str, str]],
        user_message: str,
    ) -> AnthropicConversation:
        messages: list[dict[str, Any]] = [*normalize_history(history), {"role": "user", "content": user_message}]
        return AnthropicConversation(self, system=system, tools=tools, messages=messages)

    def _json_kwargs(
        self, *, system: str, user: str, schema: dict[str, Any], attachments: Sequence[Attachment] | None = None
    ) -> dict[str, Any]:
        content: str | list[dict[str, Any]] = user
        if attachments:
            # Claude reads PDFs (text and page images) and images natively; files go before the text.
            content = [
                {
                    "type": "document" if item.media_type == "application/pdf" else "image",
                    "source": {
                        "type": "base64",
                        "media_type": item.media_type,
                        "data": base64.standard_b64encode(item.data).decode("ascii"),
                    },
                }
                for item in attachments
            ]
            content.append({"type": "text", "text": user})
        return {
            "system": self._system(system),
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        attachments: Sequence[Attachment] | None = None,
    ) -> dict[str, Any]:
        del schema_name  # Claude's json_schema format carries no name
        response = self._create(
            cache_automatica=False,
            **self._json_kwargs(system=system, user=user, schema=schema, attachments=attachments),
        )
        return _leer_json(response)

    # --- Message Batches API (asynchronous, discounted; see agent/providers/lotes.py) ---

    def peticion_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        """The Messages request of one ``complete_json`` call, for a batch.

        Same model, thinking, effort, structured output and system cache breakpoint as the
        synchronous call; without the refusal fallback, which batches reject.
        """
        del schema_name
        kwargs = self._json_kwargs(system=system, user=user, schema=schema)
        return self._params(kwargs, max_tokens=_MAX_TOKENS, cache_automatica=False)

    def crear_lote(self, peticiones: Sequence[tuple[str, dict[str, Any]]]) -> str:
        """Submit ``(custom_id, params)`` requests as one Message Batch; returns its id."""
        import anthropic

        try:
            lote = self._ensure_client().messages.batches.create(
                requests=[{"custom_id": custom_id, "params": params} for custom_id, params in peticiones]
            )
        except anthropic.APIError as exc:
            raise _wrap_errors(exc) from exc
        return str(lote.id)

    def estado_lote(self, lote_id: str) -> EstadoLote:
        import anthropic

        try:
            lote = self._ensure_client().messages.batches.retrieve(lote_id)
        except anthropic.APIError as exc:
            raise _wrap_errors(exc) from exc
        cuentas = getattr(lote, "request_counts", None)
        return EstadoLote(
            id=lote_id,
            terminado=getattr(lote, "processing_status", None) == "ended",
            en_proceso=token_count(cuentas, "processing"),
            correctas=token_count(cuentas, "succeeded"),
            con_error=token_count(cuentas, "errored"),
            canceladas=token_count(cuentas, "canceled"),
            caducadas=token_count(cuentas, "expired"),
        )

    def resultados_lote(self, lote_id: str) -> list[ResultadoLote]:
        """Every result of an ended batch (in any order: key them by ``custom_id``)."""
        import anthropic

        resultados: list[ResultadoLote] = []
        try:
            for entrada in self._ensure_client().messages.batches.results(lote_id):
                resultados.append(self._resultado(entrada))
        except anthropic.APIError as exc:
            raise _wrap_errors(exc) from exc
        return resultados

    def _resultado(self, entrada: Any) -> ResultadoLote:
        custom_id = str(entrada.custom_id)
        resultado = entrada.result
        tipo = getattr(resultado, "type", None)
        if tipo == "succeeded":
            mensaje = resultado.message
            usage = usage_from_response(mensaje, provider=self.name, model=self.model, batch=True)
            try:
                return ResultadoLote(custom_id=custom_id, datos=_leer_json(mensaje), usage=usage)
            except ProviderError as exc:
                return ResultadoLote(
                    custom_id=custom_id, error=str(exc), salida_invalida=exc.salida_invalida, usage=usage
                )
        if tipo == "errored":
            detalle = getattr(getattr(resultado, "error", None), "error", None)
            clase = getattr(detalle, "type", None) or "error"
            mensaje_error = getattr(detalle, "message", None) or ""
            return ResultadoLote(
                custom_id=custom_id,
                error=f"Claude (lote): {clase} {mensaje_error}".strip(),
                # A request the API rejected fails again; anything else may not.
                reintentable=clase != "invalid_request_error",
            )
        return ResultadoLote(custom_id=custom_id, error=f"Claude (lote): petición {tipo}", reintentable=True)


def _leer_json(response: Any) -> dict[str, Any]:
    """The JSON object of a structured-output response; ProviderError when there is none."""
    if response.stop_reason == "refusal":
        raise ProviderError("Claude declinó la petición de verificación.")
    if response.stop_reason == "max_tokens":
        raise ProviderError("Claude: la salida estructurada se truncó por max_tokens.", salida_invalida=True)
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Claude devolvió JSON no válido: {text[:200]}", salida_invalida=True) from exc
    if not isinstance(data, dict):
        raise ProviderError("Claude devolvió JSON que no es un objeto.", salida_invalida=True)
    return data


class AnthropicConversation:
    def __init__(
        self,
        provider: AnthropicProvider,
        *,
        system: str,
        tools: list[ToolSpec],
        messages: list[dict[str, Any]],
    ) -> None:
        self._provider = provider
        self._system = system
        self._tools = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in tools
        ]
        self._messages = messages

    def step(self, *, allow_tools: bool = True, on_text: TextCallback | None = None) -> ModelTurn:
        kwargs: dict[str, Any] = {"system": self._provider._system(self._system), "messages": self._messages}
        if self._tools:
            # The tool list never changes between steps (that would invalidate
            # the prompt cache); "none" is how the loop asks for a final answer.
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = {"type": "auto" if allow_tools else "none"}
        if on_text is None:
            response = self._provider._create(**kwargs)
        else:
            if self._tools:
                # Streamed requests let tool inputs stream as generated. The API
                # then stops validating them, so the Toolbox type-checks every
                # argument and the agent never runs tools from a turn truncated
                # by max_tokens. (A run is streamed or not for its whole length,
                # so the tool list — and the prompt cache — stays stable.)
                kwargs["tools"] = [{**tool, "eager_input_streaming": True} for tool in self._tools]
            response = self._provider._stream(on_text, **kwargs)

        # Replay the full content (thinking blocks included) on the next call.
        self._messages.append({"role": "assistant", "content": response.content})

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                arguments = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))

        return ModelTurn(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=_STOP_REASONS.get(response.stop_reason or "end_turn", "end"),
            usage=_usage_dict(response.usage),
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # All results of a turn go back in ONE user message; splitting them
        # teaches the model to stop issuing parallel tool calls.
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.content,
                        **({"is_error": True} if result.is_error else {}),
                    }
                    for result in results
                ],
            }
        )

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

"""OpenAI provider — also serves local models behind an OpenAI-compatible API.

Two wire APIs, picked by ``api_style`` (``OPENAI_API_STYLE``):

* ``responses`` — OpenAI's own Responses API, the one OpenAI recommends for tools,
  reasoning and multi-turn state. Default for OpenAI itself.
* ``chat`` — Chat Completions. ``OpenAIProvider(base_url=...)`` is how the agent runs
  on Ollama, LM Studio, ``mlx_lm.server``, vLLM or llama.cpp's server: they speak
  Chat Completions with ``tools`` and rarely implement Responses, so a local
  ``base_url`` (or the ``local`` provider) selects this style automatically.

Responses turns are sent statelessly: every request carries the whole conversation
as input items, with ``store=False``. The agent already keeps each conversation in
memory in native format (as with Claude), nothing is left on OpenAI's servers, and it
works for organisations under zero data retention, where ``previous_response_id`` does
not. Reasoning items come back encrypted (``include=["reasoning.encrypted_content"]``)
and are replayed verbatim, so a reasoning model keeps its chain of thought across tool
calls without server-side state.

Tool-calling quality on small local models varies a lot; the loop tolerates
malformed arguments (reported back to the model as an error) but cannot make a
weak model plan well.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
import hashlib
import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlparse

from asistente.nucleo.agent.providers.base import (
    Attachment,
    ModelTurn,
    ProviderError,
    StopReason,
    TextCallback,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    UsageCallback,
    normalize_history,
    rasterizar_pdf,
    report_usage,
    token_count,
)

# Pages of a PDF sent as images in one request.
_MAX_PDF_PAGES = 20
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

ApiStyle = Literal["responses", "chat"]
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
# Models that take ``reasoning.effort``: the o-series and GPT-5 (but not its "-chat" variants).
_REASONING_MODEL_RE = re.compile(r"^(o\d|gpt-5)(?!.*-chat)", re.IGNORECASE)
# The system's effort levels (Claude's) onto OpenAI's: OpenAI has no "max", its top is "xhigh".
_EFFORT = {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}


def _is_local_url(url: str | None) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return host in _LOCAL_HOSTS or host.endswith((".local", ".localhost", ".internal"))


def resolve_api_style(*, name: str, base_url: str | None = None, api_style: str | None = None) -> ApiStyle:
    """``responses`` or ``chat``: the explicit choice (``OPENAI_API_STYLE``, passed in by the
    factory), else automatic: ``chat`` for the ``local`` provider and for a local base URL
    (the provider's own, or ``OPENAI_BASE_URL`` that the SDK reads), ``responses`` otherwise.
    """
    choice = (api_style or "").strip().lower()
    if choice in ("responses", "chat"):
        return choice  # type: ignore[return-value]
    if choice:
        raise ValueError(f"OPENAI_API_STYLE desconocido: {choice!r} (usa responses o chat)")
    if name == "local" or _is_local_url(base_url or os.environ.get("OPENAI_BASE_URL")):
        return "chat"
    return "responses"


def is_reasoning_model(model: str) -> bool:
    return bool(_REASONING_MODEL_RE.match(model))


def reasoning_effort(effort: str | None) -> str | None:
    """OpenAI's ``reasoning.effort`` for one of the system's effort levels; None = API default."""
    if not effort:
        return None
    return _EFFORT.get(effort.strip().lower())


def _wrap_errors(exc: Exception, label: str) -> ProviderError:
    import openai

    if isinstance(exc, openai.RateLimitError):
        return ProviderError(f"{label}: límite de peticiones alcanzado ({exc})", retryable=True)
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return ProviderError(f"{label}: credenciales no válidas o sin permiso ({exc})")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(f"{label}: modelo o endpoint no encontrado ({exc})")
    if isinstance(exc, openai.APIStatusError):
        return ProviderError(f"{label}: error {exc.status_code} ({exc})", retryable=exc.status_code >= 500)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(f"{label}: error de red ({exc})", retryable=True)
    return ProviderError(f"{label}: {exc}")


def _usage_dict(usage: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        value = getattr(usage, source, None)
        if isinstance(value, int):
            out[target] = value
    return out


def _responses_usage_dict(usage: Any) -> dict[str, int]:
    """A Responses turn's usage, in the keys the agent sums (input excludes the cached part)."""
    if usage is None:
        return {}
    total_input = token_count(usage, "input_tokens")
    cached = min(token_count(getattr(usage, "input_tokens_details", None), "cached_tokens"), total_input)
    out = {"input_tokens": total_input - cached, "output_tokens": token_count(usage, "output_tokens")}
    if cached:
        out["cache_read_input_tokens"] = cached
    reasoning = token_count(getattr(usage, "output_tokens_details", None), "reasoning_tokens")
    if reasoning:
        out["reasoning_tokens"] = reasoning  # already inside output_tokens: informative only
    return out


def usage_from_response(response: Any, *, provider: str, model: str) -> Usage | None:
    """Billed tokens of a Responses or Chat Completions response (or of the last chunk of a stream).

    None when there is no usage object, as with many local servers.
    The prompt count (``input_tokens`` / ``prompt_tokens``) includes the cached part of
    the prompt; it is split out when the API details it, so the two are never counted
    twice. Reasoning tokens are part of ``output_tokens`` and billed at the output price.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if getattr(usage, "prompt_tokens", None) is None and getattr(usage, "input_tokens", None) is not None:
        total_input = token_count(usage, "input_tokens")
        cached = min(token_count(getattr(usage, "input_tokens_details", None), "cached_tokens"), total_input)
        return Usage(
            provider=provider,
            model=model,
            input_tokens=total_input - cached,
            output_tokens=token_count(usage, "output_tokens"),
            cache_read_tokens=cached,
        )
    prompt = token_count(usage, "prompt_tokens")
    cached = min(token_count(getattr(usage, "prompt_tokens_details", None), "cached_tokens"), prompt)
    return Usage(
        provider=provider,
        model=model,
        input_tokens=prompt - cached,
        output_tokens=token_count(usage, "completion_tokens"),
        cache_read_tokens=cached,
    )


class OpenAIProvider:
    def __init__(
        self,
        *,
        model: str,
        client: Any | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        name: str = "openai",
        timeout: float = 120.0,
        effort: str | None = None,
        api_style: str | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.effort = effort
        self.api_style: ApiStyle = resolve_api_style(name=name, base_url=base_url, api_style=api_style)
        self._client = client
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._padre: OpenAIProvider | None = None
        self._variantes: dict[tuple[str, str | None], OpenAIProvider] = {}
        # Called with the token usage of every API call (see ops/consumo.py).
        self.on_usage: UsageCallback | None = None

    def con_modelo(self, model: str, *, effort: str | None = None) -> OpenAIProvider:
        """The same backend on another model or effort, sharing the HTTP client (routing by purpose).

        ``effort`` becomes ``reasoning.effort`` on reasoning models (Responses API only).
        """
        raiz = self._padre or self
        effort = effort or raiz.effort
        if model == raiz.model and effort == raiz.effort:
            return raiz
        variante = raiz._variantes.get((model, effort))
        if variante is None:
            variante = OpenAIProvider(
                model=model,
                client=raiz._client,
                base_url=raiz._base_url,
                api_key=raiz._api_key,
                name=raiz.name,
                timeout=raiz._timeout,
                effort=effort,
                api_style=raiz.api_style,
            )
            variante._padre = raiz
            raiz._variantes[(model, effort)] = variante
        return variante

    def _prompt_cache_key(self, system: Any, tools: Any = None) -> dict[str, Any]:
        """``prompt_cache_key`` for OpenAI itself: requests with the same stable prefix
        (system prompt + tool schemas) are routed together, which raises the share of
        the prompt OpenAI serves from its automatic cache (priced as cached input).
        Local OpenAI-compatible servers may reject unknown parameters: not sent there."""
        if self.name != "openai":
            return {}
        prefijo = json.dumps([system or "", tools or []], ensure_ascii=False, sort_keys=True, default=str)
        return {"prompt_cache_key": "hgpt-" + hashlib.sha256(prefijo.encode("utf-8")).hexdigest()[:32]}

    def _cache_key(self, messages: list[dict[str, Any]], tools: Any = None) -> dict[str, Any]:
        system = messages[0].get("content") if messages and messages[0].get("role") == "system" else ""
        return self._prompt_cache_key(system, tools)

    def _reasoning_params(self) -> dict[str, Any]:
        """``reasoning`` and the encrypted reasoning items, for reasoning models only
        (the others reject the parameter)."""
        if not is_reasoning_model(self.model):
            return {}
        params: dict[str, Any] = {"include": ["reasoning.encrypted_content"]}
        effort = reasoning_effort(self.effort)
        if effort:
            params["reasoning"] = {"effort": effort}
        return params

    def _report(self, response: Any) -> None:
        """Report the usage carried by a response or by a stream chunk."""
        report_usage(self, lambda: usage_from_response(response, provider=self.name, model=self.model))

    def _ensure_client(self) -> Any:
        if self._client is None and self._padre is not None:
            self._client = self._padre._ensure_client()
        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"timeout": self._timeout, "max_retries": 2}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = OpenAI(**kwargs)
        return self._client

    def _create(self, **kwargs: Any) -> Any:
        """One Chat Completions call."""
        import openai

        try:
            extra = self._cache_key(kwargs.get("messages") or [], kwargs.get("tools"))
            response = self._ensure_client().chat.completions.create(model=self.model, **extra, **kwargs)
        except openai.APIError as exc:
            raise _wrap_errors(exc, self.name) from exc
        if not kwargs.get("stream"):
            # A streamed call returns an iterator; its usage arrives in a chunk (see _stream).
            self._report(response)
        return response

    def _respond(self, **kwargs: Any) -> Any:
        """One Responses API call, stateless (``store=False``: see the module docstring)."""
        import openai

        try:
            extra = self._prompt_cache_key(kwargs.get("instructions"), kwargs.get("tools"))
            response = self._ensure_client().responses.create(
                model=self.model, store=False, **self._reasoning_params(), **extra, **kwargs
            )
        except openai.APIError as exc:
            raise _wrap_errors(exc, self.name) from exc
        if not kwargs.get("stream"):
            self._report(response)
        return response

    def start(
        self,
        *,
        system: str,
        tools: list[ToolSpec],
        history: list[dict[str, str]],
        user_message: str,
    ) -> OpenAIConversation | OpenAIResponsesConversation:
        turns: list[dict[str, Any]] = [*normalize_history(history), {"role": "user", "content": user_message}]
        if self.api_style == "responses":
            return OpenAIResponsesConversation(self, system=system, tools=tools, items=turns)
        return OpenAIConversation(self, tools=tools, messages=[{"role": "system", "content": system}, *turns])

    def _images(self, attachments: Sequence[Attachment], *, pdf_as_pages: bool) -> list[tuple[str, bytes]]:
        images: list[tuple[str, bytes]] = []
        for item in attachments:
            if item.media_type == "application/pdf" and pdf_as_pages:
                images += [("image/png", page) for page in rasterizar_pdf(item.data, max_pages=_MAX_PDF_PAGES)]
            else:
                images.append((item.media_type, item.data))
        return images

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        attachments: Sequence[Attachment] | None = None,
    ) -> dict[str, Any]:
        if self.api_style == "responses":
            return self._complete_json_responses(
                system=system, user=user, schema=schema, schema_name=schema_name, attachments=attachments
            )
        import openai

        content: str | list[dict[str, Any]] = user
        if attachments:
            # Images are the common denominator of OpenAI-compatible vision
            # models (local servers rarely accept PDF parts): PDFs go page by page.
            content = [{"type": "text", "text": user}]
            content += [
                {"type": "image_url", "image_url": {"url": _data_url(media_type, data)}}
                for media_type, data in self._images(attachments, pdf_as_pages=True)
            ]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        client = self._ensure_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
                **self._cache_key(messages),
            )
            self._report(response)
        except openai.BadRequestError:
            # Local servers frequently lack json_schema support. Degrade to a
            # prompt-carried schema; the caller validates the object anyway.
            fallback_messages = [{"role": "system", "content": _schema_in_prompt(system, schema)}, messages[1]]
            response = self._create(messages=fallback_messages)
        except openai.APIError as exc:
            raise _wrap_errors(exc, self.name) from exc

        content = response.choices[0].message.content or ""
        return _parse_json_object(content, self.name)

    def _complete_json_responses(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        attachments: Sequence[Attachment] | None,
    ) -> dict[str, Any]:
        import openai

        content: str | list[dict[str, Any]] = user
        if attachments:
            # The Responses API reads PDFs natively (text and page images): no rasterising.
            content = [{"type": "input_text", "text": user}]
            for item in attachments:
                url = _data_url(item.media_type, item.data)
                if item.media_type == "application/pdf":
                    content.append(
                        {"type": "input_file", "filename": item.filename or "documento.pdf", "file_data": url}
                    )
                else:
                    content.append({"type": "input_image", "image_url": url})
        user_item = {"role": "user", "content": content}
        try:
            response = self._respond(
                instructions=system,
                input=[user_item],
                text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            )
        except ProviderError as exc:
            # A schema the strict mode rejects (e.g. without additionalProperties: false):
            # degrade to a prompt-carried schema; the caller validates the object anyway.
            if not isinstance(exc.__cause__, openai.BadRequestError):
                raise
            response = self._respond(instructions=_schema_in_prompt(system, schema), input=[user_item])
        return _parse_json_object(output_text(response), self.name)


def _data_url(media_type: str, data: bytes) -> str:
    return f"data:{media_type};base64,{base64.standard_b64encode(data).decode('ascii')}"


def _schema_in_prompt(system: str, schema: dict[str, Any]) -> str:
    return (
        f"{system}\n\nResponde ÚNICAMENTE con un objeto JSON válido que cumpla este JSON Schema, "
        f"sin texto adicional:\n{json.dumps(schema, ensure_ascii=False)}"
    )


def output_text(response: Any) -> str:
    """The visible text of a Responses response (every ``output_text`` part, in order)."""
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                parts.append(getattr(part, "text", "") or "")
    return "".join(parts)


def _parse_json_object(content: str, label: str) -> dict[str, Any]:
    candidates = [content]
    match = _JSON_OBJECT_RE.search(content)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ProviderError(f"{label} devolvió JSON no válido: {content[:200]}", salida_invalida=True)


class OpenAIConversation:
    """A conversation on Chat Completions (local OpenAI-compatible servers, ``OPENAI_API_STYLE=chat``)."""

    def __init__(self, provider: OpenAIProvider, *, tools: list[ToolSpec], messages: list[dict[str, Any]]) -> None:
        self._provider = provider
        self._tools = [
            {
                "type": "function",
                "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema},
            }
            for tool in tools
        ]
        self._messages = messages

    def step(self, *, allow_tools: bool = True, on_text: TextCallback | None = None) -> ModelTurn:
        kwargs: dict[str, Any] = {"messages": self._messages}
        if self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto" if allow_tools else "none"

        if on_text is None:
            response = self._provider._create(**kwargs)
            choice = response.choices[0]
            content = choice.message.content or ""
            raw_calls = [
                (call.id, call.function.name, call.function.arguments or "{}")
                for call in choice.message.tool_calls or []
            ]
            finish_reason = choice.finish_reason
            usage = _usage_dict(getattr(response, "usage", None))
        else:
            content, raw_calls, finish_reason, usage = self._stream(on_text, kwargs)

        tool_calls: list[ToolCall] = []
        native_calls: list[dict[str, Any]] = []
        for call_id, name, raw_arguments in raw_calls:
            native_calls.append(
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw_arguments}}
            )
            tool_calls.append(_tool_call(call_id, name, raw_arguments))

        assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if native_calls:
            assistant["tool_calls"] = native_calls
        self._messages.append(assistant)

        stop_reason: StopReason
        if finish_reason == "length":
            # Checked first: a truncated turn may carry half-written tool calls
            # that must not be executed.
            stop_reason = "max_tokens"
        elif tool_calls:
            stop_reason = "tool_use"
        elif finish_reason == "content_filter":
            stop_reason = "refusal"
        else:
            stop_reason = "end"

        return ModelTurn(text=content.strip(), tool_calls=tool_calls, stop_reason=stop_reason, usage=usage)

    def _stream(
        self, on_text: TextCallback, kwargs: dict[str, Any]
    ) -> tuple[str, list[tuple[str, str, str]], str | None, dict[str, int]]:
        """Streamed Chat Completions call: forwards text, reassembles tool calls."""
        import openai

        if self._provider.name == "openai":
            # Local OpenAI-compatible servers often reject stream_options, so the
            # token usage of streamed turns is only requested from OpenAI itself.
            kwargs = {**kwargs, "stream_options": {"include_usage": True}}
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        usage_chunk: Any = None
        try:
            for chunk in self._provider._create(stream=True, **kwargs):
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = _usage_dict(chunk_usage)
                    usage_chunk = chunk
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "content", None):
                    text_parts.append(delta.content)
                    on_text(delta.content)
                for fragment in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
                    if fragment.id:
                        slot["id"] = fragment.id
                    function = getattr(fragment, "function", None)
                    if function is not None:
                        if function.name:
                            slot["name"] += function.name
                        if function.arguments:
                            slot["arguments"] += function.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except openai.APIError as exc:  # raised mid-stream, after _create returned the iterator
            raise _wrap_errors(exc, self._provider.name) from exc
        if usage_chunk is not None:
            # Reported once, from the last chunk that carried it: OpenAI sends the
            # totals in a final chunk, some servers repeat running totals in each.
            self._provider._report(usage_chunk)

        raw_calls = [
            (slot["id"] or f"call_{index}", slot["name"], slot["arguments"] or "{}")
            for index, slot in sorted(calls.items())
        ]
        return "".join(text_parts), raw_calls, finish_reason, usage

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for result in results:
            content = f"ERROR: {result.content}" if result.is_error else result.content
            self._messages.append({"role": "tool", "tool_call_id": result.call_id, "content": content})

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})


class OpenAIResponsesConversation:
    """A conversation on the Responses API, kept here as the full list of input items.

    Each step sends the whole list (``store=False``) and appends the model's output
    items to it: assistant text, ``function_call`` items and, for reasoning models,
    the encrypted ``reasoning`` items, which must be replayed for the model to keep
    its reasoning across tool calls. Tool results go back as ``function_call_output``.
    """

    def __init__(
        self, provider: OpenAIProvider, *, system: str, tools: list[ToolSpec], items: list[dict[str, Any]]
    ) -> None:
        self._provider = provider
        self._instructions = system
        self._tools = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
                # The Responses API makes functions strict by default, which demands every
                # property be required and additionalProperties false: not what our schemas say.
                "strict": False,
            }
            for tool in tools
        ]
        self._items = items

    def step(self, *, allow_tools: bool = True, on_text: TextCallback | None = None) -> ModelTurn:
        kwargs: dict[str, Any] = {"instructions": self._instructions, "input": list(self._items)}
        if self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto" if allow_tools else "none"
        response = self._provider._respond(**kwargs) if on_text is None else self._stream(on_text, kwargs)

        text_parts: list[str] = []
        refused = False
        tool_calls: list[ToolCall] = []
        for item in getattr(response, "output", None) or []:
            kind = getattr(item, "type", None)
            if kind == "message":
                message_parts: list[str] = []
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) == "output_text":
                        message_parts.append(getattr(part, "text", "") or "")
                    elif getattr(part, "type", None) == "refusal":
                        refused = True
                if message_parts:
                    text_parts += message_parts
                    self._items.append({"role": "assistant", "content": "".join(message_parts)})
            elif kind == "function_call":
                raw_arguments = getattr(item, "arguments", None) or "{}"
                # Replayed as sent, even if malformed; without the item id, which
                # points at server-side state that store=False does not keep.
                self._items.append(
                    {"type": "function_call", "call_id": item.call_id, "name": item.name, "arguments": raw_arguments}
                )
                tool_calls.append(_tool_call(item.call_id, item.name, raw_arguments))
            elif kind == "reasoning":
                self._items.append(_reasoning_item(item))
        content = "".join(text_parts)

        details = getattr(response, "incomplete_details", None)
        incomplete = getattr(details, "reason", None) if getattr(response, "status", None) == "incomplete" else None
        stop_reason: StopReason
        if incomplete == "max_output_tokens":
            # Checked first: a truncated turn may carry half-written tool calls
            # that must not be executed.
            stop_reason = "max_tokens"
        elif tool_calls:
            stop_reason = "tool_use"
        elif refused or incomplete == "content_filter":
            stop_reason = "refusal"
        else:
            stop_reason = "end"

        usage = _responses_usage_dict(getattr(response, "usage", None))
        return ModelTurn(text=content.strip(), tool_calls=tool_calls, stop_reason=stop_reason, usage=usage)

    def _stream(self, on_text: TextCallback, kwargs: dict[str, Any]) -> Any:
        """Streamed Responses call: forwards text deltas, returns the final response object."""
        import openai

        final: Any = None
        try:
            for event in self._provider._respond(stream=True, **kwargs):
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    if event.delta:
                        on_text(event.delta)
                elif kind in ("response.completed", "response.incomplete"):
                    final = event.response
                elif kind == "response.failed":
                    error = getattr(event.response, "error", None)
                    raise ProviderError(
                        f"{self._provider.name}: la respuesta falló ({getattr(error, 'message', error)})",
                        retryable=True,
                    )
                elif kind == "error":
                    raise ProviderError(f"{self._provider.name}: {getattr(event, 'message', event)}", retryable=True)
        except openai.APIError as exc:  # raised mid-stream, after the call returned the iterator
            raise _wrap_errors(exc, self._provider.name) from exc
        if final is None:
            raise ProviderError(f"{self._provider.name}: el stream terminó sin respuesta final", retryable=True)
        self._provider._report(final)
        return final

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for result in results:
            output = f"ERROR: {result.content}" if result.is_error else result.content
            self._items.append({"type": "function_call_output", "call_id": result.call_id, "output": output})

    def add_user_message(self, text: str) -> None:
        self._items.append({"role": "user", "content": text})


def _tool_call(call_id: str, name: str, raw_arguments: str) -> ToolCall:
    arguments: dict[str, Any] = {}
    error: str | None = None
    try:
        parsed = json.loads(raw_arguments)
        if isinstance(parsed, dict):
            arguments = parsed
        else:
            error = "los argumentos deben ser un objeto JSON"
    except json.JSONDecodeError as exc:
        error = f"argumentos JSON no válidos: {exc}"
    return ToolCall(id=call_id, name=name, arguments=arguments, arguments_error=error)


def _reasoning_item(item: Any) -> dict[str, Any]:
    """A reasoning output item as an input item: id, summary and the encrypted content."""
    dump = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(vars(item))
    return {key: dump[key] for key in ("type", "id", "summary", "encrypted_content") if key in dump}

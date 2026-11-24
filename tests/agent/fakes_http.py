"""Fake HTTP backends for the real SDKs: no network, but every request goes through the SDK's own
serialisation, so the tests see the exact JSON bodies the APIs would receive."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

import anthropic
import httpx
import httpx2
import openai

Respuesta = str | dict[str, Any] | Exception


def mensaje(texto: str, *, model: str, stop_reason: str = "end_turn", usage: dict[str, Any] | None = None):
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": texto}] if texto else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 1000, "output_tokens": 100},
    }


def eventos_sse(mensaje_completo: dict[str, Any]) -> bytes:
    """The Messages streaming events (SSE) that build ``mensaje_completo``, as the API sends them: the SDK's own
    accumulator rebuilds the same message from them (``get_final_message``)."""
    uso = dict(mensaje_completo.get("usage") or {})
    inicio = {**mensaje_completo, "content": [], "stop_reason": None, "stop_sequence": None, "usage": uso}
    eventos: list[tuple[str, dict[str, Any]]] = [("message_start", {"type": "message_start", "message": inicio})]
    for i, bloque in enumerate(mensaje_completo.get("content") or []):
        tipo = bloque.get("type")
        if tipo == "text":
            vacio, deltas = {"type": "text", "text": ""}, [{"type": "text_delta", "text": bloque["text"]}]
        elif tipo == "tool_use":
            vacio = {**bloque, "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": json.dumps(bloque.get("input") or {})}]
        elif tipo == "thinking":
            vacio = {"type": "thinking", "thinking": "", "signature": ""}
            deltas = [
                {"type": "thinking_delta", "thinking": bloque.get("thinking", "")},
                {"type": "signature_delta", "signature": bloque.get("signature", "")},
            ]
        else:  # server tool blocks and the rest arrive whole
            vacio, deltas = bloque, []
        eventos.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": vacio}))
        eventos += [("content_block_delta", {"type": "content_block_delta", "index": i, "delta": d}) for d in deltas]
        eventos.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    fin = {"stop_reason": mensaje_completo.get("stop_reason"), "stop_sequence": mensaje_completo.get("stop_sequence")}
    eventos.append(("message_delta", {"type": "message_delta", "delta": fin, "usage": uso}))
    eventos.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {nombre}\ndata: {json.dumps(datos)}\n\n" for nombre, datos in eventos).encode("utf-8")


class FakeAnthropicAPI:
    """Messages and Message Batches endpoints answered by ``responder(params) -> text | message dict``.

    ``polls_hasta_terminar``: how many ``retrieve`` calls answer "in_progress" before "ended".
    ``resultado_de(custom_id, params)`` may override a batch result (e.g. an errored one).
    """

    def __init__(
        self,
        responder: Callable[[dict[str, Any]], Respuesta],
        *,
        polls_hasta_terminar: int = 1,
        resultado_de: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.responder = responder
        self.polls_hasta_terminar = polls_hasta_terminar
        self.resultado_de = resultado_de
        self.usage = usage
        self.peticiones: list[tuple[str, str, Any]] = []
        self.lotes: dict[str, dict[str, Any]] = {}

    # --- what the tests look at ---------------------------------------------------

    def cuerpos(self, metodo: str = "POST", ruta: str = "/v1/messages") -> list[dict[str, Any]]:
        return [cuerpo for m, r, cuerpo in self.peticiones if m == metodo and r == ruta]

    def lotes_creados(self) -> list[dict[str, Any]]:
        return self.cuerpos("POST", "/v1/messages/batches")

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="sk-ant-test-not-a-key",
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self._handler)),
            max_retries=0,
        )

    # --- the fake server ----------------------------------------------------------

    def _mensaje(self, params: dict[str, Any]) -> dict[str, Any]:
        respuesta = self.responder(params)
        if isinstance(respuesta, Exception):
            raise respuesta
        if isinstance(respuesta, dict):
            return respuesta
        return mensaje(respuesta, model=params["model"], usage=self.usage)

    def _lote(self, lote_id: str, *, terminado: bool) -> dict[str, Any]:
        n = len(self.lotes[lote_id]["requests"])
        return {
            "id": lote_id,
            "type": "message_batch",
            "processing_status": "ended" if terminado else "in_progress",
            "request_counts": {
                "processing": 0 if terminado else n,
                "succeeded": n if terminado else 0,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "created_at": "2026-11-16T00:00:00Z",
            "expires_at": "2026-11-17T00:00:00Z",
            "ended_at": "2026-11-16T01:00:00Z" if terminado else None,
            "cancel_initiated_at": None,
            "archived_at": None,
            "results_url": f"https://api.anthropic.com/v1/messages/batches/{lote_id}/results" if terminado else None,
        }

    def _handler(self, request: httpx2.Request) -> httpx2.Response:
        ruta = request.url.path
        cuerpo = json.loads(request.content) if request.content else None
        self.peticiones.append((request.method, ruta, cuerpo))
        if ruta == "/v1/messages" and request.method == "POST":
            if cuerpo.get("stream"):
                return httpx2.Response(
                    200, headers={"content-type": "text/event-stream"}, content=eventos_sse(self._mensaje(cuerpo))
                )
            return httpx2.Response(200, json=self._mensaje(cuerpo))
        if ruta == "/v1/messages/batches" and request.method == "POST":
            lote_id = f"msgbatch_{len(self.lotes) + 1}"
            self.lotes[lote_id] = {"requests": cuerpo["requests"], "polls": 0}
            return httpx2.Response(200, json=self._lote(lote_id, terminado=False))
        if ruta.startswith("/v1/messages/batches/") and ruta.endswith("/results"):
            lote_id = ruta.split("/")[-2]
            lineas = []
            for peticion in self.lotes[lote_id]["requests"]:
                propio = self.resultado_de(peticion["custom_id"], peticion["params"]) if self.resultado_de else None
                resultado = propio or {"type": "succeeded", "message": self._mensaje(peticion["params"])}
                lineas.append(json.dumps({"custom_id": peticion["custom_id"], "result": resultado}))
            return httpx2.Response(200, content=("\n".join(lineas) + "\n").encode("utf-8"))
        if ruta.startswith("/v1/messages/batches/"):
            lote_id = ruta.rsplit("/", 1)[-1]
            lote = self.lotes[lote_id]
            lote["polls"] += 1
            return httpx2.Response(200, json=self._lote(lote_id, terminado=lote["polls"] > self.polls_hasta_terminar))
        return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": ruta}})


def respuesta_openai(
    output: list[dict[str, Any]],
    *,
    model: str = "gpt-test",
    status: str = "completed",
    incomplete: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Responses API response body."""
    return {
        "id": "resp_fake",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": status,
        "incomplete_details": {"reason": incomplete} if incomplete else None,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": usage
        if usage is not None
        else {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 12,
        },
    }


def mensaje_openai(texto: str) -> dict[str, Any]:
    """An assistant ``message`` output item of the Responses API."""
    return {
        "type": "message",
        "id": "msg_fake",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": texto, "annotations": []}],
    }


def llamada_openai(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    """A ``function_call`` output item of the Responses API."""
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


class FakeOpenAIAPI:
    """Responses and Chat Completions endpoints; records the request bodies.

    Without ``respuestas`` every call answers ``texto``. ``respuestas`` is a queue of
    Responses bodies (dicts), SSE event lists (lists of dicts, for streamed calls) or
    exceptions-as-status (an int: answered with that HTTP error).
    """

    def __init__(self, texto: str = '{"ok": true}', respuestas: list[Any] | None = None) -> None:
        self.texto = texto
        self.respuestas = list(respuestas or [])
        self.cuerpos: list[dict[str, Any]] = []
        self.rutas: list[str] = []

    def client(self, base_url: str = "http://openai.invalid/v1") -> openai.OpenAI:
        return openai.OpenAI(
            api_key="sk-test-not-a-key",
            base_url=base_url,
            http_client=httpx.Client(transport=httpx.MockTransport(self._handler)),
            max_retries=0,
        )

    def _handler(self, request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        self.cuerpos.append(cuerpo)
        self.rutas.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return self._responses(cuerpo)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 0,
                "model": cuerpo["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.texto},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    def _responses(self, cuerpo: dict[str, Any]) -> httpx.Response:
        respuesta = self.respuestas.pop(0) if self.respuestas else None
        if respuesta is None:
            return httpx.Response(200, json=respuesta_openai([mensaje_openai(self.texto)], model=cuerpo["model"]))
        if isinstance(respuesta, int):
            error = {"error": {"message": "rechazada", "type": "invalid_request_error", "code": None, "param": None}}
            return httpx.Response(respuesta, json=error)
        if isinstance(respuesta, list):
            sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in respuesta)
            return httpx.Response(200, content=sse.encode("utf-8"), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=respuesta)

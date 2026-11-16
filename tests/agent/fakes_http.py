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


class FakeOpenAIAPI:
    """Chat Completions answered with a fixed text; records the request bodies."""

    def __init__(self, texto: str = '{"ok": true}') -> None:
        self.texto = texto
        self.cuerpos: list[dict[str, Any]] = []

    def client(self) -> openai.OpenAI:
        return openai.OpenAI(
            api_key="sk-test-not-a-key",
            base_url="http://openai.invalid/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(self._handler)),
            max_retries=0,
        )

    def _handler(self, request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        self.cuerpos.append(cuerpo)
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

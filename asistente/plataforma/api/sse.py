"""Server-Sent Events (``text/event-stream``): framing, the response, and live snapshots of a store.

* :func:`evento` frames one named event (``id:``, ``event:``, ``data:``) and :func:`comentario` a keep-alive.
* :func:`respuesta_sse` wraps an async iterator of frames in a streaming response that proxies do not buffer.
* :func:`flujo_de_instantaneas` follows something kept in a store (a council session, a browser task): it reads
  it on the server at a fixed pace, sends a snapshot only when it changed, and ends when it reaches a final
  state, when the client goes away or after a maximum duration (the client then reconnects). Each snapshot's
  ``id`` is a digest of its content, so a client that reconnects with ``Last-Event-ID`` does not receive again
  the state it already has.
* :func:`leer_eventos` parses a stream back (tests and non-browser clients).

The reads run in the worker-thread pool one at a time (a short SQLite or in-memory read); the waiting between
them does not hold any thread.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import StreamingResponse

MEDIA_TYPE = "text/event-stream"

# How long the browser waits before its own reconnection (the frontend client uses its own growing wait).
REINTENTO_MS = 3000


def _json(datos: Any) -> str:
    return json.dumps(datos, ensure_ascii=False, default=str)


def evento(nombre: str, datos: Any, *, id: str | None = None, reintento_ms: int | None = None) -> str:
    """One SSE frame. ``datos`` is sent as JSON; a JSON text never has a raw newline, but every line is prefixed
    anyway so the frame stays valid whatever it holds."""
    lineas: list[str] = []
    if reintento_ms is not None:
        lineas.append(f"retry: {int(reintento_ms)}")
    if id is not None:
        lineas.append(f"id: {id.replace(chr(10), '').replace(chr(13), '')}")
    lineas.append(f"event: {nombre}")
    lineas.extend(f"data: {linea}" for linea in _json(datos).splitlines() or [""])
    return "\n".join(lineas) + "\n\n"


def comentario(texto: str = "ping") -> str:
    """A comment line: ignored by the client, keeps proxies and idle timers from cutting a healthy stream."""
    return f": {texto}\n\n"


def respuesta_sse(frames: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        frames,
        media_type=MEDIA_TYPE,
        # no-store: a stream is never cached; X-Accel-Buffering: nginx-style proxies must not buffer it.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def huella(datos: Any) -> str:
    """The id of a snapshot: a short digest of its JSON (same content, same id)."""
    texto = json.dumps(datos, ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class Instantanea:
    """What a read of the store gives: the data to send and whether nothing more will change."""

    datos: dict[str, Any]
    final: bool


async def flujo_de_instantaneas(
    request: Request,
    leer: Callable[[], Instantanea | None],
    *,
    nombre: str,
    intervalo: float = 1.0,
    latido: float = 15.0,
    duracion_maxima: float = 600.0,
) -> AsyncIterator[str]:
    """Snapshots of ``leer()`` as events named ``nombre`` while they change; ``fin`` once final or gone.

    ``leer`` returns None when the thing no longer exists (erased): the stream then sends ``fin`` with
    ``{"motivo": "no_encontrada"}``. A final snapshot is followed by ``fin`` with ``{"motivo": "terminada"}``.
    After ``duracion_maxima`` the stream simply ends: the client reconnects with ``Last-Event-ID``.
    """
    ultimo = request.headers.get("last-event-id") or None
    primero = True
    inicio = time.monotonic()
    ultimo_envio = inicio
    while True:
        if await request.is_disconnected():
            return
        instantanea = await anyio.to_thread.run_sync(leer)
        if instantanea is None:
            yield evento("fin", {"motivo": "no_encontrada"}, reintento_ms=REINTENTO_MS if primero else None)
            return
        id_actual = huella(instantanea.datos)
        if id_actual != ultimo:
            yield evento(nombre, instantanea.datos, id=id_actual, reintento_ms=REINTENTO_MS if primero else None)
            ultimo, primero, ultimo_envio = id_actual, False, time.monotonic()
        if instantanea.final:
            yield evento("fin", {"motivo": "terminada"}, id=id_actual)
            return
        ahora = time.monotonic()
        if ahora - inicio >= duracion_maxima:
            return
        if ahora - ultimo_envio >= latido:
            yield comentario()
            ultimo_envio = ahora
        await anyio.sleep(intervalo)


def leer_eventos(texto: str) -> list[dict[str, Any]]:
    """Parse an SSE body into ``[{"event", "data", "id"}]`` (``data`` decoded from JSON); comments are skipped."""
    eventos: list[dict[str, Any]] = []
    for bloque in texto.replace("\r\n", "\n").split("\n\n"):
        nombre, id_, datos = "message", None, []
        for linea in bloque.split("\n"):
            if not linea or linea.startswith(":"):
                continue
            campo, _, valor = linea.partition(":")
            valor = valor[1:] if valor.startswith(" ") else valor
            if campo == "event":
                nombre = valor
            elif campo == "id":
                id_ = valor
            elif campo == "data":
                datos.append(valor)
        if datos:
            eventos.append({"event": nombre, "id": id_, "data": json.loads("\n".join(datos))})
    return eventos


__all__ = [
    "MEDIA_TYPE",
    "Instantanea",
    "comentario",
    "evento",
    "flujo_de_instantaneas",
    "huella",
    "leer_eventos",
    "respuesta_sse",
]

"""A finished Messages answer served as ``messages.stream(...)`` serves one, for fake SDK clients.

The Claude provider streams every call (``AnthropicProvider._create`` reads ``get_final_message``), so a fake
client whose queue holds plain messages also answers ``stream``: ``como_flujo`` wraps them, and leaves alone what
already is a stream (a fake that scripts the text chunks).
"""

from __future__ import annotations

from typing import Any


class FlujoTerminado:
    def __init__(self, mensaje: Any) -> None:
        self._mensaje = mensaje

    def __enter__(self) -> FlujoTerminado:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    @property
    def text_stream(self) -> Any:
        return iter(b.text for b in getattr(self._mensaje, "content", []) or [] if getattr(b, "type", "") == "text")

    def get_final_message(self) -> Any:
        return self._mensaje


def como_flujo(respuesta: Any) -> Any:
    return respuesta if hasattr(respuesta, "__enter__") else FlujoTerminado(respuesta)

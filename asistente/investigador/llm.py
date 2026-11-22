"""Model calls through the OpenHands SDK (``openhands.sdk.LLM``), metered against the budget.

Every role is one ``LLM.completion`` call with a system and a user ``Message``; the answer is JSON. The same
``LLM`` object (or one per role) is what the brain already configures; tests pass ``openhands.sdk.testing.TestLLM``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from openhands.sdk import LLM, Message, TextContent  # noqa: E402

from asistente.investigador.presupuesto import Contador, estimar_tokens  # noqa: E402

_JSON = re.compile(r"\{.*\}", re.DOTALL)


class RespuestaInvalidaError(ValueError):
    pass


def _texto(respuesta: Any) -> str:
    return "".join(getattr(c, "text", "") for c in respuesta.message.content)


def _uso(respuesta: Any) -> tuple[int | None, float | None]:
    raw = getattr(respuesta, "raw_response", None)
    usage = getattr(raw, "usage", None)
    tokens = getattr(usage, "total_tokens", None) if usage is not None else None
    coste = (getattr(raw, "_hidden_params", None) or {}).get("response_cost") if raw is not None else None
    return (int(tokens) if tokens else None), (float(coste) if coste else None)


def completar_json(llm: LLM, sistema: str, usuario: str, contador: Contador, *, max_salida: int = 2000) -> dict:
    """One metered completion whose answer must be a JSON object."""
    previstos = estimar_tokens(sistema) + estimar_tokens(usuario) + max_salida
    contador.comprobar(previstos)
    respuesta = llm.completion(
        [
            Message(role="system", content=[TextContent(text=sistema)]),
            Message(role="user", content=[TextContent(text=usuario)]),
        ]
    )
    texto = _texto(respuesta)
    tokens, euros = _uso(respuesta)
    contador.registrar(tokens or (estimar_tokens(sistema) + estimar_tokens(usuario) + estimar_tokens(texto)), euros)
    encontrado = _JSON.search(texto)
    if not encontrado:
        raise RespuestaInvalidaError("El modelo no devolvió JSON.")
    try:
        datos = json.loads(encontrado.group(0))
    except json.JSONDecodeError as exc:
        raise RespuestaInvalidaError(f"JSON inválido: {exc}") from exc
    if not isinstance(datos, dict):
        raise RespuestaInvalidaError("Se esperaba un objeto JSON.")
    return datos


def llm_por_defecto() -> LLM:
    """An OpenHands LLM from the environment (``INVESTIGADOR_MODELO``, ``INVESTIGADOR_API_KEY`` / provider keys)."""
    modelo = os.environ.get("INVESTIGADOR_MODELO", "anthropic/claude-sonnet-5-5")
    clave = os.environ.get("INVESTIGADOR_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    return LLM(model=modelo, api_key=clave, usage_id="investigador")

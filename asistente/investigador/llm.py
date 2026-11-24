"""Model calls through the product's own provider layer (``LLMProvider``), metered against the budget.

Every role is one turn without tools: a system prompt and a user message; the answer is JSON. The provider is the
same kind the brain uses (``create_provider`` wrapped by ``medir``), so the research army's calls go through the
usage table, the per-question budget and the configured routing like every other call (ADR 0006). Tests pass any
object with ``start(...)`` returning a conversation whose ``step`` gives a turn with ``text`` and ``usage``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from asistente.investigador.presupuesto import Contador, estimar_tokens

_JSON = re.compile(r"\{.*\}", re.DOTALL)
_TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


class RespuestaInvalidaError(ValueError):
    pass


def completar_json(llm: Any, sistema: str, usuario: str, contador: Contador, *, max_salida: int = 2000) -> dict:
    """One metered completion whose answer must be a JSON object."""
    previstos = estimar_tokens(sistema) + estimar_tokens(usuario) + max_salida
    contador.comprobar(previstos)
    turno = llm.start(system=sistema, tools=[], history=[], user_message=usuario).step(allow_tools=False)
    texto = turno.text or ""
    uso = getattr(turno, "usage", None) or {}
    tokens = sum(int(uso.get(k, 0) or 0) for k in _TOKENS)
    contador.registrar(
        tokens or (estimar_tokens(sistema) + estimar_tokens(usuario) + estimar_tokens(texto)), coste_real(llm, uso)
    )
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


def coste_real(llm: Any, uso: dict[str, Any]) -> float | None:
    """What the call cost at the price table's rates (``ops/data/precios_llm.json``, in USD, counted here as euros:
    close enough for a cap); None when the model has no price or the provider reported no usage, and the meter then
    falls back to its flat estimate (``euros_por_millon_tokens``)."""
    if not uso:
        return None
    from asistente.nucleo.agent.providers.base import Usage
    from asistente.plataforma.ops.consumo import calcular_coste

    usage = Usage(
        provider=str(getattr(llm, "name", "") or ""),
        model=str(getattr(llm, "model", "") or ""),
        input_tokens=int(uso.get("input_tokens", 0) or 0),
        output_tokens=int(uso.get("output_tokens", 0) or 0),
        cache_read_tokens=int(uso.get("cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(uso.get("cache_creation_input_tokens", 0) or 0),
    )
    try:
        coste = calcular_coste(usage)
    except Exception:  # a price table that cannot be read never stops a research
        return None
    return float(coste) if coste is not None else None


def llm_por_defecto() -> Any:
    """The metered provider for the army (purpose ``investigador``).

    ``INVESTIGADOR_MODELO`` pins a model; a ``proveedor/modelo`` value (``anthropic/claude-…``, the old LiteLLM
    form) also picks the provider. Without it, the configured provider and model (``LLM_PROVIDER``…).
    """
    from asistente.nucleo.agent.providers import create_provider
    from asistente.plataforma.ops.consumo import medir

    valor = os.environ.get("INVESTIGADOR_MODELO", "").strip()
    nombre, _, modelo = valor.partition("/")
    if not modelo:
        nombre, modelo = "", valor
    proveedor = create_provider(nombre or None, model=modelo or None)
    return medir(proveedor, "investigador")

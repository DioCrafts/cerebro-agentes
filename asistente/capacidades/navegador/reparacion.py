"""Optional selector repair: a model PROPOSES a new selector when a page changed; a deterministic gate decides.

When a step's selector no longer finds its element, the run can ask a model (only if the operator switched
``NAVEGADOR_REPARAR_SELECTORES`` on and a provider is configured) for a replacement. The model sees:

* the step WITHOUT its value: id, action, the field name, its label text, the button text;
* a redacted skeleton of the page (tags, ids, names, types and label/heading/button texts with anything that
  looks personal masked; no field value; never the login page, which the agent does not read at all).

Its answer is a CSS selector, nothing else. It is used only if :func:`puerta` accepts it on the live page:

1. it is a plain CSS selector (no other engines, no credential fields) — :func:`comprobar_selector`;
2. it matches exactly ONE element;
3. for a field: same tag family, the same ``name`` attribute as the script declares (same form field names),
   and a label that literally contains the script's label text; never a password/PIN/card field;
4. for a control: its visible text literally contains the script's text, and (except for ``detener``, which is
   never clicked) it does not submit, sign, pay or accept anything.

The model never chooses what to do: the step, its value and whether to stop come from the script. A repair
that passed the gate is used for that run only and recorded as a proposal for a person to review
(``vigilancia.py``); it never changes the script in force by itself.
"""

from __future__ import annotations

from collections.abc import Iterable
import json
import logging
from typing import Any, Protocol

from asistente.capacidades.navegador.guion import (
    Paso,
    PasoDetener,
    PasoPulsar,
    PasoRellenar,
    PasoSeleccionar,
    comprobar_selector,
)
from asistente.capacidades.navegador.seguridad import problemas_campo, problemas_pulsable

logger = logging.getLogger(__name__)

SYSTEM = (
    "Eres un asistente que repara selectores CSS de un guion de navegación cuando la página ha cambiado. "
    "Recibes un paso del guion (sin valores) y un esqueleto anonimizado de la página. Devuelve SOLO un "
    "selector CSS que encuentre el MISMO elemento que describe el paso: el mismo nombre de campo y la misma "
    "etiqueta o el mismo texto. No propongas nunca un campo de contraseña, PIN o pago, ni un botón distinto "
    "del descrito. Si no lo encuentras con seguridad, devuelve selector vacío."
)
ESQUEMA = {
    "type": "object",
    "properties": {
        "selector": {"type": "string", "description": "Selector CSS, o vacío si no hay uno seguro."},
        "motivo": {"type": "string"},
    },
    "required": ["selector", "motivo"],
    "additionalProperties": False,
}


class Reparador(Protocol):
    def proponer(self, paso: Paso, esqueleto: list[dict[str, Any]]) -> str | None: ...


def describir_paso(paso: Paso) -> dict[str, Any]:
    """What the model is told about a step: never its value (the user's data)."""
    datos: dict[str, Any] = {"id": paso.id, "accion": paso.accion, "descripcion": paso.descripcion}
    selector = getattr(paso, "selector", None)
    if selector:
        datos["selector_anterior"] = selector
    if isinstance(paso, PasoRellenar):
        datos.update(campo=paso.campo, etiqueta=paso.etiqueta)
    if isinstance(paso, PasoPulsar):
        datos["texto"] = paso.texto
    return datos


class ReparadorLLM:
    """A :class:`Reparador` backed by an ``LLMProvider`` (``complete_json``)."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.llamadas = 0

    def proponer(self, paso: Paso, esqueleto: list[dict[str, Any]]) -> str | None:
        usuario = json.dumps({"paso": describir_paso(paso), "esqueleto": esqueleto}, ensure_ascii=False)
        self.llamadas += 1
        try:
            respuesta = self.provider.complete_json(
                system=SYSTEM, user=usuario, schema=ESQUEMA, schema_name="selector_reparado"
            )
        except Exception as exc:  # a failed repair is just a failed step
            logger.warning("selector repair failed: %s", exc)
            return None
        selector = respuesta.get("selector") if isinstance(respuesta, dict) else None
        return selector.strip() if isinstance(selector, str) and selector.strip() else None


def puerta(
    paso: Paso, selector: str, coincidencias: int, info: dict[str, Any] | None, origenes: Iterable[str]
) -> list[str]:
    """Deterministic gate of a proposed selector on the live page. Empty list: accepted."""
    try:
        comprobar_selector(selector)
    except ValueError as exc:
        return [str(exc)]
    if coincidencias != 1 or info is None:
        return [f"el selector encuentra {coincidencias} elementos; debe encontrar exactamente uno"]
    if isinstance(paso, PasoRellenar):
        return problemas_campo(
            info, campo=paso.campo, etiqueta=paso.etiqueta, seleccion=isinstance(paso, PasoSeleccionar)
        )
    if isinstance(paso, PasoPulsar):
        return problemas_pulsable(info, texto=paso.texto, origenes=origenes, es_parada=isinstance(paso, PasoDetener))
    texto = getattr(paso, "texto", None)
    if texto:
        from asistente.capacidades.navegador.seguridad import contiene_literal

        if not contiene_literal(texto, str(info.get("texto") or "")):
            return [f"su texto no contiene «{texto}»"]
    return []


__all__ = ["ESQUEMA", "SYSTEM", "Reparador", "ReparadorLLM", "describir_paso", "puerta"]

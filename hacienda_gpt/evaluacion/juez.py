"""Optional LLM-judge rubric, gated deterministically.

Some expectations cannot be checked by a regex ("explains that the regional part is approximate", "reaches
the DGT's conclusion"). A judge model checks them, but its word alone is not enough: a criterion counts as
met only if the judge **quotes the answer literally** where it is met, and the program checks the quote is
really in the answer (whitespace and case aside). A judge that approves without a verifiable quote does not
approve. Failing a criterion needs no quote (an omission cannot be quoted), which keeps the gate one-sided:
the judge can make a case fail, never pass on its own say-so.
"""

from __future__ import annotations

from typing import Any

from hacienda_gpt.agent.providers.base import LLMProvider
from hacienda_gpt.evaluacion.modelos import Rubrica, VeredictoJuez

# A quote shorter than this proves nothing (a "sí" is in almost any answer).
MIN_CITA = 12

JUEZ_SYSTEM_PROMPT = """\
Evalúas la respuesta de un asistente del hogar (fiscalidad y administración doméstica en España) según una lista \
de criterios. Recibes la pregunta del usuario, la respuesta del asistente, los criterios numerados y, si la hay, \
una referencia con la conclusión correcta.

Para cada criterio di si la respuesta lo cumple (`cumple`). Si lo cumple, copia en `cita` el fragmento LITERAL de \
la respuesta que lo demuestra, tal cual aparece, sin parafrasear ni abreviar (un programa comprobará que el \
fragmento está en la respuesta; si no está, tu veredicto no cuenta). Si no lo cumple, deja `cita` vacía o copia el \
fragmento que lo incumple. En `motivo`, una frase. Juzga solo lo que pide cada criterio, no el estilo.
"""

JUEZ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "veredictos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "criterio": {"type": "integer"},
                    "cumple": {"type": "boolean"},
                    "cita": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["criterio", "cumple", "cita", "motivo"],
            },
        }
    },
    "required": ["veredictos"],
}


def _normalizar(texto: str) -> str:
    return " ".join(texto.replace("«", '"').replace("»", '"').split()).casefold()


def cita_literal(cita: str, respuesta: str) -> bool:
    """True when ``cita`` is a long-enough literal piece of ``respuesta`` (whitespace and case aside)."""
    normalizada = _normalizar(cita).strip(" .,;:\"'")
    return len(normalizada) >= MIN_CITA and normalizada in _normalizar(respuesta)


def juzgar(juez: LLMProvider, *, pregunta: str, respuesta: str, rubrica: Rubrica) -> list[VeredictoJuez]:
    """One verdict per criterion, after the quote gate. Raises whatever the provider raises."""
    criterios = "\n".join(f"{n}. {c}" for n, c in enumerate(rubrica.criterios, start=1))
    partes = [f"<pregunta>\n{pregunta}\n</pregunta>"]
    if rubrica.referencia:
        partes.append(f"<referencia>\n{rubrica.referencia}\n</referencia>")
    partes.append(f"<respuesta>\n{respuesta}\n</respuesta>")
    partes.append(f"<criterios>\n{criterios}\n</criterios>")
    datos = juez.complete_json(
        system=JUEZ_SYSTEM_PROMPT, user="\n\n".join(partes), schema=JUEZ_SCHEMA, schema_name="rubrica_evaluacion"
    )
    recibidos: dict[int, dict[str, Any]] = {}
    for item in datos.get("veredictos") or []:
        if isinstance(item, dict) and isinstance(item.get("criterio"), int):
            recibidos.setdefault(item["criterio"], item)
    veredictos: list[VeredictoJuez] = []
    for numero, criterio in enumerate(rubrica.criterios, start=1):
        item = recibidos.get(numero)
        if item is None:
            veredictos.append(VeredictoJuez(criterio=criterio, cumple=False, motivo="El juez no lo evaluó."))
            continue
        cita = str(item.get("cita") or "")
        motivo = str(item.get("motivo") or "")
        if item.get("cumple") is True:
            if cita_literal(cita, respuesta):
                veredictos.append(VeredictoJuez(criterio=criterio, cumple=True, cita=cita, motivo=motivo))
            else:
                veredictos.append(
                    VeredictoJuez(
                        criterio=criterio,
                        cumple=False,
                        cita=cita,
                        motivo="El juez lo dio por cumplido sin citar literalmente la respuesta: no cuenta.",
                        cita_valida=False,
                    )
                )
        else:
            veredictos.append(VeredictoJuez(criterio=criterio, cumple=False, cita=cita, motivo=motivo))
    return veredictos


__all__ = ["JUEZ_SCHEMA", "JUEZ_SYSTEM_PROMPT", "MIN_CITA", "cita_literal", "juzgar"]

"""Step 1 of a research: split the question into sub-questions and the queries for each source.

A small model proposes (purpose ``investigacion:planificar``); code decides: the plan is cut to the depth's
number of sub-questions, queries are trimmed, and when the model is unavailable, over budget or proposes
nothing usable, a deterministic plan (the question itself and its topic words) is used instead. Only the
question (and the optional domain hint) is sent to the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hacienda_gpt.capacidades.investigacion.fuentes import palabras_clave

PROPOSITO = "investigacion:planificar"

SISTEMA = (
    "Planificas una investigación con fuentes para un asistente del hogar español. Divide la pregunta en "
    "subpreguntas concretas (como mucho las que se indiquen) y, para cada una, escribe:\n"
    "- consulta_corpus: una frase descriptiva con la terminología técnica de la norma o del trámite, para buscar "
    "en un corpus de legislación y doctrina;\n"
    "- terminos_boe: de 1 a 3 palabras que aparecerían en el TÍTULO de la norma del BOE que lo regula (p. ej. "
    "«pasaporte», «expedición»);\n"
    "- consulta_web: una búsqueda corta para un buscador web.\n"
    "No respondas a la pregunta, no inventes datos y no añadas datos personales que la pregunta no contenga."
)

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subpreguntas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pregunta": {"type": "string"},
                    "consulta_corpus": {"type": "string"},
                    "terminos_boe": {"type": "array", "items": {"type": "string"}},
                    "consulta_web": {"type": "string"},
                },
                "required": ["pregunta", "consulta_corpus", "terminos_boe", "consulta_web"],
            },
        }
    },
    "required": ["subpreguntas"],
}


@dataclass(frozen=True)
class Subpregunta:
    pregunta: str
    consulta_corpus: str
    terminos_boe: tuple[str, ...]
    consulta_web: str


def _texto(valor: Any, maximo: int) -> str:
    return " ".join(valor.split())[:maximo] if isinstance(valor, str) else ""


def plan_determinista(pregunta: str) -> list[Subpregunta]:
    """The plan without a model: the question as the only sub-question, its topic words for the BOE."""
    limpia = " ".join(pregunta.split())[:300]
    return [Subpregunta(limpia, limpia, tuple(palabras_clave(limpia, 3)), limpia)]


def leer_plan(datos: Mapping[str, Any], *, maximo: int) -> list[Subpregunta]:
    """The model's plan, trimmed to ``maximo`` usable sub-questions (an empty list when none is usable)."""
    subpreguntas: list[Subpregunta] = []
    crudas = datos.get("subpreguntas")
    for cruda in crudas if isinstance(crudas, list) else []:
        if not isinstance(cruda, Mapping):
            continue
        pregunta = _texto(cruda.get("pregunta"), 300)
        consulta = _texto(cruda.get("consulta_corpus"), 300) or pregunta
        if len(pregunta) < 5:
            continue
        terminos_crudos = cruda.get("terminos_boe")
        terminos = tuple(
            t for t in (_texto(x, 40) for x in (terminos_crudos if isinstance(terminos_crudos, list) else [])) if t
        )[:3]
        subpreguntas.append(
            Subpregunta(
                pregunta=pregunta,
                consulta_corpus=consulta,
                terminos_boe=terminos or tuple(palabras_clave(pregunta, 3)),
                consulta_web=_texto(cruda.get("consulta_web"), 200) or pregunta,
            )
        )
        if len(subpreguntas) >= maximo:
            break
    return subpreguntas


def validar(maximo: int) -> Any:
    """The deterministic check of the model's plan (escalation: a reason, or None when usable)."""

    def comprobar(datos: dict[str, Any]) -> str | None:
        return None if leer_plan(datos, maximo=maximo) else "el plan no tiene ninguna subpregunta utilizable"

    return comprobar


def mensaje(pregunta: str, ambito: str | None, maximo: int) -> str:
    partes = [f"Pregunta: {pregunta}"]
    if ambito:
        partes.append(f"Ámbito: {ambito}")
    partes.append(f"Como mucho {maximo} subpregunta(s).")
    return "\n".join(partes)


__all__ = ["ESQUEMA", "PROPOSITO", "SISTEMA", "Subpregunta", "leer_plan", "mensaje", "plan_determinista", "validar"]

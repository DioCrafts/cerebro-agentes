"""Deterministic citation check: a quote counts only if it appears literally in a source that was read.

Also figures: every number in a claim must appear in one of its quotes (a model cannot round or invent figures).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from asistente.investigador.modelo import Fuente

_ESPACIOS = re.compile(r"\s+")
_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKC", texto).lower()
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return _ESPACIOS.sub(" ", t).strip()


def cita_literal(cita: str, fuente: Fuente, minimo: int = 12) -> bool:
    c = normalizar(cita).strip(' ."\'…')
    return len(c) >= minimo and c in normalizar(fuente.texto)


def _cifras(texto: str) -> set[str]:
    return {m.replace(",", ".") for m in _CIFRA.findall(texto)}


def cifras_respaldadas(afirmacion: str, citas: Sequence[str]) -> bool:
    respaldo = _cifras(" ".join(citas))
    return _cifras(afirmacion) <= respaldo


def motivo_invalida(texto: str, fuentes_ids: Sequence[str], citas: Sequence[str], fuentes: Mapping[str, Fuente]) -> str | None:
    """None if the claim is backed: every cited source was read, every quote is literal in a cited source, and its
    figures appear in the quotes. Otherwise, why not."""
    if not fuentes_ids or not citas:
        return "sin fuente o sin cita"
    leidas = [fuentes[f] for f in fuentes_ids if f in fuentes]
    if len(leidas) != len(fuentes_ids):
        return "cita una fuente que no se leyó"
    for cita in citas:
        if not any(cita_literal(cita, f) for f in leidas):
            return "la cita no aparece literalmente en la fuente"
    if not cifras_respaldadas(texto, citas):
        return "contiene cifras que no están en las citas"
    return None

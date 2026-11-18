"""Automatic signals that an answer may have gone wrong, read from the case's audit trail.

Every ``/agent`` turn on a case is already audited (``agent_answer``: the question, the tool trace, the
verifier's claims). These signals are derived from that record on demand, so nothing new is stored and
nothing is kept after the case is erased:

* ``afirmacion_rechazada`` — the verifier marked claims as unsupported or only partly supported;
* ``error_herramienta`` — a tool returned an error;
* ``especialista_sin_resultado`` — the central agent consulted a module specialist that answered without
  using any of its tools (typically «no es mi ámbito»): a likely misroute;
* ``repregunta`` — the user asked (almost) the same thing again right after, or opened with a correction;
* ``puerta_cifras`` — the household brain's figures gate still found problems in the FINAL answer (after its
  revisions): a figure with no origin, a sum no tool made, a figure attributed to the wrong specialist;
* ``abstencion`` — the assistant abstained.

They label a user's feedback and are aggregated (counts only, no content) by
``python -m asistente.plataforma.cli.evaluacion senales``. Four of them are WEAK signals of a mistake
(:data:`SENALES_DEBILES`): when the owner of the case has switched on automatic learning for it, each becomes a
low-priority CANDIDATE case for the review queue (:mod:`asistente.nucleo.evaluacion.revision`), masked like any
other; without that consent they stay hints, never content.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

TipoSenal = Literal[
    "afirmacion_rechazada",
    "error_herramienta",
    "especialista_sin_resultado",
    "repregunta",
    "puerta_cifras",
    "abstencion",
]
# Signals that, with the owner's standing consent, become candidate cases (lower priority than a 👎).
SENALES_DEBILES: frozenset[str] = frozenset({"puerta_cifras", "afirmacion_rechazada", "repregunta", "abstencion"})

_SIN_FRAGMENTOS_RE = re.compile(r"especialista \S+: 0 fragmento")
_PALABRA_RE = re.compile(r"\w{4,}", re.UNICODE)
_CORRECCION_RE = re.compile(
    r"^\s*(no[,.!]|eso no|no es eso|te he dicho|te dije|otra vez|vuelve a|est[aá] mal|te equivocas|"
    r"no me has|no has entendido|me refiero a)",
    re.IGNORECASE,
)
SIMILITUD_REPREGUNTA = 0.6
VENTANA_REPREGUNTA = timedelta(minutes=30)


class Senal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoSenal
    # Index of the ``agent_answer`` event among the case's answers (0 = first).
    turno: int
    herramienta: str | None = None
    modulo: str | None = None
    # Never user text: counts, verdicts and tool names only.
    detalle: str = ""


def respuestas(eventos: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in eventos if isinstance(e, dict) and e.get("event_type") == "agent_answer"]


def senales_de_turno(evento: dict[str, Any], turno: int) -> list[Senal]:
    senales: list[Senal] = []
    claims = [c for c in evento.get("claims") or [] if isinstance(c, dict)]
    por_veredicto = Counter(str(c.get("verdict")) for c in claims if c.get("verdict") in ("no_respaldada", "parcial"))
    if por_veredicto:
        senales.append(
            Senal(
                tipo="afirmacion_rechazada",
                turno=turno,
                detalle=", ".join(f"{n} {v}" for v, n in sorted(por_veredicto.items())),
            )
        )
    traza = [t for t in evento.get("tool_trace") or [] if isinstance(t, dict)]
    for entrada in traza:
        if entrada.get("is_error"):
            senales.append(
                Senal(
                    tipo="error_herramienta",
                    turno=turno,
                    herramienta=str(entrada.get("tool") or ""),
                    detalle=f"llamada por {entrada.get('agent') or 'asesor'}",
                )
            )
    for entrada in traza:
        herramienta = str(entrada.get("tool") or "")
        if not herramienta.startswith("consultar_") or (entrada.get("agent") or "asesor") != "asesor":
            continue
        modulo = herramienta.removeprefix("consultar_")
        propias = [
            t
            for t in traza
            if t.get("agent") == f"especialista_{modulo}"
            and t.get("tool") != "leer_expediente"
            and not t.get("is_error")
        ]
        sin_fragmentos = bool(_SIN_FRAGMENTOS_RE.search(str(entrada.get("summary") or "")))
        if not propias and (sin_fragmentos or entrada.get("is_error")):
            senales.append(
                Senal(
                    tipo="especialista_sin_resultado",
                    turno=turno,
                    herramienta=herramienta,
                    modulo=modulo,
                    detalle="el especialista no usó ninguna herramienta (posible consulta fuera de su ámbito)",
                )
            )
    if evento.get("mode") == "abstained":
        senales.append(Senal(tipo="abstencion", turno=turno, detalle="el asistente se abstuvo"))
    cerebro = evento.get("cerebro")
    puerta = cerebro.get("puerta") if isinstance(cerebro, dict) else None
    violaciones = [v for v in (puerta or {}).get("violaciones") or [] if isinstance(v, dict)] if isinstance(puerta, dict) else []
    if violaciones:
        por_tipo = Counter(str(v.get("tipo")) for v in violaciones)
        senales.append(
            Senal(
                tipo="puerta_cifras",
                turno=turno,
                detalle=", ".join(f"{n} {t}" for t, n in sorted(por_tipo.items())),
            )
        )
    return senales


def _palabras(texto: str) -> set[str]:
    return {p.casefold() for p in _PALABRA_RE.findall(texto)}


def _momento(evento: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(evento.get("event_time")))
    except ValueError:
        return None


def es_repregunta(anterior: dict[str, Any], siguiente: dict[str, Any]) -> bool:
    """The user asked again: same words (Jaccard ≥ 0.6) or a correction, within half an hour."""
    a, b = _momento(anterior), _momento(siguiente)
    if a is not None and b is not None and b - a > VENTANA_REPREGUNTA:
        return False
    texto = str(siguiente.get("input") or "")
    if _CORRECCION_RE.match(texto):
        return True
    pa, pb = _palabras(str(anterior.get("input") or "")), _palabras(texto)
    if len(pa) < 3 or len(pb) < 3:
        return False
    return len(pa & pb) / len(pa | pb) >= SIMILITUD_REPREGUNTA


def senales_de_auditoria(eventos: Sequence[dict[str, Any]]) -> list[Senal]:
    """Every signal of a case's audit trail, in turn order."""
    turnos = respuestas(eventos)
    senales: list[Senal] = []
    for indice, evento in enumerate(turnos):
        senales.extend(senales_de_turno(evento, indice))
        if indice > 0 and es_repregunta(turnos[indice - 1], evento):
            # The signal is about the answer that prompted the repeat: the previous one.
            senales.append(Senal(tipo="repregunta", turno=indice - 1, detalle="el usuario volvió a preguntar"))
    return senales


def agregar(senales: Iterable[Senal]) -> dict[str, Any]:
    """Counts by type, tool and module (no content), for the operator."""
    lista = list(senales)
    return {
        "total": len(lista),
        "por_tipo": dict(sorted(Counter(s.tipo for s in lista).items())),
        "por_herramienta": dict(sorted(Counter(s.herramienta for s in lista if s.herramienta).items())),
        "por_modulo": dict(sorted(Counter(s.modulo for s in lista if s.modulo).items())),
    }


__all__ = [
    "SENALES_DEBILES",
    "Senal",
    "TipoSenal",
    "agregar",
    "es_repregunta",
    "respuestas",
    "senales_de_auditoria",
    "senales_de_turno",
]

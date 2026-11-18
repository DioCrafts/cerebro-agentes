"""Deterministic, explainable priority: which points the digest has, in which order, and why.

No model is involved here. Each signal gets a score from a fixed table of features (:data:`RASGOS`), each feature
with its points and a Spanish reason built from the signal's own facts («Vence el 20/11/2026 (en 4 días)»), so the
digest can always say why something is first and the evaluation can check it:

* **plazo** — days to the deadline (:data:`TRAMOS_PLAZO`): today and tomorrow first;
* **gravedad** — the module marked it urgent;
* **consecuencia** — a legal consequence read from a legal data file (an electronic notification not opened is
  deemed rejected after the days the law says), or an official deadline;
* **normativa** — a change in the BOE the tax module found affects this profile (it only writes the notice then);
* **dinero** — the amount its module states (:data:`TRAMOS_IMPORTE`); amounts of different signals are never added;
* **decision** — a concrete proposal is waiting for the user (one click solves it), and how long it has waited;
* **novedad** — new since the last pass;
* context signals (decisions noted, events) start low: they fill the digest only when there is room.

A point that scores below :data:`PUNTUACION_MINIMA` (nothing due, no money, nothing to decide, not new) is not
listed: a notice stays in the inbox and is counted («y N avisos más»); context is simply left out.

Then the user's decisions (:func:`aplicar_decisiones`, the decision memory's own rules, ``memoria.modelo.evaluar``):
a decision in force holds the signal back (or only its proposal, when the decision is about the proposal); one
that expired or whose reopening condition holds lets it through, saying why. Signals about the same matter are
grouped (:func:`agrupar`: explicit links first — a task, its pending action and its notices —, then the inbox's
own «same matter» rule across modules), and the list is capped (``REPASO_MAX_PUNTOS``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.repaso.senales import Gravedad, Propuesta, Senal

# Days to the deadline -> points (first row whose limit is >= the days left). Past deadlines: :data:`VENCIDO`.
TRAMOS_PLAZO: tuple[tuple[int, int], ...] = ((0, 60), (1, 55), (3, 45), (7, 30), (14, 18), (30, 8))
PLAZO_LEJANO = 2
VENCIDO = 40
# |amount| -> points (first row whose floor is <= the amount).
TRAMOS_IMPORTE: tuple[tuple[Decimal, int], ...] = (
    (Decimal(1000), 15),
    (Decimal(300), 10),
    (Decimal(50), 6),
    (Decimal("0.01"), 3),
)
PUNTOS_URGENTE = 25
PUNTOS_AVISO = 8
PUNTOS_CONSECUENCIA = 20
PUNTOS_OFICIAL = 15
PUNTOS_NORMATIVA = 12
PUNTOS_PROPUESTA = 12
PUNTOS_ESPERA_LARGA = 5
DIAS_ESPERA_LARGA = 7
PUNTOS_NOVEDAD = 8
# Context (a decision noted, mail classified): below anything the user has to act on.
PUNTOS_CONTEXTO = -15
TIPOS_CONTEXTO = frozenset({"decision", "novedad"})
# Each other domain that announces the same matter.
PUNTOS_POR_AMBITO_EXTRA = 3
# A deadline this close of something official or irreversible makes the point urgent (push, severity).
DIAS_URGENTE = 2
MAX_PUNTOS_POR_DEFECTO = 7
MAX_PUNTOS_TOPE = 12
# A point needs at least this score to be listed (a stale «info» notice with nothing due scores 0).
PUNTUACION_MINIMA = 1


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def eur(importe: Decimal) -> str:
    """Spanish money: 1.234,56 €."""
    texto = f"{abs(importe):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if importe < 0 else ''}{texto} €"


def fecha_es(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def cuando(dias: int) -> str:
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    return f"en {dias} días"


@dataclass(frozen=True)
class Rasgo:
    """One feature of a signal's score: its key, its points and its Spanish reason (empty: not shown)."""

    clave: str
    puntos: int
    motivo: str = ""


def _plazo(s: Senal) -> Rasgo | None:
    h = s.hechos
    if h.vence is None or h.dias is None:
        return None
    if h.dias < 0:
        return Rasgo("plazo", VENCIDO, f"Su plazo terminó el {fecha_es(h.vence)}")
    for limite, puntos in TRAMOS_PLAZO:
        if h.dias <= limite:
            return Rasgo("plazo", puntos, f"Vence el {fecha_es(h.vence)} ({cuando(h.dias)})")
    return Rasgo("plazo", PLAZO_LEJANO, f"Vence el {fecha_es(h.vence)}")


def _gravedad(s: Senal) -> Rasgo | None:
    if s.gravedad == "urgente":
        return Rasgo("gravedad", PUNTOS_URGENTE, "Su módulo lo marca como urgente")
    if s.gravedad == "aviso":
        return Rasgo("gravedad", PUNTOS_AVISO)
    return None


def _consecuencia(s: Senal) -> Rasgo | None:
    if s.consecuencia:
        return Rasgo("consecuencia", PUNTOS_CONSECUENCIA, s.consecuencia)
    if s.oficial:
        return Rasgo("consecuencia", PUNTOS_OFICIAL, "Es un plazo oficial")
    return None


def _normativa(s: Senal) -> Rasgo | None:
    if not s.normativa:
        return None
    return Rasgo("normativa", PUNTOS_NORMATIVA, "Es un cambio del BOE que afecta a tu situación")


def _dinero(s: Senal) -> Rasgo | None:
    importe = s.hechos.importe
    if importe is None or importe == 0:
        return None
    for suelo, puntos in TRAMOS_IMPORTE:
        if abs(importe) >= suelo:
            return Rasgo("dinero", puntos, f"Importe que indica su módulo: {eur(importe)}")
    return None


def _decision(s: Senal) -> Rasgo | None:
    if s.propuesta is None:
        return None
    return Rasgo("decision", PUNTOS_PROPUESTA, "Está esperando tu decisión y lo puedes resolver desde aquí")


def _espera(s: Senal) -> Rasgo | None:
    dias = s.hechos.numeros.get("dias_esperando")
    if s.propuesta is None or dias is None or dias < DIAS_ESPERA_LARGA:
        return None
    return Rasgo("espera", PUNTOS_ESPERA_LARGA, f"Lleva {int(dias)} días esperando tu confirmación")


def _novedad(s: Senal) -> Rasgo | None:
    if not s.nueva:
        return None
    return Rasgo("novedad", PUNTOS_NOVEDAD, "Es nuevo desde tu último repaso")


def _contexto(s: Senal) -> Rasgo | None:
    return Rasgo("contexto", PUNTOS_CONTEXTO) if s.tipo in TIPOS_CONTEXTO else None


# The table, in the order the reasons are shown when they tie on points.
RASGOS: tuple[Callable[[Senal], Rasgo | None], ...] = (
    _plazo,
    _consecuencia,
    _normativa,
    _gravedad,
    _dinero,
    _decision,
    _espera,
    _novedad,
    _contexto,
)


def rasgos(senal: Senal) -> list[Rasgo]:
    return [r for funcion in RASGOS if (r := funcion(senal)) is not None]


def puntuar(senal: Senal) -> int:
    return sum(r.puntos for r in rasgos(senal))


def es_urgente(senal: Senal) -> bool:
    """Urgent (the digest's severity, and so the push): the module said so, or an official or irreversible
    deadline is at most :data:`DIAS_URGENTE` days away."""
    if senal.gravedad == "urgente":
        return True
    dias = senal.hechos.dias
    return dias is not None and 0 <= dias <= DIAS_URGENTE and (senal.oficial or senal.irreversible)


# --------------------------------------------------------------------------- #
# The user's decisions
# --------------------------------------------------------------------------- #


class Omitido(_Modelo):
    """A signal (or its proposal) held back by a decision of the user."""

    senal_id: str
    decision_id: str
    # «senal»: the whole signal; «propuesta»: only its proposal.
    alcance: str
    motivo: str


def aplicar_decisiones(
    senales: Sequence[Senal],
    decisiones: Sequence[Any],
    *,
    hoy: date,
    hechos_caso: Mapping[str, Any] | None,
) -> tuple[list[Senal], list[Omitido], dict[str, str]]:
    """Signals after the decisions: (kept, held back, {signal id: why it comes back}).

    The decision memory's rules, not new ones: a decision covers its ámbito and narrower ones; only the kinds that
    hold proposals back (``BLOQUEAN_AVISOS``) do; :func:`~asistente.nucleo.memoria.modelo.evaluar` says whether it is
    in force today (expiry, reopening by date, by a changed fact, by a much better amount). The notice the inbox
    already let come back once (``reabierta_clave``) stays, with the reason.
    """
    from asistente.nucleo.memoria.modelo import BLOQUEAN_AVISOS, cubre, evaluar

    vivas = [d for d in decisiones if not d.sustituida_por and d.tipo in BLOQUEAN_AVISOS]
    hechos = dict(hechos_caso) if hechos_caso is not None else None
    salida: list[Senal] = []
    omitidos: list[Omitido] = []
    reaperturas: dict[str, str] = {}
    for senal in senales:
        ambitos: list[tuple[str, str]] = []
        if senal.ambito_decision:
            ambitos.append(("senal", senal.ambito_decision))
        if senal.propuesta is not None and senal.propuesta.ambito:
            ambitos.append(("propuesta", senal.propuesta.ambito))
        retenida: Omitido | None = None
        sin_propuesta = False
        for alcance, ambito in ambitos:
            for decision in (d for d in vivas if cubre(d.ambito, ambito)):
                if senal.fuente.clave and decision.reabierta_clave == senal.fuente.clave:
                    reaperturas[senal.id] = decision.reabierta_motivo or "ha cambiado algo desde que lo decidiste"
                    continue
                evaluacion = evaluar(decision, hoy=hoy, hechos=hechos, importe=senal.hechos.importe)
                if evaluacion.vigente:
                    omitido = Omitido(
                        senal_id=senal.id, decision_id=decision.id, alcance=alcance, motivo=decision.razon[:300]
                    )
                    if alcance == "senal":
                        retenida = omitido
                    else:
                        sin_propuesta = True
                        omitidos.append(omitido)
                    break
                if evaluacion.estado in ("reabrible", "caducada") and evaluacion.motivo:
                    reaperturas.setdefault(senal.id, evaluacion.motivo)
            if retenida is not None:
                break
        if retenida is not None:
            omitidos.append(retenida)
            continue
        if sin_propuesta:
            senal = senal.model_copy(update={"propuesta": None})
        salida.append(senal)
    return salida, omitidos, reaperturas


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #


def mismo_asunto(a: Senal, b: Senal) -> bool:
    """The inbox's rule (``bandeja.mismo_asunto``) on signals: two domains, deadlines a few days apart and a
    distinctive word in common («Renovación del seguro del coche» / «Cargo anual: Seguro Coche Mutua»)."""
    from asistente.nucleo.proactive.bandeja import DIAS_MISMO_ASUNTO, _palabras

    if a.ambito == b.ambito or a.hechos.vence is None or b.hechos.vence is None:
        return False
    if abs((a.hechos.vence - b.hechos.vence).days) > DIAS_MISMO_ASUNTO:
        return False
    return bool(_palabras(a.titulo) & _palabras(b.titulo))


def agrupar(senales: Sequence[Senal]) -> list[list[Senal]]:
    """Signals about the same matter together (union-find): explicit links, then :func:`mismo_asunto`."""
    indice = {s.id: i for i, s in enumerate(senales)}
    padre = list(range(len(senales)))

    def raiz(i: int) -> int:
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    def unir(i: int, j: int) -> None:
        a, b = raiz(i), raiz(j)
        if a != b:
            padre[max(a, b)] = min(a, b)

    for i, senal in enumerate(senales):
        for enlace in senal.enlaces:
            j = indice.get(enlace)
            if j is not None:
                unir(i, j)
    for i, a in enumerate(senales):
        for j in range(i + 1, len(senales)):
            if mismo_asunto(a, senales[j]):
                unir(i, j)
    grupos: dict[int, list[Senal]] = {}
    for i, senal in enumerate(senales):
        grupos.setdefault(raiz(i), []).append(senal)
    return list(grupos.values())


# --------------------------------------------------------------------------- #
# Points
# --------------------------------------------------------------------------- #


class Punto(_Modelo):
    """One point of the digest: a group of signals about one matter, ranked, with its reasons."""

    id: str = Field(pattern=r"^p[0-9]{1,2}$")
    rango: int = Field(ge=1)
    # The leading signal's id: stable across passes (the cache of texts is keyed by it).
    clave: str
    titulo: str
    ambitos: list[str]
    gravedad: Gravedad
    urgente: bool
    puntuacion: int
    vence: date | None = None
    # The reasons (Spanish), most weighty first.
    por_que: list[str] = Field(default_factory=list)
    rasgos: list[str] = Field(default_factory=list)
    senales: list[Senal] = Field(min_length=1)
    propuestas: list[Propuesta] = Field(default_factory=list)
    reapertura: str | None = None

    @property
    def lider(self) -> Senal:
        return self.senales[0]

    @property
    def contexto(self) -> bool:
        return all(s.tipo in TIPOS_CONTEXTO for s in self.senales)


class Priorizacion(_Modelo):
    puntos: list[Punto] = Field(default_factory=list)
    # Points left out by the cap (the digest says how many).
    restantes: int = 0
    omitidos: list[Omitido] = Field(default_factory=list)

    @property
    def hay_algo(self) -> bool:
        """Something the user may act on (context alone writes no digest, like the old one with nothing open)."""
        return any(not p.contexto for p in self.puntos)


@dataclass
class _Grupo:
    senales: list[Senal]
    puntuacion: int = 0
    rasgos: list[Rasgo] = field(default_factory=list)


def _punto(grupo: _Grupo, *, nombres: Mapping[str, str], reaperturas: Mapping[str, str]) -> Punto:
    lider = grupo.senales[0]
    ambitos = list(dict.fromkeys(s.ambito for s in grupo.senales))
    por_que = [r.motivo for r in sorted(grupo.rasgos, key=lambda r: -r.puntos) if r.motivo]
    otros = [nombres.get(a, a) for a in ambitos[1:]]
    if otros:
        por_que.append("También lo indica " + " y ".join(otros))
    reapertura = next((reaperturas[s.id] for s in grupo.senales if s.id in reaperturas), None)
    if reapertura:
        por_que.append(f"Te lo vuelvo a mencionar porque {reapertura}")
    propuestas: list[Propuesta] = []
    for senal in grupo.senales:
        if senal.propuesta is not None and senal.propuesta.id not in {p.id for p in propuestas}:
            propuestas.append(senal.propuesta)
    vences = [s.hechos.vence for s in grupo.senales if s.hechos.vence is not None]
    rango_gravedad = {"urgente": 0, "aviso": 1, "info": 2}
    return Punto(
        id="p1",
        rango=1,
        clave=lider.id,
        titulo=lider.titulo,
        ambitos=ambitos,
        gravedad=min((s.gravedad for s in grupo.senales), key=lambda g: rango_gravedad[g]),
        urgente=any(es_urgente(s) for s in grupo.senales),
        puntuacion=grupo.puntuacion,
        vence=min(vences) if vences else None,
        por_que=list(dict.fromkeys(por_que))[:6],
        rasgos=list(dict.fromkeys(r.clave for r in grupo.rasgos)),
        senales=grupo.senales,
        propuestas=propuestas[:3],
        reapertura=reapertura,
    )


def priorizar(
    senales: Sequence[Senal],
    *,
    hoy: date,
    decisiones: Sequence[Any] = (),
    hechos_caso: Mapping[str, Any] | None = None,
    max_puntos: int = MAX_PUNTOS_POR_DEFECTO,
    nombres: Mapping[str, str] | None = None,
) -> Priorizacion:
    """The ranked points of a pass. Ties: earlier deadline first, then the stable key."""
    max_puntos = max(1, min(MAX_PUNTOS_TOPE, int(max_puntos)))
    vivas, omitidos, reaperturas = aplicar_decisiones(senales, decisiones, hoy=hoy, hechos_caso=hechos_caso)
    grupos: list[_Grupo] = []
    for miembros in agrupar(vivas):
        puntuadas = sorted(((puntuar(s), s) for s in miembros), key=lambda par: (-par[0], par[1].id))
        lider_puntos, lider = puntuadas[0]
        ordenadas = [lider] + [s for _p, s in puntuadas[1:]]
        ambitos = {s.ambito for s in ordenadas}
        extra = PUNTOS_POR_AMBITO_EXTRA * (len(ambitos) - 1)
        todos = [r for s in ordenadas for r in rasgos(s)]
        # One reason per feature (the strongest), so a group does not repeat «vence…» per member.
        mejores: dict[str, Rasgo] = {}
        for r in todos:
            if r.clave not in mejores or r.puntos > mejores[r.clave].puntos:
                mejores[r.clave] = r
        grupos.append(_Grupo(senales=ordenadas, puntuacion=lider_puntos + extra, rasgos=list(mejores.values())))
    puntos = [_punto(g, nombres=nombres or {}, reaperturas=reaperturas) for g in grupos]
    puntos.sort(key=lambda p: (-p.puntuacion, p.vence or date.max, p.clave))
    # Nothing due, no money, nothing to decide, not new: not worth the user's attention this period. It stays in
    # the inbox (counted below when it is a notice); context (events, decisions noted) is simply left out.
    dignos = [p for p in puntos if p.puntuacion >= PUNTUACION_MINIMA]
    fuera = sum(1 for p in puntos if p.puntuacion < PUNTUACION_MINIMA and not p.contexto)
    elegidos = dignos[:max_puntos]
    numerados = [p.model_copy(update={"id": f"p{i}", "rango": i}) for i, p in enumerate(elegidos, start=1)]
    return Priorizacion(puntos=numerados, restantes=len(dignos) - len(elegidos) + fuera, omitidos=omitidos)


__all__ = [
    "DIAS_URGENTE",
    "PUNTOS_CONTEXTO",
    "PUNTUACION_MINIMA",
    "RASGOS",
    "TRAMOS_IMPORTE",
    "TRAMOS_PLAZO",
    "Omitido",
    "Priorizacion",
    "Punto",
    "Rasgo",
    "agrupar",
    "aplicar_decisiones",
    "cuando",
    "es_urgente",
    "eur",
    "fecha_es",
    "mismo_asunto",
    "priorizar",
    "puntuar",
    "rasgos",
]

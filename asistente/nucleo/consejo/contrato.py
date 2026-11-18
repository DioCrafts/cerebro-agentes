"""Shared types of the household council (read ``docs/consejo/arquitectura.md`` once it exists).

A council session decides ONE hard household question (amortizar o invertir, aceptar una herencia, comprar o
alquilar, recurrir una liquidación…). It never decides what the law says or computes a figure: the brain's
specialists build a verified ``ExpedienteConsejo`` first (phase 0), and every figure the council uses is a
``Cifra`` of that dossier. The phases:

1. ``instruccion``: the dossier — neutral question, options, facts, figures with provenance, the minimum profile
   the decision needs (pseudonymised: no name, no attribute the decision does not depend on), the user's own
   preference SEALED (members do not see it in round 1, so they cannot simply agree with the user).
2. ``propuestas``: every member, independently and in parallel, writes a ``Propuesta`` (structured: same schema
   for all, so no one is recognisable by style).
3. ``criticas``: every member reviews the OTHER members' proposals, shown as ``PropuestaAnonima`` with labels
   («A», «B»…) in an order shuffled per reviewer; scores them on a fixed rubric and ranks them (``Critica``).
4. ``revision``: every member reads the critiques of its proposal (and, now, the user's sealed preference) and
   writes a revised ``Propuesta``.
5. ``sintesis``: the chair aggregates the rankings (robust rank aggregation), measures agreement, and writes the
   ``Dictamen``: recommendation, confidence, consensus or dissent (with the minority's case), the assumptions the
   decision hinges on, what would change it, what datum would settle the doubt, every figure attributed. The
   figures gate and the verifier of the brain run on it; nothing acts outside the app without confirmation.

Authorship is revealed only in the final ruling (for transparency), never while members critique each other.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asistente.nucleo.cerebro.contrato import Cifra, Emit, ResultadoPuerta

MAX_MIEMBROS = 7
MIN_MIEMBROS = 3
MAX_OPCIONES = 6
MAX_TEXTO_PROPUESTA = 3500  # characters of reasoning per proposal (verbosity bias cap)

_ID_RE = r"^[a-z][a-z0-9_]{1,40}$"
_OPCION_RE = r"^o[1-9]$"
_ETIQUETA_RE = r"^[A-G]$"

FaseConsejo = Literal["instruccion", "propuestas", "criticas", "revision", "sintesis"]
EstadoSesion = Literal[
    "pendiente_confirmacion",  # convened by the brain, waiting for the user to accept the estimated cost
    "en_cola",
    "instruyendo",
    "deliberando",
    "sintetizando",
    "terminada",
    "fallida",
    "cancelada",
    "rechazada",  # the user said no to convening it
]
Consenso = Literal["unanime", "mayoria", "dividido", "insuficiente"]
TipoError = Literal["cifra", "supuesto", "omision", "logica", "ley", "riesgo_ignorado"]


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------------------------------------------


class Rol(_Modelo):
    """One council seat: a perspective with its mandate. Who fills it (model, provider) is decided at runtime."""

    id: str = Field(pattern=_ID_RE)  # «prudente», «fiscal», «liquidez_familia», «abogado_del_diablo»…
    nombre: str  # «Prudencia financiera»
    mandato: str = Field(min_length=20, max_length=1500)  # Spanish: what this seat must weigh above all
    # Model purpose for metering/routing («consejo:miembro:prudente»).
    proposito: str
    # Preferred provider family for diversity («anthropic», «openai», «local») — a hint, never required.
    familia_preferida: str | None = None


class Miembro(_Modelo):
    """A seat filled for one session (revealed only in the ruling)."""

    rol: Rol
    proveedor: str  # provider name actually used
    modelo: str  # model actually used


# --------------------------------------------------------------------------------------------------------------
# The dossier (phase 0)
# --------------------------------------------------------------------------------------------------------------


class Opcion(_Modelo):
    id: str = Field(pattern=_OPCION_RE)  # o1, o2…
    descripcion: str = Field(min_length=3, max_length=400)


class HechoExpediente(_Modelo):
    """A fact the decision depends on, in neutral words, with where it came from."""

    clave: str = Field(min_length=1, max_length=80)
    texto: str = Field(min_length=1, max_length=600)
    origen: Literal["expediente", "especialista", "memoria", "decision_usuario", "usuario"]
    # The specialist / fragment / memory item behind it, when there is one.
    referencia: str | None = Field(default=None, max_length=120)


class ExpedienteConsejo(_Modelo):
    """The verified, pseudonymised dossier every member reads (identical for all)."""

    id: str
    # The question rewritten neutrally (no leading wording, no user preference).
    pregunta: str = Field(min_length=10, max_length=1500)
    opciones: list[Opcion] = Field(min_length=2, max_length=MAX_OPCIONES)
    hechos: list[HechoExpediente] = Field(default_factory=list, max_length=80)
    cifras: list[Cifra] = Field(default_factory=list, max_length=200)
    fragment_ids: list[str] = Field(default_factory=list)  # citable sources ([F#]) from the specialists
    # The minimum profile the decision needs («edad: 45», «hijos: 2», «comunidad: Madrid»): no names, no ids,
    # no attribute the decision does not depend on (gender, origin, religion… are dropped unless legally relevant).
    perfil_minimo: dict[str, str] = Field(default_factory=dict)
    # Constraints from the decisions memory («no quiere planes de pensiones», with its date).
    restricciones: list[str] = Field(default_factory=list, max_length=30)
    # Uncertainties the specialists declared (Euríbor path, future income…).
    incertidumbres: list[str] = Field(default_factory=list, max_length=30)
    # SEALED: shown to members only from the revision round on, never in round 1.
    preferencia_usuario: str | None = Field(default=None, max_length=600)
    # What was removed or pseudonymised, for the audit trail (never the original values).
    anonimizado: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _opciones_unicas(self) -> ExpedienteConsejo:
        ids = [o.id for o in self.opciones]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de opción repetidos")
        return self


# --------------------------------------------------------------------------------------------------------------
# Deliberation
# --------------------------------------------------------------------------------------------------------------


class Propuesta(_Modelo):
    """One member's analysis (round 1 or revised). The SAME schema for everyone."""

    miembro_id: str = Field(pattern=_ID_RE)  # the seat (hidden from the other members)
    ronda: Literal[1, 2]
    recomendacion: str  # an option id (o1…) or "depende"
    # When «depende»: on what (the condition that decides between the options).
    condicion: str | None = Field(default=None, max_length=600)
    confianza: float = Field(ge=0.0, le=1.0)
    razonamiento: str = Field(min_length=20, max_length=MAX_TEXTO_PROPUESTA)
    supuestos: list[str] = Field(default_factory=list, max_length=10)
    riesgos: list[str] = Field(default_factory=list, max_length=10)
    que_me_haria_cambiar: list[str] = Field(default_factory=list, max_length=6)
    cifras_usadas: list[str] = Field(default_factory=list)  # Cifra ids of the dossier
    # Round 2 only: what changed after the critiques, and why (or why not).
    cambios: str | None = Field(default=None, max_length=1200)


class PropuestaAnonima(_Modelo):
    """A proposal as another member sees it: labelled, normalised, no author."""

    etiqueta: str = Field(pattern=_ETIQUETA_RE)
    recomendacion: str
    condicion: str | None = None
    confianza: float
    razonamiento: str
    supuestos: list[str]
    riesgos: list[str]
    que_me_haria_cambiar: list[str]
    cifras_usadas: list[str]


class ErrorSenalado(_Modelo):
    tipo: TipoError
    texto: str = Field(min_length=5, max_length=500)


class PuntuacionRubrica(_Modelo):
    """1–5 on each axis (the rubric is fixed so scores are comparable across reviewers)."""

    rigor: int = Field(ge=1, le=5)  # uses the dossier's figures correctly, no invented numbers
    completitud: int = Field(ge=1, le=5)  # weighs every option and the key risks
    prudencia: int = Field(ge=1, le=5)  # proportionate to irreversibility and uncertainty
    claridad: int = Field(ge=1, le=5)
    ajuste_al_caso: int = Field(ge=1, le=5)  # fits THIS household's facts and constraints


class Critica(_Modelo):
    evaluador_id: str = Field(pattern=_ID_RE)
    etiqueta: str = Field(pattern=_ETIQUETA_RE)  # the proposal reviewed (as the reviewer saw it)
    fortalezas: list[str] = Field(default_factory=list, max_length=6)
    debilidades: list[str] = Field(default_factory=list, max_length=6)
    errores: list[ErrorSenalado] = Field(default_factory=list, max_length=8)
    puntuacion: PuntuacionRubrica


class Ranking(_Modelo):
    evaluador_id: str = Field(pattern=_ID_RE)
    orden: list[str]  # labels, best first (the reviewer's own proposal is never among them)


# --------------------------------------------------------------------------------------------------------------
# The ruling
# --------------------------------------------------------------------------------------------------------------


class PosturaOpcion(_Modelo):
    opcion_id: str
    a_favor: list[str] = Field(default_factory=list, max_length=8)  # the strongest arguments, in Spanish
    en_contra: list[str] = Field(default_factory=list, max_length=8)
    miembros: list[str] = Field(default_factory=list)  # seats that recommend it in round 2


class Dictamen(_Modelo):
    sesion_id: str
    recomendacion: str  # option id or "depende"
    condicion: str | None = None
    # The council's confidence: combines the members' confidence and their agreement (disagreement lowers it).
    confianza: float = Field(ge=0.0, le=1.0)
    consenso: Consenso
    # Kendall's W over the final rankings (0 = no agreement, 1 = full agreement); None with < 3 rankings.
    acuerdo: float | None = Field(default=None, ge=0.0, le=1.0)
    resumen: str = Field(min_length=20, max_length=4000)  # Spanish, for the user
    posturas: list[PosturaOpcion] = Field(default_factory=list)
    disenso: str | None = Field(default=None, max_length=2000)  # the minority's best case, when there is one
    supuestos_decisivos: list[str] = Field(default_factory=list, max_length=8)
    sensibilidad: list[str] = Field(default_factory=list, max_length=8)  # «si X, la recomendación cambia a o2»
    datos_que_resolverian: list[str] = Field(default_factory=list, max_length=6)
    puerta: ResultadoPuerta | None = None  # the brain's figures gate on ``resumen`` + ``posturas``
    miembros: list[Miembro] = Field(default_factory=list)  # revealed at the end, for transparency
    # Which seat wrote which round-2 proposal (revealed at the end).
    autoria: dict[str, str] = Field(default_factory=dict)  # label -> seat id
    avisos: list[str] = Field(default_factory=list)


class CosteSesion(_Modelo):
    llamadas: int = Field(default=0, ge=0)
    usd: Decimal = Decimal("0")
    tokens_entrada: int = 0
    tokens_salida: int = 0


class SesionConsejo(_Modelo):
    """One council session, as stored and as the UI shows it."""

    id: str = Field(pattern=r"^cons_[0-9a-f]{32}$")
    case_id: str
    user_id: str | None = None
    estado: EstadoSesion
    fase: FaseConsejo | None = None
    pregunta_usuario: str = Field(max_length=4000)  # as the user asked it (the dossier holds the neutral one)
    creada: datetime
    actualizada: datetime
    estimacion_usd: Decimal  # shown to the user before confirming
    tope_usd: Decimal  # hard cap: the session stops (honestly) if it would exceed it
    coste: CosteSesion = Field(default_factory=CosteSesion)
    expediente: ExpedienteConsejo | None = None
    propuestas: list[Propuesta] = Field(default_factory=list)
    criticas: list[Critica] = Field(default_factory=list)
    rankings: list[Ranking] = Field(default_factory=list)
    dictamen: Dictamen | None = None
    error: str | None = None


# --------------------------------------------------------------------------------------------------------------
# Interfaces between the parts
# --------------------------------------------------------------------------------------------------------------


@runtime_checkable
class MotorDeliberacion(Protocol):
    """Phases 1–4 (``asistente.nucleo.consejo.deliberacion``): given a dossier, deliberate and rule."""

    def deliberar(
        self,
        expediente: ExpedienteConsejo,
        *,
        sesion_id: str,
        emit: Emit | None = None,
        tope_usd: Decimal | None = None,
    ) -> SesionConsejo: ...


# Progress events (for the UI and the audit trail).
EVENTO_FASE = "consejo_fase"  # {"fase", "estado"}
EVENTO_MIEMBRO = "consejo_miembro"  # {"miembro_id", "fase", "estado"}

__all__ = [
    "EVENTO_FASE",
    "EVENTO_MIEMBRO",
    "MAX_MIEMBROS",
    "MAX_OPCIONES",
    "MAX_TEXTO_PROPUESTA",
    "MIN_MIEMBROS",
    "Consenso",
    "CosteSesion",
    "Critica",
    "Dictamen",
    "ErrorSenalado",
    "EstadoSesion",
    "ExpedienteConsejo",
    "FaseConsejo",
    "HechoExpediente",
    "Miembro",
    "MotorDeliberacion",
    "Opcion",
    "PosturaOpcion",
    "Propuesta",
    "PropuestaAnonima",
    "PuntuacionRubrica",
    "Ranking",
    "Rol",
    "SesionConsejo",
    "TipoError",
]

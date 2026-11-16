"""What the assistant remembers of the user's decisions: the data model and its pure rules.

A :class:`Decision` is something the user DECIDED or PREFERS about a kind of proposal — not a fact of their
situation (facts live in the case). Examples: «no quiere planes de pensiones porque necesita liquidez»
(``rechazo`` of ``fiscal:plan_pensiones``), «prefiere declaración conjunta» (``preferencia`` of
``fiscal:tributacion:conjunta``), «no puedo inmovilizar dinero» (``restriccion``), «ya lo hice»
(``aceptacion``).

The **ámbito** names what the decision is about: the module key, then the lever or proposal type, then
optionally what exactly (``luz:cambio_tarifa``, ``luz:cambio_tarifa:<oferta>``, ``fiscal:plan_pensiones:conyuge``).
A decision covers its own ámbito and every narrower one (``fiscal:plan_pensiones`` covers
``fiscal:plan_pensiones:conyuge``), never a broader or a sibling one.

A decision does not last forever. It is in force (``vigente``) until its ``caducidad`` (a default per type)
or until one of its ``condiciones_de_reapertura`` holds: a date passes, a fact of the case changes, or a new
proposal is much better than the one rejected. Then the proposal may come back ONCE, saying what the user
decided and what changed (:func:`evaluar` decides; the inbox and the optimiser apply it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Literal
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TipoDecision = Literal["rechazo", "aceptacion", "preferencia", "restriccion"]
# chat: the user said it in the conversation (agent tool, with the user's literal words);
# boton: «No me interesa» / «Ya lo hice» / «Recuérdamelo más tarde» on a notice or a proposal;
# ajustes: written by the user in «Lo que sé de tus preferencias»;
# accion: the user confirmed or rejected a pending action of a module.
FuenteDecision = Literal["chat", "boton", "ajustes", "accion"]
Boton = Literal["no_me_interesa", "ya_lo_hice", "recuerdamelo"]
TipoCondicion = Literal["fecha", "hecho", "mejora"]
EstadoDecision = Literal["vigente", "reabrible", "caducada", "reabierta", "sustituida"]
MotivoReapertura = Literal["fecha", "hecho", "mejora", "caducidad"]

TIPOS: tuple[TipoDecision, ...] = ("rechazo", "aceptacion", "preferencia", "restriccion")
# Kinds that stop a proposal from being made again while they are in force.
BLOQUEAN_AVISOS: frozenset[str] = frozenset({"rechazo", "restriccion", "aceptacion"})
# Kinds the tax optimiser treats as a constraint. «Ya lo hice» too: it is not proposed again, and the plan
# asks for the amount (it belongs in the case's figures, where the calculator can use it).
BLOQUEAN_OPTIMIZADOR: frozenset[str] = frozenset({"rechazo", "restriccion", "aceptacion"})
# Default lifetime per kind, in months (None: until the user changes it). A rejection is revisited after a
# year; «ya lo hice» after six months (most proposals are yearly: next year's is a new one).
CADUCIDAD_POR_DEFECTO_MESES: dict[str, int | None] = {
    "rechazo": 12,
    "restriccion": 12,
    "aceptacion": 6,
    "preferencia": None,
}
# A new proposal at least this many times better than the rejected one may come back once.
FACTOR_MEJORA_POR_DEFECTO = Decimal(2)
MAX_CONDICIONES = 5
PATRON_AMBITO = r"^[a-z][a-z0-9_]{0,30}(:[A-Za-z0-9_.\-]{1,80}){0,4}$"
_AMBITO = re.compile(PATRON_AMBITO)

ETIQUETAS_TIPO: dict[str, str] = {
    "rechazo": "No le interesa",
    "aceptacion": "Ya lo hizo o lo aceptó",
    "preferencia": "Prefiere",
    "restriccion": "Restricción",
}


def ahora() -> datetime:
    return datetime.now(UTC)


def sumar_meses(dia: date, meses: int) -> date:
    """``dia`` plus whole months, clamped to the last day of the target month (31/01 + 1 → 28 or 29/02)."""
    total = dia.month - 1 + meses
    anio, mes = dia.year + total // 12, total % 12 + 1
    for ultimo in (31, 30, 29, 28):
        try:
            return date(anio, mes, min(dia.day, ultimo))
        except ValueError:
            continue
    raise ValueError(f"fecha imposible: {dia} + {meses} meses")  # pragma: no cover


def caducidad_por_defecto(tipo: str, desde: date) -> date | None:
    meses = CADUCIDAD_POR_DEFECTO_MESES.get(tipo)
    return None if meses is None else sumar_meses(desde, meses)


def ambito_valido(ambito: str) -> bool:
    return bool(_AMBITO.match(ambito))


def cubre(ambito_decision: str, ambito_propuesta: str) -> bool:
    """Whether a decision about ``ambito_decision`` applies to a proposal about ``ambito_propuesta``."""
    return ambito_propuesta == ambito_decision or ambito_propuesta.startswith(ambito_decision + ":")


def modulo_de_ambito(ambito: str) -> str:
    return ambito.split(":", 1)[0]


def huella(valor: Any) -> str | None:
    """A fingerprint of a fact's value (None: the fact was absent).

    A reopening condition on a fact keeps only this, never the value itself: the decision store must not
    become a second copy of the user's data (a fact the user erases is not left behind here). Numbers are
    compared by value (``1`` and ``1.0`` are the same).
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        canonico = json.dumps(valor)
    elif isinstance(valor, int | float | Decimal):
        canonico = format(Decimal(str(valor)).normalize(), "f")
    elif isinstance(valor, str):
        canonico = json.dumps(valor.strip(), ensure_ascii=False)
    else:
        canonico = json.dumps(valor, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:24]


def normalizar_texto(texto: str) -> str:
    """Lower case, no accents, no quotes or punctuation, single spaces: to compare the user's words."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9ñ€%]+", " ", sin_acentos).split())


def fecha_es(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def eur(importe: Decimal) -> str:
    texto = f"{abs(importe):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if importe < 0 else ''}{texto} €"


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condicion(_Modelo):
    """When a decision is worth revisiting before it expires."""

    tipo: TipoCondicion
    # fecha: on or after this day (e.g. the end of a permanence period).
    fecha: date | None = None
    # hecho: when this fact of the case changes from what it was when the user decided.
    hecho: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    huella_al_decidir: str | None = None
    # mejora: when a new proposal is worth more than ``factor`` × ``importe_referencia``.
    factor: Decimal | None = Field(default=None, gt=1, le=100)
    importe_referencia: Decimal | None = Field(default=None, gt=0, le=Decimal("100000000"))
    # In Spanish, for the user: «cuando termine la permanencia (31/03/2027)».
    descripcion: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _coherente(self) -> Condicion:
        if self.tipo == "fecha" and self.fecha is None:
            raise ValueError("una condición de fecha necesita «fecha»")
        if self.tipo == "hecho" and not self.hecho:
            raise ValueError("una condición sobre un dato necesita «hecho» (el nombre del dato)")
        if self.tipo == "mejora":
            if self.importe_referencia is None:
                raise ValueError("una condición de mejora necesita el importe de la propuesta rechazada")
            if self.factor is None:
                self.factor = FACTOR_MEJORA_POR_DEFECTO
        return self

    def texto(self) -> str:
        if self.descripcion:
            return self.descripcion
        if self.tipo == "fecha" and self.fecha is not None:
            return f"a partir del {fecha_es(self.fecha)}"
        if self.tipo == "hecho":
            return f"si cambia «{self.hecho}»"
        factor = self.factor or FACTOR_MEJORA_POR_DEFECTO
        return f"si aparece una propuesta que valga más de {format(factor.normalize(), 'f')} veces " + (
            f"los {eur(self.importe_referencia)} de la rechazada" if self.importe_referencia else "la rechazada"
        )


class Propuesta(_Modelo):
    """What was on the table when the user decided (to reference it when it comes back)."""

    titulo: str = Field(default="", max_length=300)
    importe: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    # The inbox key of the notice the user answered, if any.
    clave_aviso: str | None = Field(default=None, max_length=300)


class Decision(_Modelo):
    id: str = Field(default_factory=lambda: f"dec_{uuid4().hex}", pattern=r"^dec_[0-9a-f]{32}$")
    case_id: str = Field(min_length=1, max_length=200)
    # Owner of the case when it has one (accounts on); None without accounts.
    user_id: str | None = Field(default=None, max_length=200)
    ambito: str = Field(min_length=1, max_length=200)
    tipo: TipoDecision
    # In the user's words (or the button they pressed).
    razon: str = Field(min_length=1, max_length=500)
    fuente: FuenteDecision
    boton: Boton | None = None
    fecha: datetime = Field(default_factory=ahora)
    # Last day... exclusive: the decision is in force while today < caducidad. None: until changed.
    caducidad: date | None = None
    condiciones_de_reapertura: list[Condicion] = Field(default_factory=list, max_length=MAX_CONDICIONES)
    propuesta: Propuesta | None = None
    actualizada: datetime | None = None
    # Set once the proposal came back after a reopening condition (it comes back only once).
    reabierta_el: date | None = None
    reabierta_por: MotivoReapertura | None = None
    reabierta_motivo: str | None = Field(default=None, max_length=500)
    reabierta_clave: str | None = Field(default=None, max_length=300)
    # A newer decision about the same ámbito replaced this one (kept in the history the user can see).
    sustituida_por: str | None = None
    # How many times a proposal was held back because of this decision, and the last time.
    omisiones: int = Field(default=0, ge=0)
    ultima_omision: date | None = None

    @field_validator("ambito")
    @classmethod
    def _ambito(cls, valor: str) -> str:
        valor = valor.strip()
        if not ambito_valido(valor):
            raise ValueError(
                "ámbito no válido: debe ser «módulo:palanca[:detalle]» en minúsculas (p. ej. «fiscal:plan_pensiones»)"
            )
        return valor

    @field_validator("razon")
    @classmethod
    def _razon(cls, valor: str) -> str:
        valor = " ".join(valor.split())
        if not valor:
            raise ValueError("la razón no puede estar vacía")
        return valor

    @property
    def modulo(self) -> str:
        return modulo_de_ambito(self.ambito)


@dataclass(frozen=True)
class Evaluacion:
    estado: EstadoDecision
    # Spanish: why it is not (or no longer) in force — what changed.
    motivo: str | None = None
    por: MotivoReapertura | None = None

    @property
    def vigente(self) -> bool:
        return self.estado == "vigente"


def evaluar(
    decision: Decision,
    *,
    hoy: date,
    hechos: dict[str, Any] | None = None,
    importe: Decimal | None = None,
    etiquetas: dict[str, str] | None = None,
) -> Evaluacion:
    """The state of ``decision`` today, for a proposal worth ``importe`` (when known).

    * ``sustituida``: a newer decision about the same thing replaced it.
    * ``reabierta``: it already let its proposal come back once (after a date, a fact change or its expiry):
      it no longer holds anything back. A reopening for a much better offer is different: the decision stays
      in force for ordinary offers and cannot reopen for a better one again.
    * ``caducada``: past its ``caducidad`` and not reopened yet: the next proposal may come back, once.
    * ``reabrible``: a reopening condition holds: same.
    * ``vigente``: holds proposals back.

    ``hechos`` are the case facts (None: unknown, fact conditions are not checked).
    """
    if decision.sustituida_por:
        return Evaluacion("sustituida")
    if decision.reabierta_el is not None and decision.reabierta_por != "mejora":
        return Evaluacion("reabierta", decision.reabierta_motivo, decision.reabierta_por)
    if decision.caducidad is not None and hoy >= decision.caducidad:
        if decision.boton == "recuerdamelo":
            motivo = f"me pediste que te lo recordara a partir del {fecha_es(decision.caducidad)}"
        else:
            motivo = f"tu decisión era hasta el {fecha_es(decision.caducidad)}"
        return Evaluacion("caducada", motivo, "caducidad")
    etiquetas = etiquetas or {}
    for condicion in decision.condiciones_de_reapertura:
        if condicion.tipo == "fecha" and condicion.fecha is not None and hoy >= condicion.fecha:
            texto = condicion.descripcion or "la fecha que indicaste"
            return Evaluacion("reabrible", f"ha llegado el {fecha_es(condicion.fecha)} ({texto})", "fecha")
        if condicion.tipo == "hecho" and condicion.hecho and hechos is not None:
            if huella(hechos.get(condicion.hecho)) != condicion.huella_al_decidir:
                nombre = etiquetas.get(condicion.hecho, condicion.hecho.replace("_", " "))
                return Evaluacion("reabrible", f"ha cambiado un dato de tu expediente: {nombre}", "hecho")
        if (
            condicion.tipo == "mejora"
            and importe is not None
            and condicion.importe_referencia is not None
            and decision.reabierta_el is None
        ):
            factor = condicion.factor or FACTOR_MEJORA_POR_DEFECTO
            if importe > condicion.importe_referencia * factor:
                return Evaluacion(
                    "reabrible",
                    f"la nueva propuesta supone {eur(importe)}, más de {format(factor.normalize(), 'f')} veces los "
                    f"{eur(condicion.importe_referencia)} de la que rechazaste",
                    "mejora",
                )
    return Evaluacion("vigente")


def descripcion_corta(decision: Decision, etiqueta: str | None = None) -> str:
    """One Spanish line: what, the user's words, when."""
    que = etiqueta or decision.ambito
    linea = f"{ETIQUETAS_TIPO[decision.tipo]}: {que} — «{decision.razon}» ({fecha_es(decision.fecha.date())}"
    if decision.caducidad is not None:
        linea += f"; hasta el {fecha_es(decision.caducidad)}"
    linea += ")"
    if decision.condiciones_de_reapertura:
        linea += "; se puede revisar " + "; ".join(c.texto() for c in decision.condiciones_de_reapertura)
    return linea


__all__ = [
    "BLOQUEAN_AVISOS",
    "BLOQUEAN_OPTIMIZADOR",
    "CADUCIDAD_POR_DEFECTO_MESES",
    "ETIQUETAS_TIPO",
    "FACTOR_MEJORA_POR_DEFECTO",
    "PATRON_AMBITO",
    "TIPOS",
    "Boton",
    "Condicion",
    "Decision",
    "EstadoDecision",
    "Evaluacion",
    "FuenteDecision",
    "Propuesta",
    "TipoDecision",
    "ambito_valido",
    "caducidad_por_defecto",
    "cubre",
    "descripcion_corta",
    "evaluar",
    "huella",
    "modulo_de_ambito",
    "normalizar_texto",
    "sumar_meses",
]

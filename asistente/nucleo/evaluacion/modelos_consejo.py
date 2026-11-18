"""Data model of the household council's evaluation cases (``docs/consejo/evaluacion.md``).

A council case (category ``decision`` or ``sesgo``, ``requiere: ["consejo"]``) carries a **dossier fixture**
(:class:`FixtureConsejo`): the neutral question, the options, the facts and the figures WITH their provenance (the
specialist, the tool, the cited fragment), the cited fragments themselves, the minimum profile, the hard constraints
from the decisions memory, the uncertainties and the user's (sealed) preference. It is exactly what the brain's
instruction phase would hand the council, written by hand so the case needs no specialist and no corpus.

Its expectations are deterministic (:class:`ExpDictamen`, :class:`ExpInvarianza`): which recommendations are right,
which options break a hard constraint, which key risks the ruling must surface (each a canonical phrase plus a
regex that must match it), whether the facts are genuinely balanced (then the ruling must show the other side and
must not be overconfident), whether the right answer is «depende» on a named condition, and — always — no figure
that is not in the dossier and no law that is not in the dossier.

A ``sesgo`` case adds counterfactual variants (:class:`VarianteContrafactual`): the same dossier with protected
attributes the decision does not depend on (name, gender, origin, religion…). The ruling must not change.

This module only imports the shared contracts: :mod:`.modelos` imports it for the case schema.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from asistente.nucleo.cerebro.contrato import Cifra, ResultadoPuerta
from asistente.nucleo.consejo.contrato import (
    Consenso,
    ExpedienteConsejo,
    HechoExpediente,
    Opcion,
    PosturaOpcion,
)

# Attributes a household decision of this suite never depends on (G's pseudonymiser drops them; the bias harness
# checks what happens when they slip through). Age, children, income, region… are NOT here: they can matter.
ATRIBUTOS_PROTEGIDOS = frozenset(
    {"nombre", "genero", "origen", "nacionalidad", "religion", "etnia", "orientacion", "idioma_materno"}
)
RECOMENDACION_DEPENDE = "depende"
_ID_RIESGO_RE = r"^[a-z][a-z0-9_]{1,40}$"


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _regex(patron: str) -> re.Pattern[str]:
    try:
        return re.compile(patron, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"expresión regular no válida «{patron}»: {exc}") from exc


# --------------------------------------------------------------------------- #
# The dossier fixture
# --------------------------------------------------------------------------- #


class FragmentoExpediente(_Modelo):
    """A citable source a specialist brought (a BOE literal, a tool output): ``[F#]`` in the dossier."""

    id: str = Field(pattern=r"^F[1-9][0-9]{0,2}$")
    titulo: str = Field(min_length=3, max_length=200)
    texto: str = Field(min_length=10, max_length=3000)
    autoridad: str = Field(default="BOE", max_length=40)


class Preferencia(_Modelo):
    """What the user said they would like (sealed in round 1: members do not see it until the revision)."""

    opcion: str = Field(pattern=r"^o[1-9]$")
    texto: str = Field(min_length=5, max_length=500)


class FixtureExpediente(_Modelo):
    """The dossier as the brain's instruction phase would build it (see :class:`ExpedienteConsejo`)."""

    pregunta: str = Field(min_length=10, max_length=1500)
    opciones: list[Opcion] = Field(min_length=2, max_length=6)
    hechos: list[HechoExpediente] = Field(default_factory=list)
    cifras: list[Cifra] = Field(default_factory=list)
    fragmentos: list[FragmentoExpediente] = Field(default_factory=list)
    perfil_minimo: dict[str, str] = Field(default_factory=dict)
    restricciones: list[str] = Field(default_factory=list)
    incertidumbres: list[str] = Field(default_factory=list)
    preferencia: Preferencia | None = None

    @model_validator(mode="after")
    def _coherente(self) -> FixtureExpediente:
        opciones = {o.id for o in self.opciones}
        if len(opciones) != len(self.opciones):
            raise ValueError("ids de opción repetidos")
        if self.preferencia is not None and self.preferencia.opcion not in opciones:
            raise ValueError(f"la preferencia apunta a una opción que no existe ({self.preferencia.opcion})")
        ids = [f.id for f in self.fragmentos]
        if ids != [f"F{n}" for n in range(1, len(ids) + 1)]:
            raise ValueError("los fragmentos deben numerarse F1, F2… en orden (son los ids del registro de pruebas)")
        cifras = [c.id for c in self.cifras]
        if len(set(cifras)) != len(cifras):
            raise ValueError("ids de cifra repetidos")
        for cifra in self.cifras:
            if cifra.fragment_id is not None and cifra.fragment_id not in ids:
                raise ValueError(f"la cifra {cifra.id} cita {cifra.fragment_id}, que no está entre los fragmentos")
        protegidos = sorted(set(self.perfil_minimo) & ATRIBUTOS_PROTEGIDOS)
        if protegidos:
            raise ValueError(f"el perfil mínimo no puede llevar atributos protegidos: {', '.join(protegidos)}")
        return self

    def a_expediente(self, expediente_id: str, *, con_preferencia: bool = True) -> ExpedienteConsejo:
        """The contract's dossier (the preference is SEALED inside it: the engine decides when members see it)."""
        preferencia = None
        if con_preferencia and self.preferencia is not None:
            preferencia = texto_preferencia(self.preferencia)
        return ExpedienteConsejo(
            id=expediente_id,
            pregunta=self.pregunta,
            opciones=list(self.opciones),
            hechos=list(self.hechos),
            cifras=list(self.cifras),
            fragment_ids=[f.id for f in self.fragmentos],
            perfil_minimo=dict(self.perfil_minimo),
            restricciones=list(self.restricciones),
            incertidumbres=list(self.incertidumbres),
            preferencia_usuario=preferencia,
        )


def texto_preferencia(preferencia: Preferencia) -> str:
    """The sealed preference as the dossier carries it: the option id plus the user's own words."""
    return f"La persona prefiere la opción {preferencia.opcion}: «{preferencia.texto}»"


class VarianteContrafactual(_Modelo):
    """The same dossier with protected attributes the decision does not depend on."""

    id: str = Field(pattern=r"^[a-z0-9_]{1,30}$")
    atributos: dict[str, str] = Field(min_length=1)
    # How the person introduces themself in the question when the attributes slip through («Me llamo …»).
    presentacion: str = Field(min_length=5, max_length=300)

    @field_validator("atributos")
    @classmethod
    def _protegidos(cls, valor: dict[str, str]) -> dict[str, str]:
        ajenos = sorted(set(valor) - ATRIBUTOS_PROTEGIDOS)
        if ajenos:
            raise ValueError(
                f"atributos no protegidos en una variante contrafactual: {', '.join(ajenos)} (solo "
                f"{', '.join(sorted(ATRIBUTOS_PROTEGIDOS))})"
            )
        return valor


class FixtureConsejo(_Modelo):
    """A council case's dossier, its domain and, for ``sesgo`` cases, its counterfactual variants."""

    dominio: str = Field(pattern=r"^[a-z_]{3,30}$")
    expediente: FixtureExpediente
    contrafactuales: list[VarianteContrafactual] = Field(default_factory=list)

    @model_validator(mode="after")
    def _variantes(self) -> FixtureConsejo:
        ids = [v.id for v in self.contrafactuales]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de variante contrafactual repetidos")
        if len(self.contrafactuales) == 1:
            raise ValueError("una prueba contrafactual necesita al menos dos variantes")
        return self


# --------------------------------------------------------------------------- #
# Expectations on the ruling
# --------------------------------------------------------------------------- #


class RiesgoClave(_Modelo):
    """A risk or trade-off the ruling must surface: a canonical phrase and a regex that must match the ruling.

    The phrase must match its own regex (checked when the case loads): the reference ruling the scripted run uses
    is built from these phrases, which proves every expectation is satisfiable.
    """

    id: str = Field(pattern=_ID_RIESGO_RE)
    frase: str = Field(min_length=10, max_length=300)
    patron: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def _satisfacible(self) -> RiesgoClave:
        if not _regex(self.patron).search(self.frase):
            raise ValueError(f"el riesgo «{self.id}»: su frase no cumple su propio patrón")
        return self


class Depende(_Modelo):
    """The right answer is «depende», and on THIS condition (a phrase and the regex the condition must match)."""

    frase: str = Field(min_length=10, max_length=400)
    patron: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def _satisfacible(self) -> Depende:
        if not _regex(self.patron).search(self.frase):
            raise ValueError("la condición de «depende»: su frase no cumple su propio patrón")
        return self


class ExpDictamen(_Modelo):
    """Deterministic rubric of a ruling (the council's or the single-pass brain's: the same for both)."""

    tipo: Literal["dictamen"]
    # Recommendations considered right (option ids and/or «depende»); empty = any (only the other checks apply).
    admitidas: list[str] = Field(default_factory=list)
    # Options that break a HARD constraint of the decisions memory: recommending one is a serious failure.
    prohibidas: list[str] = Field(default_factory=list)
    motivo_prohibidas: str = ""
    riesgos: list[RiesgoClave] = Field(default_factory=list)
    # The facts are genuinely balanced: the ruling must show the other side (dissent, or both options argued) and
    # its confidence must not exceed ``confianza_max_equilibrado``.
    equilibrado: bool = False
    confianza_max_equilibrado: float = Field(default=0.75, gt=0.0, le=1.0)
    depende: Depende | None = None
    sin_cifras_inventadas: bool = True
    sin_ley_ajena: bool = True

    @model_validator(mode="after")
    def _coherente(self) -> ExpDictamen:
        if set(self.admitidas) & set(self.prohibidas):
            raise ValueError("una opción no puede estar admitida y prohibida a la vez")
        ids = [r.id for r in self.riesgos]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de riesgo repetidos")
        if self.depende is not None and self.admitidas and RECOMENDACION_DEPENDE not in self.admitidas:
            raise ValueError("con «depende», «depende» debe estar entre las recomendaciones admitidas")
        return self


class ExpInvarianza(_Modelo):
    """Counterfactual fairness of one ``sesgo`` case: every variant gets the same ruling."""

    tipo: Literal["invarianza"]
    misma_recomendacion: bool = True
    # Largest |confidence(variant) − confidence(base)| tolerated.
    tolerancia_confianza: float = Field(default=0.15, ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# What a decision run produced (``Ejecucion.consejo``)
# --------------------------------------------------------------------------- #

SistemaDecision = Literal["consejo", "un_paso"]


class RespuestaDecision(_Modelo):
    """A ruling, normalised so the council and the single-pass brain are scored by the very same rubric."""

    sistema: SistemaDecision
    recomendacion: str
    condicion: str | None = None
    confianza: float = Field(ge=0.0, le=1.0)
    consenso: Consenso | None = None  # the council only
    acuerdo: float | None = Field(default=None, ge=0.0, le=1.0)  # Kendall's W (the council only)
    resumen: str = ""
    posturas: list[PosturaOpcion] = Field(default_factory=list)
    disenso: str | None = None
    supuestos_decisivos: list[str] = Field(default_factory=list)
    sensibilidad: list[str] = Field(default_factory=list)
    datos_que_resolverian: list[str] = Field(default_factory=list)
    # The members' round-2 confidences (council) — for calibration and conformity.
    confianzas_miembros: list[float] = Field(default_factory=list)
    # The figures gate of the evaluation on :meth:`texto` (same gate as the brain, the dossier as the only source).
    puerta: ResultadoPuerta | None = None
    avisos: list[str] = Field(default_factory=list)

    def texto(self) -> str:
        """Everything the user reads of the ruling (what the rubric's text checks run on). The confidence is not
        rendered as a number: it is a field, not a figure of the decision."""
        lineas = [f"Recomendación: {self.recomendacion}"]
        if self.condicion:
            lineas.append(f"Condición: {self.condicion}")
        if self.resumen:
            lineas.append(self.resumen)
        for postura in self.posturas:
            if postura.a_favor:
                lineas.append(f"A favor de {postura.opcion_id}: " + " ".join(postura.a_favor))
            if postura.en_contra:
                lineas.append(f"En contra de {postura.opcion_id}: " + " ".join(postura.en_contra))
        if self.disenso:
            lineas.append(f"Disenso: {self.disenso}")
        for titulo, items in (
            ("Supuestos decisivos", self.supuestos_decisivos),
            ("Sensibilidad", self.sensibilidad),
            ("Datos que resolverían la duda", self.datos_que_resolverian),
        ):
            if items:
                lineas.append(f"{titulo}: " + " ".join(items))
        return "\n".join(lineas)


class ResultadoVariante(_Modelo):
    """The ruling of one counterfactual variant (``placebo``: the base dossier run again — the noise floor)."""

    id: str
    recomendacion: str
    confianza: float = Field(ge=0.0, le=1.0)
    placebo: bool = False


class SalidaConsejo(_Modelo):
    """``Ejecucion.consejo``: the ruling, the counterfactual variants and, for the council, its session."""

    respuesta: RespuestaDecision
    variantes: list[ResultadoVariante] = Field(default_factory=list)
    # The council's ``SesionConsejo`` (JSON), for the audit trail, calibration and the bias harness.
    sesion: dict[str, Any] | None = None


__all__ = [
    "ATRIBUTOS_PROTEGIDOS",
    "RECOMENDACION_DEPENDE",
    "Depende",
    "ExpDictamen",
    "ExpInvarianza",
    "FixtureConsejo",
    "FixtureExpediente",
    "FragmentoExpediente",
    "Preferencia",
    "RespuestaDecision",
    "ResultadoVariante",
    "RiesgoClave",
    "SalidaConsejo",
    "SistemaDecision",
    "VarianteContrafactual",
    "texto_preferencia",
]

"""Data model of the evaluation suite: cases, expectations, rubrics, runs and reports.

A suite case is a JSON file under ``asistente/nucleo/evaluacion/casos/``. It carries:

* the question (and optional chat history), the household modules switched on and a pseudonymised
  snapshot of the case facts needed to reproduce it;
* **deterministic expectations** — which tool or module must (not) be called, which figure must appear
  (computed by re-running a deterministic calculator on the snapshot, never written by hand nor taken
  from a model), which claims must not appear, how many citations are required, which modes are
  acceptable, whether an ``actua`` call must stay pending;
* an optional **rubric** for an LLM judge, gated deterministically (see :mod:`asistente.nucleo.evaluacion.juez`);
* an optional **script** (``guion``) of a reference run, replayed without any model in CI to prove the case
  is satisfiable and the harness, tools and specialists work end to end.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from asistente.nucleo.cerebro.contrato import (
    InformeCerebro,
    RecuerdoMemoria,
    RutaRespuesta,
    TipoIntencion,
    TipoViolacion,
)


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _regex_valida(patron: str) -> str:
    try:
        re.compile(patron)
    except re.error as exc:
        raise ValueError(f"expresión regular no válida «{patron}»: {exc}") from exc
    return patron


# --------------------------------------------------------------------------- #
# Expected figures: a recipe, not a number
# --------------------------------------------------------------------------- #


class CalculoEsperado(_Modelo):
    """How to obtain an expected figure: run ``herramienta`` with ``argumentos`` and read ``patron``.

    ``herramienta`` is a tax tool of the central agent (``calcular_irpf``, ``calcular_modelo_303``…) or a
    module tool (``hipoteca_cuadro``, ``luz_calcular_factura``…). It runs on the case snapshot unless
    ``con_expediente`` is false. ``patron`` is applied to the tool's text output (multiline) and must have a
    named group ``cifra``. ``registrada`` is only the figure the tool gave when the case was written: a
    mismatch is reported as drift (a parameter or calculator changed) but the expectation is always the
    freshly computed figure.
    """

    herramienta: str = Field(min_length=1)
    argumentos: dict[str, Any] = Field(default_factory=dict)
    patron: str = Field(min_length=1)
    con_expediente: bool = True
    registrada: str | None = None

    @field_validator("patron")
    @classmethod
    def _patron(cls, value: str) -> str:
        _regex_valida(value)
        if "(?P<cifra>" not in value:
            raise ValueError("el patrón debe tener un grupo con nombre «cifra»: (?P<cifra>…)")
        return value


# --------------------------------------------------------------------------- #
# Deterministic expectations (discriminated by ``tipo``)
# --------------------------------------------------------------------------- #


class ExpHerramientaLlamada(_Modelo):
    tipo: Literal["herramienta_llamada"]
    herramienta: str = Field(min_length=1)
    # "asesor" (the central agent) or "especialista_<clave>"; None = anyone.
    agente: str | None = None
    min_veces: int = Field(default=1, ge=1)


class ExpHerramientaNoLlamada(_Modelo):
    tipo: Literal["herramienta_no_llamada"]
    herramienta: str = Field(min_length=1)


class ExpModuloConsultado(_Modelo):
    tipo: Literal["modulo_consultado"]
    modulo: str = Field(min_length=1)


class ExpModuloNoConsultado(_Modelo):
    tipo: Literal["modulo_no_consultado"]
    modulo: str = Field(min_length=1)


class ExpCifra(_Modelo):
    """The figure the calculator gives must appear in the answer."""

    tipo: Literal["cifra"]
    # Referenced from a script as ``{cifra:<nombre>}``.
    nombre: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    calculo: CalculoEsperado


class ExpSumaAusente(_Modelo):
    """The sum of figures computed by different modules/tools must NOT appear (no adding across modules)."""

    tipo: Literal["suma_ausente"]
    calculos: list[CalculoEsperado] = Field(min_length=2)
    motivo: str = ""


class ExpTextoAusente(_Modelo):
    """A claim that must not appear (case-insensitive regex)."""

    tipo: Literal["texto_ausente"]
    patron: str = Field(min_length=1)
    motivo: str = ""

    @field_validator("patron")
    @classmethod
    def _patron(cls, value: str) -> str:
        return _regex_valida(value)


class ExpTextoPresente(_Modelo):
    tipo: Literal["texto_presente"]
    patron: str = Field(min_length=1)
    motivo: str = ""

    @field_validator("patron")
    @classmethod
    def _patron(cls, value: str) -> str:
        return _regex_valida(value)


class ExpCitas(_Modelo):
    """At least ``minimo`` citations in the answer (optionally of one authority, e.g. ``CALCULADORA``)."""

    tipo: Literal["citas"]
    minimo: int = Field(default=1, ge=1)
    autoridad: str | None = None


class ExpModo(_Modelo):
    tipo: Literal["modo"]
    permitidos: list[Literal["verified", "partially_verified", "unverified", "clarification", "abstained"]] = Field(
        min_length=1
    )


class ExpAccionPendiente(_Modelo):
    """An ``actua`` tool call must have been turned into a pending action (never executed)."""

    tipo: Literal["accion_pendiente"]
    herramienta: str = Field(min_length=1)


class ExpSinAccionPendiente(_Modelo):
    tipo: Literal["sin_accion_pendiente"]


class ExpCifraAusente(_Modelo):
    """A figure that must NOT appear (any Spanish formatting): the wrong number a user reported, for instance.

    Unlike an expected figure, a forbidden one may be written by hand: it is the mistake, not the truth."""

    tipo: Literal["cifra_ausente"]
    valor: str = Field(min_length=1, max_length=40)
    motivo: str = ""


# --- the household brain (they read the turn's InformeCerebro; the case must ``requiere`` "cerebro") -------- #


class ExpEspecialistas(_Modelo):
    """Which specialists the brain involved: in its plan (``plan``) or in the reports it got (``informes``)."""

    tipo: Literal["especialistas"]
    incluye: list[str] = Field(default_factory=list)
    excluye: list[str] = Field(default_factory=list)
    # True: exactly ``incluye``, nobody else.
    exactamente: bool = False
    fuente: Literal["plan", "informes"] = "plan"


class ExpIntencion(_Modelo):
    """How the brain understood the request: the kind, a clarification question, parallel or dependent work."""

    tipo: Literal["intencion"]
    tipos: list[TipoIntencion] = Field(default_factory=list)
    # True: it must ask ONE clarification question (and plan nothing); False: it must not ask.
    aclaracion: bool | None = None
    # True: two or more encargos and none waits for another; False: at least one waits for another.
    paralelo: bool | None = None
    # [especialista que espera, especialista del que depende] pairs that must be in the plan.
    dependencias: list[tuple[str, str]] = Field(default_factory=list)


class ExpRuta(_Modelo):
    """The path of the answer: ``directa`` (the brain alone), ``paso_directo`` (the tax advisor's own answer),
    ``sintesis`` (several reports merged)."""

    tipo: Literal["ruta"]
    permitidas: list[RutaRespuesta] = Field(min_length=1)


class ExpFlujoDato(_Modelo):
    """A datum of specialist ``de`` reaches specialist ``a`` THROUGH the brain: a ``pedir_dato`` request (a→de) or
    an encargo of ``a`` that waits for one of ``de`` — either is a correct way to collaborate."""

    tipo: Literal["flujo_dato"]
    de: str = Field(min_length=1)
    a: str = Field(min_length=1)
    via: Literal["cualquiera", "peticion", "dependencia"] = "cualquiera"


class ExpPuerta(_Modelo):
    """The figures gate's verdict on the final answer (after its revisions)."""

    tipo: Literal["puerta"]
    sin_violaciones: bool = True
    tipos_prohibidos: list[TipoViolacion] = Field(default_factory=list)
    min_atribuciones: int = Field(default=0, ge=0)


class ExpCifraAtribuida(_Modelo):
    """The calculator's figure named ``nombre`` (an ``ExpCifra`` of the same case) appears AND the gate attributes
    it to ``especialista`` (procedencia: which figure comes from whom)."""

    tipo: Literal["cifra_atribuida"]
    nombre: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    especialista: str = Field(min_length=1)


class ExpRecuerdos(_Modelo):
    """Which remembered items (``memoria`` of the case) the brain used for the answer."""

    tipo: Literal["recuerdos"]
    usados_incluye: list[str] = Field(default_factory=list)
    usados_excluye: list[str] = Field(default_factory=list)
    ninguno: bool = False


class ExpRepaso(_Modelo):
    """The proactive pass: every claim and proposal grounded in an input signal, nothing invented."""

    tipo: Literal["repaso"]
    afirmaciones_fundadas: bool = True
    propuestas_fundadas: bool = True
    # Signal ids the summary must cover / must leave out.
    menciona: list[str] = Field(default_factory=list)
    no_menciona: list[str] = Field(default_factory=list)
    # When set, only these signals may carry a proposal.
    propuestas_solo_de: list[str] | None = None
    max_propuestas: int | None = Field(default=None, ge=0)


Expectativa = Annotated[
    ExpHerramientaLlamada
    | ExpHerramientaNoLlamada
    | ExpModuloConsultado
    | ExpModuloNoConsultado
    | ExpCifra
    | ExpSumaAusente
    | ExpTextoAusente
    | ExpTextoPresente
    | ExpCitas
    | ExpModo
    | ExpAccionPendiente
    | ExpSinAccionPendiente
    | ExpCifraAusente
    | ExpEspecialistas
    | ExpIntencion
    | ExpRuta
    | ExpFlujoDato
    | ExpPuerta
    | ExpCifraAtribuida
    | ExpRecuerdos
    | ExpRepaso,
    Field(discriminator="tipo"),
]
# Expectation types that read the brain's report: a case that has one must ``requiere`` "cerebro".
EXPECTATIVAS_DEL_CEREBRO = (
    ExpEspecialistas,
    ExpIntencion,
    ExpRuta,
    ExpFlujoDato,
    ExpPuerta,
    ExpCifraAtribuida,
    ExpRecuerdos,
)


# --------------------------------------------------------------------------- #
# Rubric, script, snapshot, origin
# --------------------------------------------------------------------------- #


class Rubrica(_Modelo):
    """Criteria an LLM judge checks. A criterion is met only if the judge quotes the answer literally."""

    criterios: list[str] = Field(min_length=1)
    # Expected conclusion (e.g. the DGT's answer) the judge compares with, when there is one.
    referencia: str | None = None


class LlamadaGuion(_Modelo):
    nombre: str = Field(min_length=1)
    argumentos: dict[str, Any] = Field(default_factory=dict)


class PasoGuion(_Modelo):
    """One model turn of a reference run: tool calls, or the final text.

    The text may use ``{cifra:<nombre>}`` (the figure computed for that expectation) and
    ``{fragmento:<herramienta>}`` (the id of the latest fragment that tool produced in this conversation).
    """

    herramientas: list[LlamadaGuion] = Field(default_factory=list)
    texto: str | None = None

    @model_validator(mode="after")
    def _uno(self) -> PasoGuion:
        if bool(self.herramientas) == (self.texto is not None):
            raise ValueError("cada paso del guion lleva «herramientas» o «texto», no ambos ni ninguno")
        return self


class Expediente(_Modelo):
    """Pseudonymised snapshot of the case: only the facts the answer used."""

    tax_period: str = "2025"
    jurisdiction: str = "ES"
    hechos: dict[str, Any] = Field(default_factory=dict)


class Origen(_Modelo):
    # «feedback»: a user's correction; «senal»: an automatic signal of a real turn (both: private, never in git).
    tipo: Literal["sintetico", "dgt", "feedback", "senal"]
    # DGT consulta number, or the (non-reversible) reference of the reviewed feedback.
    referencia: str | None = None
    etiquetas: list[str] = Field(default_factory=list)
    revisado_por: str | None = None
    revisado_el: date | None = None


class RecuerdoCaso(RecuerdoMemoria):
    """A remembered item the case's memory holds, and whose it is (``usuario``: the case's user unless said)."""

    usuario: str | None = None


class SenalRepaso(_Modelo):
    """One new thing the proactive pass reads (mail, event, deadline, BOE change, decision), as data."""

    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    tipo: str = Field(min_length=1, max_length=40)
    titulo: str = Field(min_length=1, max_length=300)
    detalle: str = Field(default="", max_length=2000)
    fecha: date | None = None
    importe: str | None = None


class EntradaRepaso(_Modelo):
    hoy: date
    senales: list[SenalRepaso] = Field(min_length=1)


Requisito = Literal["cerebro", "repaso", "recuerdos"]
CATEGORIA_RE = r"^[a-z0-9_]+$"


class CasoEvaluacion(_Modelo):
    id: str = Field(min_length=3, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    # Bumped whenever the case changes, so reports say which version of a case they ran.
    version: int = Field(default=1, ge=1)
    categoria: str = Field(pattern=CATEGORIA_RE)
    descripcion: str = ""
    pregunta: str = Field(min_length=1)
    historial: list[dict[str, str]] = Field(default_factory=list)
    # Household modules switched on besides the tax one (``prueba_actua`` is the synthetic module of
    # :mod:`asistente.nucleo.evaluacion.modulo_prueba`).
    modulos: list[str] = Field(default_factory=list)
    expediente: Expediente | None = None
    expectativas: list[Expectativa] = Field(default_factory=list)
    rubrica: Rubrica | None = None
    guion: dict[str, list[PasoGuion]] | None = None
    # DGT consulta hidden from retrieval while this case runs (the answer key).
    ocultar_fuente: str | None = None
    origen: Origen = Field(default_factory=lambda: Origen(tipo="sintetico"))
    # Code of the household-brain packages the case needs (asistente.nucleo.cerebro, .repaso, .recuerdos): while
    # it is not installed the runner skips the case with the reason (see ejecutores.py).
    requiere: list[Requisito] = Field(default_factory=list)
    # The user the case runs as (memory cases: the memory belongs to someone).
    usuario: str = Field(default="evaluacion", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    # What the conversation memory holds for this case (its own items, and other users' that must never show).
    memoria: list[RecuerdoCaso] = Field(default_factory=list)
    # The input of the proactive pass (``requiere`` "repaso").
    entrada_repaso: EntradaRepaso | None = None

    @model_validator(mode="after")
    def _coherente(self) -> CasoEvaluacion:
        if not self.expectativas and self.rubrica is None:
            raise ValueError("un caso necesita al menos una expectativa o una rúbrica")
        if any(isinstance(e, EXPECTATIVAS_DEL_CEREBRO) for e in self.expectativas) and "cerebro" not in self.requiere:
            raise ValueError('las expectativas del cerebro necesitan «requiere»: ["cerebro"]')
        if any(isinstance(e, ExpRepaso) for e in self.expectativas) and (
            "repaso" not in self.requiere or self.entrada_repaso is None
        ):
            raise ValueError('una expectativa «repaso» necesita «requiere»: ["repaso"] y «entrada_repaso»')
        cifras = {e.nombre for e in self.expectativas if isinstance(e, ExpCifra)}
        for e in self.expectativas:
            if isinstance(e, ExpCifraAtribuida) and e.nombre not in cifras:
                raise ValueError(f"«cifra_atribuida» nombra la cifra «{e.nombre}», que no es una cifra esperada")
        ids = [r.id for r in self.memoria]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de recuerdo repetidos en «memoria»")
        nombres = [e.nombre for e in self.expectativas if isinstance(e, ExpCifra)]
        if len(nombres) != len(set(nombres)):
            raise ValueError("los nombres de las cifras esperadas deben ser únicos")
        if self.guion is not None:
            if "asesor" not in self.guion:
                raise ValueError("el guion necesita la conversación «asesor»")
            for clave in self.guion:
                if clave != "asesor" and not re.fullmatch(r"especialista:[a-z0-9_]+", clave):
                    raise ValueError(f"conversación de guion desconocida «{clave}» (asesor | especialista:<clave>)")
        return self

    @property
    def deterministico(self) -> bool:
        """True when every check of the case needs no model (no rubric)."""
        return self.rubrica is None


class CategoriaSuite(_Modelo):
    descripcion: str
    critica: bool = False


class ConfigSuite(_Modelo):
    """``casos/suite.json``: the suite's version and its categories (the critical ones gate promotions)."""

    version: str = Field(min_length=1)
    categorias: dict[str, CategoriaSuite]


# --------------------------------------------------------------------------- #
# A run of the agent on a case, and its assessment
# --------------------------------------------------------------------------- #


class LlamadaRegistrada(_Modelo):
    herramienta: str
    agente: str = "asesor"
    argumentos: dict[str, Any] = Field(default_factory=dict)
    resumen: str = ""
    es_error: bool = False


class CitaRegistrada(_Modelo):
    fragment_id: str
    autoridad: str | None = None


class Ejecucion(_Modelo):
    """What the system under test did on one case."""

    respuesta: str = ""
    modo: str = "error"
    herramientas: list[LlamadaRegistrada] = Field(default_factory=list)
    citas: list[CitaRegistrada] = Field(default_factory=list)
    acciones_pendientes: list[str] = Field(default_factory=list)
    error: str | None = None
    latencia_s: float = 0.0
    llamadas_modelo: int = 0
    tokens: dict[str, int] = Field(default_factory=dict)
    coste_usd: Decimal | None = None
    llamadas_sin_coste: int = 0
    # What the household brain did (its InformeCerebro), when the case ran through it.
    cerebro: InformeCerebro | None = None
    # The proactive pass's output as the evaluation reads it (``SalidaRepaso``), for «repaso» cases.
    repaso: dict[str, Any] | None = None


class VeredictoJuez(_Modelo):
    criterio: str
    cumple: bool
    cita: str = ""
    motivo: str = ""
    # False when the judge said «cumple» without a literal quote of the answer: then it does not count.
    cita_valida: bool = True


class ResultadoCaso(_Modelo):
    id: str
    version: int
    categoria: str
    critica: bool
    aprobado: bool
    # With repetitions, a case passes only if every repetition passes.
    repeticiones_aprobadas: int = 0
    fallos: list[str] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    juez: list[VeredictoJuez] = Field(default_factory=list)
    ejecucion: Ejecucion
    # Not run (its code is not installed yet, or a replay without a script): out of every rate, with the reason.
    omitido: bool = False
    motivo_omision: str | None = None


class ResumenCategoria(_Modelo):
    total: int = 0
    aprobados: int = 0
    tasa: float = 0.0
    critica: bool = False


class ResumenInforme(_Modelo):
    total: int
    aprobados: int
    tasa: float
    por_categoria: dict[str, ResumenCategoria]
    errores: int = 0
    coste_usd: Decimal = Decimal(0)
    llamadas_sin_coste: int = 0
    llamadas_modelo: int = 0
    latencia_media_s: float = 0.0
    latencia_p95_s: float = 0.0
    juez_sin_cita: int = 0
    # Cases not run, by reason (they count nowhere else).
    omitidos: int = 0
    omitidos_por_motivo: dict[str, int] = Field(default_factory=dict)


class Comparacion(_Modelo):
    frente_a: str
    regresiones: list[str] = Field(default_factory=list)
    mejoras: list[str] = Field(default_factory=list)
    delta_tasa: float = 0.0
    delta_por_categoria: dict[str, float] = Field(default_factory=dict)
    delta_coste_usd: Decimal = Decimal(0)


class InformeEvaluacion(_Modelo):
    variante: str
    descripcion_variante: str = ""
    proveedor: str
    modelo: str
    prompts: dict[str, str] = Field(default_factory=dict)
    # SHA-256 of each prompt text the run used: ties a report to the exact prompt it evaluated.
    huellas_prompts: dict[str, str] = Field(default_factory=dict)
    modo: Literal["modelo", "reproduccion"]
    suite_version: str
    suite_huella: str
    generado_el: datetime
    repeticiones: int = 1
    resumen: ResumenInforme
    casos: list[ResultadoCaso]
    comparacion: Comparacion | None = None
    # True when some case did not run (budget exhausted): such a report can never justify a promotion.
    incompleto: bool = False
    avisos: list[str] = Field(default_factory=list)
    # Accepted real cases (private) were part of the run: keep the report out of the repository.
    con_casos_reales: int = 0


__all__ = [
    "EXPECTATIVAS_DEL_CEREBRO",
    "CalculoEsperado",
    "CasoEvaluacion",
    "CategoriaSuite",
    "CitaRegistrada",
    "Comparacion",
    "ConfigSuite",
    "Ejecucion",
    "ExpAccionPendiente",
    "EntradaRepaso",
    "ExpCifra",
    "ExpCifraAtribuida",
    "ExpCifraAusente",
    "ExpCitas",
    "ExpEspecialistas",
    "ExpFlujoDato",
    "ExpIntencion",
    "ExpPuerta",
    "ExpRecuerdos",
    "ExpRepaso",
    "ExpRuta",
    "ExpHerramientaLlamada",
    "ExpHerramientaNoLlamada",
    "ExpModo",
    "ExpModuloConsultado",
    "ExpModuloNoConsultado",
    "ExpSinAccionPendiente",
    "ExpSumaAusente",
    "ExpTextoAusente",
    "ExpTextoPresente",
    "Expectativa",
    "Expediente",
    "InformeEvaluacion",
    "LlamadaGuion",
    "LlamadaRegistrada",
    "Origen",
    "PasoGuion",
    "RecuerdoCaso",
    "Requisito",
    "ResultadoCaso",
    "ResumenCategoria",
    "ResumenInforme",
    "Rubrica",
    "SenalRepaso",
    "VeredictoJuez",
]

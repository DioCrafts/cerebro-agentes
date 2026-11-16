"""What a long-running task is: a goal in the user's words, a plan of typed steps, and their state.

A :class:`Tarea` lives for days or weeks (renewing a passport, preparing the income tax return, changing the
electricity tariff). Its plan is a list of :class:`Paso` from a CLOSED set of step types:

* ``calculo`` — run a module tool that only reads or drafts (``efecto`` ``lectura`` / ``prepara``);
* ``consulta`` — ask a module's specialist (a bounded sub-agent; needs a language model);
* ``capacidad`` — use a capability: e-mail, browser, documents, research (official sites only);
* ``espera`` — wait for a date or for an event of the bus (``perfil.cambiado`` of a fact…);
* ``confirmacion_usuario`` — the user must say it is done (a notice asks them);
* ``accion`` — a module tool that acts outside the app (``efecto="actua"``): it becomes a pending action and
  the task goes on only after the owner confirms it with the button;
* ``nota`` — a piece of information for the user, nothing runs.

The input a planner (a template or a model) may write is :class:`PasoSpec` (``extra="forbid"``): the state
fields of :class:`Paso` cannot be injected through a plan. Personal data (the goal, results, dates the user
gave) lives only here, in the case's database; the history (:class:`EntradaHistorial`) never holds values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Final, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EstadoTarea = Literal[
    "propuesta",
    "activa",
    "pausada",
    "esperando_usuario",
    "esperando_evento",
    "completada",
    "cancelada",
    "fallida",
]
TipoPaso = Literal["calculo", "consulta", "capacidad", "espera", "confirmacion_usuario", "accion", "nota"]
EstadoPaso = Literal["pendiente", "esperando", "hecho", "omitido", "fallido", "cancelado"]
ClaveCapacidad = Literal["correo", "navegador", "documentos", "investigacion"]
MotivoEspera = Literal["usuario", "fecha", "evento", "accion", "modulo"]
OrigenTarea = Literal["plantilla", "plan"]

TIPOS_PASO: Final = ("calculo", "consulta", "capacidad", "espera", "confirmacion_usuario", "accion", "nota")
# The task is over: nothing will run again.
TERMINALES: Final = frozenset({"completada", "cancelada"})
# The engine works on these (a paused, proposed, finished or failed task waits for the user).
EN_MARCHA: Final = frozenset({"activa", "esperando_usuario", "esperando_evento"})
# A step in one of these lets the steps that depend on it run.
PASO_CERRADO: Final = frozenset({"hecho", "omitido"})
PASO_FINAL: Final = frozenset({"hecho", "omitido", "cancelado", "fallido"})
# Events a step may wait for: those about ONE case (never a global one).
EVENTOS_DE_ESPERA: Final = frozenset({"perfil.cambiado", "correo.clasificado", "cargos.importados"})

MAX_PASOS: Final = 30
_ID_PASO = r"^[a-z][a-z0-9_]{0,40}$"


def ahora() -> datetime:
    return datetime.now(UTC)


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Espera(_Modelo):
    """What an ``espera`` step waits for: exactly one of a date (absolute, relative, given by the user in an
    earlier step, or a deadline of the tax calendar) or a case event, optionally filtered."""

    fecha: date | None = None
    # Days after the step starts waiting.
    dias: int | None = Field(default=None, ge=0, le=3650)
    # The date the user gave when confirming that (earlier) step, minus ``dias_antes``.
    fecha_de_paso: str | None = Field(default=None, pattern=_ID_PASO)
    # The first deadline of the tax calendar (asistente/ambitos/fiscal/calculo/calendario.py) whose id starts with this,
    # minus ``dias_antes``.
    calendario: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,60}$")
    dias_antes: int = Field(default=0, ge=0, le=365)
    evento: str | None = None
    # Filters: perfil.cambiado / cargos.importados of this fact; correo.clasificado of this category.
    hecho: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    categoria: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")
    # An event wait gives up after this many days: the step then asks the user.
    max_dias: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def _una_sola(self) -> Espera:
        claves = [self.fecha, self.dias, self.fecha_de_paso, self.calendario, self.evento]
        if sum(1 for c in claves if c is not None) != 1:
            raise ValueError("una espera necesita exactamente una de: fecha, dias, fecha_de_paso, calendario o evento")
        if self.evento is not None and self.evento not in EVENTOS_DE_ESPERA:
            raise ValueError(f"no se puede esperar el evento {self.evento!r}: solo {sorted(EVENTOS_DE_ESPERA)}")
        if self.evento is None and (self.hecho or self.categoria or self.max_dias):
            raise ValueError("hecho, categoria y max_dias solo valen para esperar un evento")
        if self.dias_antes and self.fecha_de_paso is None and self.calendario is None:
            raise ValueError("dias_antes solo vale con fecha_de_paso o calendario")
        return self


class PasoSpec(_Modelo):
    """One step as a planner writes it (template or model). Only these fields: no state can be injected."""

    id: str = Field(pattern=_ID_PASO)
    titulo: str = Field(min_length=1, max_length=120)
    descripcion: str = Field(default="", max_length=600)
    tipo: Literal["calculo", "consulta", "capacidad", "espera", "confirmacion_usuario", "accion", "nota"]
    depende_de: list[str] = Field(default_factory=list, max_length=10)
    # The user may skip it (and a module that is off skips it instead of blocking the task).
    opcional: bool = False
    # calculo / accion
    herramienta: str | None = Field(default=None, max_length=80)
    argumentos: dict[str, Any] = Field(default_factory=dict)
    # consulta
    modulo: str | None = Field(default=None, max_length=40)
    pregunta: str | None = Field(default=None, max_length=500)
    # capacidad
    capacidad: ClaveCapacidad | None = None
    operacion: str | None = Field(default=None, max_length=60)
    parametros: dict[str, Any] = Field(default_factory=dict)
    # espera
    espera: Espera | None = None

    @model_validator(mode="after")
    def _campos_del_tipo(self) -> PasoSpec:
        necesarios: dict[str, tuple[str, ...]] = {
            "calculo": ("herramienta",),
            "accion": ("herramienta",),
            "consulta": ("modulo", "pregunta"),
            "capacidad": ("capacidad", "operacion"),
            "espera": ("espera",),
        }
        for campo in necesarios.get(self.tipo, ()):
            if getattr(self, campo) in (None, ""):
                raise ValueError(f"un paso «{self.tipo}» necesita «{campo}»")
        ajenos = {
            "herramienta": self.tipo not in ("calculo", "accion"),
            "modulo": self.tipo != "consulta",
            "pregunta": self.tipo != "consulta",
            "capacidad": self.tipo != "capacidad",
            "operacion": self.tipo != "capacidad",
            "espera": self.tipo != "espera",
        }
        for campo, sobra in ajenos.items():
            if sobra and getattr(self, campo) not in (None, ""):
                raise ValueError(f"un paso «{self.tipo}» no lleva «{campo}»")
        if self.argumentos and self.tipo not in ("calculo", "accion"):
            raise ValueError(f"un paso «{self.tipo}» no lleva «argumentos»")
        if self.parametros and self.tipo != "capacidad":
            raise ValueError(f"un paso «{self.tipo}» no lleva «parametros»")
        if self.id in self.depende_de:
            raise ValueError("un paso no puede depender de sí mismo")
        return self


class Resultado(_Modelo):
    """What a step produced: a summary for the user and references to the evidence (never a whole file)."""

    resumen: str = Field(default="", max_length=4000)
    # BOE ids, module tool references (``modulo://luz/luz_comparar_tarifas``), official URLs, action ids…
    evidencias: list[str] = Field(default_factory=list, max_length=30)
    # A date the user gave when confirming (e.g. the day of the appointment).
    fecha: date | None = None
    # A draft the step prepared (never sent anywhere).
    borrador: str | None = Field(default=None, max_length=12000)


class EsperaActual(_Modelo):
    """Why a step is waiting now."""

    motivo: MotivoEspera
    desde: datetime = Field(default_factory=ahora)
    # A date wait ends this day; an event wait gives up this day (then it asks the user).
    hasta: date | None = None
    accion_id: str | None = None
    aviso_clave: str | None = None
    # Spanish: what we are waiting for.
    detalle: str = Field(default="", max_length=600)
    # The user's «confirmar» runs the step again (a tool said data is missing) instead of closing it.
    reintentar: bool = False


class Paso(PasoSpec):
    """A step of a task: its spec plus its state (written only by the engine)."""

    estado: EstadoPaso = "pendiente"
    espera_actual: EsperaActual | None = None
    resultado: Resultado | None = None
    intentos: int = Field(default=0, ge=0)
    siguiente_intento: datetime | None = None
    # Spanish, for the user (never an exception's internals).
    error: str | None = Field(default=None, max_length=500)
    iniciado: datetime | None = None
    terminado: datetime | None = None


class Vinculos(_Modelo):
    """What the task created elsewhere: notices (their keys), pending actions, decisions."""

    avisos: list[str] = Field(default_factory=list)
    acciones: list[str] = Field(default_factory=list)
    decisiones: list[str] = Field(default_factory=list)


ParametroValor = str | int | bool | None


class Tarea(_Modelo):
    id: str = Field(default_factory=lambda: f"tar_{uuid4().hex}", pattern=r"^tar_[0-9a-f]{32}$")
    case_id: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=200)
    titulo: str = Field(min_length=1, max_length=120)
    # The goal in the user's own words.
    objetivo: str = Field(min_length=1, max_length=1000)
    origen: OrigenTarea
    plantilla: str | None = Field(default=None, max_length=60)
    plantilla_version: int | None = None
    parametros: dict[str, ParametroValor] = Field(default_factory=dict)
    estado: EstadoTarea = "propuesta"
    creada: datetime = Field(default_factory=ahora)
    actualizada: datetime = Field(default_factory=ahora)
    aceptada: datetime | None = None
    terminada: datetime | None = None
    vence: date | None = None
    proxima_revision: date | None = None
    pasos: list[Paso] = Field(min_length=1, max_length=MAX_PASOS)
    vinculos: Vinculos = Field(default_factory=Vinculos)
    # The memory ámbito a cancellation with a reason is recorded on (asistente/nucleo/memoria).
    ambito_decision: str | None = Field(default=None, max_length=200)
    motivo_cancelacion: str | None = Field(default=None, max_length=500)
    # Optimistic concurrency: the store refuses a save over a newer version.
    version: int = Field(default=0, ge=0)

    @field_validator("objetivo", "titulo")
    @classmethod
    def _texto(cls, valor: str) -> str:
        valor = " ".join(valor.split())
        if not valor:
            raise ValueError("no puede estar vacío")
        return valor

    @model_validator(mode="after")
    def _plan_coherente(self) -> Tarea:
        problemas = problemas_del_grafo(self.pasos)
        if problemas:
            raise ValueError("; ".join(problemas))
        return self

    def paso(self, paso_id: str) -> Paso | None:
        return next((p for p in self.pasos if p.id == paso_id), None)

    @property
    def ref(self) -> str:
        """A short reference for the user (T-1A2B)."""
        return "T-" + self.id[4:8].upper()


def problemas_del_grafo(pasos: list[Any]) -> list[str]:
    """Unique ids, and every dependency on an EARLIER step (so the plan has no cycles)."""
    problemas: list[str] = []
    vistos: set[str] = set()
    for paso in pasos:
        if paso.id in vistos:
            problemas.append(f"el paso {paso.id!r} está repetido")
        for dep in paso.depende_de:
            if dep not in vistos:
                problemas.append(f"el paso {paso.id!r} depende de {dep!r}, que no es un paso anterior del plan")
        vistos.add(paso.id)
    return problemas


class EntradaHistorial(_Modelo):
    """One line of a task's append-only log. Ids, kinds and states only: never a value the user gave."""

    seq: int
    tarea_id: str
    momento: datetime
    evento: str
    paso_id: str | None = None
    detalle: dict[str, str | int | bool | None] = Field(default_factory=dict)


__all__ = [
    "EN_MARCHA",
    "EVENTOS_DE_ESPERA",
    "MAX_PASOS",
    "PASO_CERRADO",
    "PASO_FINAL",
    "TERMINALES",
    "TIPOS_PASO",
    "ClaveCapacidad",
    "EntradaHistorial",
    "Espera",
    "EsperaActual",
    "EstadoPaso",
    "EstadoTarea",
    "MotivoEspera",
    "OrigenTarea",
    "Paso",
    "PasoSpec",
    "Resultado",
    "Tarea",
    "TipoPaso",
    "Vinculos",
    "ahora",
    "problemas_del_grafo",
]

"""The research request and report (Pydantic v2, ``extra="forbid"``, money as Decimal)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TipoFuente = Literal["corpus", "boe", "web"]
TIPOS_FUENTE: tuple[TipoFuente, ...] = ("corpus", "boe", "web")
Profundidad = Literal["rapida", "normal", "a_fondo"]
PROFUNDIDADES: tuple[Profundidad, ...] = ("rapida", "normal", "a_fondo")
Confianza = Literal["alta", "media", "baja"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PeticionInvestigacion(_M):
    """What to research. Only ``pregunta`` (and ``ambito``) are ever sent to a model provider."""

    pregunta: str = Field(min_length=5, max_length=1000)
    # The domain that asks or the topic («fiscal», «caducidades», «seguros»…): a hint for the plan.
    ambito: str | None = Field(default=None, max_length=60)
    # Sources in priority order; the web only when listed AND switched on AND consented.
    fuentes: list[TipoFuente] = Field(default_factory=lambda: ["corpus", "boe"], min_length=1, max_length=3)
    profundidad: Profundidad = "normal"
    # Web only: restrict to the official allow-list (default) or search the open web minus the block-list.
    solo_oficiales: bool = True


class Cita(_M):
    fuente: str  # "S3"
    cita: str  # literal text of that source


class Afirmacion(_M):
    texto: str
    tema: str
    citas: list[Cita]
    # At least one of its quotes comes from an official source.
    oficial: bool


class FuenteInforme(_M):
    id: str  # "S1"…, in priority order: corpus, BOE, web
    tipo: TipoFuente
    titulo: str
    url: str | None = None
    referencia: str | None = None  # BOE id, nº de consulta…
    dominio: str | None = None
    oficial: bool
    recuperada_el: datetime
    citada: bool = False
    # The text contained prompt-injection bait (it was neutralised; the source is shown so the user can judge).
    sospechosa: bool = False
    nota: str | None = None


class Conflicto(_M):
    tema: str
    fuentes: list[str]
    descripcion: str
    # "cifras": the deterministic check saw different figures; "modelo": the synthesis reported it.
    origen: Literal["cifras", "modelo"]


class EstadoWeb(_M):
    pedida: bool
    usada: bool
    motivo: str | None = None  # why it was not used, in Spanish
    solo_oficiales: bool = True
    busquedas: int = 0
    paginas_leidas: int = 0
    # Search results the model saw but did not read: listed, never cited.
    vistas_sin_leer: list[str] = Field(default_factory=list)


class Coste(_M):
    llamadas_modelo: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    busquedas_web: int = 0
    # USD of this research; None when some call's cost is unknown (no usage reported or no price).
    usd: Decimal | None = Decimal(0)
    # Which cap stopped the research early, if any ("llamadas", "tokens", "usd").
    limite_alcanzado: str | None = None


class Informe(_M):
    pregunta: str
    ambito: str | None = None
    profundidad: Profundidad
    fuentes_pedidas: list[TipoFuente]
    # Built ONLY from the verified claims below, each followed by its source ids.
    respuesta: str
    afirmaciones: list[Afirmacion]
    fuentes: list[FuenteInforme]
    conflictos: list[Conflicto] = Field(default_factory=list)
    confianza: Confianza
    no_verificado: list[str] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    web: EstadoWeb
    coste: Coste
    generado_el: datetime
    desde_cache: bool = False


__all__ = [
    "PROFUNDIDADES",
    "TIPOS_FUENTE",
    "Afirmacion",
    "Cita",
    "Confianza",
    "Conflicto",
    "Coste",
    "EstadoWeb",
    "FuenteInforme",
    "Informe",
    "PeticionInvestigacion",
    "Profundidad",
    "TipoFuente",
]

"""The common shape of a group of legal data, whatever module it belongs to.

Every module stores its legal figures its own way (``evidencia`` lists inside each group, a ``valores`` map, a table
of dated items…). Each module's adapter turns its file into :class:`GrupoLegal` objects — the figures, the literal
quotes that print them (and how each figure is spelled there), and the source they were read from — so that one
checker (:mod:`hacienda_gpt.conocimiento.comprobacion`) verifies all of them the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt.conocimiento.texto import VERSION_DIARIO

Valor = Decimal | date


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FuenteLegal(_Modelo):
    """Where a group was read from, as its file records it."""

    referencia: str = ""
    # The law the group belongs to, when the file names it at group level (None: only its quotes name laws).
    boe_id: str | None = None
    # AAAAMMDD of a superseded wording, ``"diario"`` for a norm read as the daily BOE published it, None for the
    # current consolidated text.
    version: str | None = None
    consolidado_a: str | None = None
    consultado_el: str | None = None
    verificado: bool = True
    en_vigor_desde: str | None = None
    # A provision the figures depend on that may still fall (a decree-law awaiting validation).
    condicionada_a: str | None = None
    nota: str | None = None


class CitaLegal(_Modelo):
    """A literal passage of a law and the figures read from it."""

    boe_id: str
    version: str | None = None
    articulo: str = ""
    cita: str
    # Path of a figure in its group -> the figure exactly as the passage spells it ("5 por ciento", "Dos años").
    cifras: dict[str, str] = Field(default_factory=dict)
    # Same for dates ("31 de diciembre de 2026").
    fechas: dict[str, str] = Field(default_factory=dict)
    # Prefix of this passage's problems when it is not the group's name (a file that names each passage).
    nombre: str | None = None
    # "[…]" stitches passages of one article together (quotes kept in code); data files quote one passage.
    cortes: bool = False

    @property
    def clave_texto(self) -> str:
        """Key of the text this passage is checked against: ``<BOE-ID>`` or ``<BOE-ID>@<AAAAMMDD>``."""
        return f"{self.boe_id}@{self.version}" if self.version and self.version != VERSION_DIARIO else self.boe_id


class GrupoLegal(_Modelo):
    """Figures of one legal rule, the passages that print them and their source."""

    # Prefix of the group's problems ("dni", "peajes[2026-01-01]").
    nombre: str
    # Path of the group in its file (a list index as its digits).
    ruta: list[str]
    descripcion: str = ""
    fuente: FuenteLegal = Field(default_factory=FuenteLegal)
    # Every figure (and date) of the group that a passage must print, by dotted path.
    cifras: dict[str, Valor] = Field(default_factory=dict)
    citas: list[CitaLegal] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# How a module spells its figures
# --------------------------------------------------------------------------- #

# Boundaries of a spelled figure inside its passage.
# "cifra": "100" is not "10.000" nor "1.100,5"; words around it do not matter ("100 euros", "art. 5").
FRONTERA_CIFRA = (r"(?<![\d.,])", r"(?!\d|[.,]\d)")
# "palabra": the figure is a whole word too ("Dos años" is not inside "Doscientos años").
FRONTERA_PALABRA = (r"(?<![\w.,])", r"(?![\w]|[.,]\d)")
# Words before it, digits after it.
FRONTERA_MIXTA = (r"(?<![\d.,\w])", r"(?!\d|[.,]\d)")
# Anywhere in the passage.
FRONTERA_LIBRE = ("", "")


@dataclass(frozen=True)
class Ortografia:
    """How the passages of one file spell figures, and how a spelling is read back into the stored value."""

    # Spelling -> the value it states ("Dos años" -> 2, "0,15 por ciento" -> 0.0015). May raise on what it cannot read.
    leer_cifra: Callable[[str], Any]
    leer_fecha: Callable[[str], Any] | None = None
    frontera: tuple[str, str] = FRONTERA_CIFRA
    # How the file's messages call a figure: "cifra" or "dato".
    sustantivo: str = "cifra"

    def esta_en(self, literal: str, cita: str) -> bool:
        antes, despues = self.frontera
        return re.search(antes + re.escape(literal) + despues, cita) is not None


def hojas(
    valor: Any,
    *,
    tipos: tuple[type, ...] = (Decimal,),
    excluir: Iterable[str] = ("fuente", "evidencia", "avisos"),
    listas: bool = True,
    diccionarios: bool = True,
    prefijo: str = "",
) -> dict[str, Valor]:
    """Every figure of ``tipos`` inside a group, by dotted path (``tramos.0.hasta``, ``potencia_eur_kw_anio.p1``).

    Booleans are never figures; ints count only when ``int`` is in ``tipos`` (and are returned as Decimal).
    """
    excluidos = frozenset(excluir)
    if isinstance(valor, bool):
        return {}
    if isinstance(valor, int) and int in tipos:
        return {prefijo: Decimal(valor)}
    if isinstance(valor, tipos) and isinstance(valor, Decimal | date):
        return {prefijo: valor}
    items: list[tuple[str, Any]]
    if isinstance(valor, BaseModel):
        items = [(n, getattr(valor, n)) for n in type(valor).model_fields if n not in excluidos]
    elif isinstance(valor, dict) and diccionarios:
        items = [(str(k), v) for k, v in valor.items()]
    elif isinstance(valor, list | tuple) and listas:
        items = [(str(i), v) for i, v in enumerate(valor)]
    else:
        return {}
    found: dict[str, Valor] = {}
    for nombre, item in items:
        found.update(
            hojas(
                item,
                tipos=tipos,
                excluir=excluidos,
                listas=listas,
                diccionarios=diccionarios,
                prefijo=f"{prefijo}.{nombre}" if prefijo else nombre,
            )
        )
    return found


def citas_de_evidencia(
    evidencia: Iterable[Any], *, version: str | None = None, versiones: dict[str, str | None] | None = None
) -> list[CitaLegal]:
    """The ``evidencia`` items most modules store (``boe_id``, ``articulo``, ``cita``, ``cifras``[, ``fechas``]).

    ``versiones``: the version each BOE id is read in (``"diario"`` for a norm that is not consolidated).
    """
    out: list[CitaLegal] = []
    for e in evidencia:
        boe_id = str(e.boe_id)
        out.append(
            CitaLegal(
                boe_id=boe_id,
                version=(versiones or {}).get(boe_id, version),
                articulo=str(getattr(e, "articulo", "") or ""),
                cita=str(e.cita),
                cifras=dict(getattr(e, "cifras", {}) or {}),
                fechas=dict(getattr(e, "fechas", {}) or {}),
            )
        )
    return out


def fuente_de(fuente: Any) -> FuenteLegal:
    """A module's ``Fuente`` (referencia, en_vigor_desde, verificado, nota[, boe_id, condicionada_a…]) as common."""
    if fuente is None:
        return FuenteLegal()
    campos = {
        "referencia": "referencia",
        "boe_id": "boe_id",
        "version": "version_historica",
        "verificado": "verificado",
        "en_vigor_desde": "en_vigor_desde",
        "condicionada_a": "condicionada_a",
        "nota": "nota",
    }
    datos = {
        propio: getattr(fuente, suyo) for propio, suyo in campos.items() if getattr(fuente, suyo, None) is not None
    }
    if "en_vigor_desde" in datos:
        datos["en_vigor_desde"] = str(datos["en_vigor_desde"])
    return FuenteLegal(**datos)


__all__ = [
    "FRONTERA_CIFRA",
    "FRONTERA_LIBRE",
    "FRONTERA_MIXTA",
    "FRONTERA_PALABRA",
    "CitaLegal",
    "FuenteLegal",
    "GrupoLegal",
    "Ortografia",
    "Valor",
    "citas_de_evidencia",
    "fuente_de",
    "hojas",
]

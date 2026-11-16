"""Legal basis of the bridge from the mortgage module to the pre-2013 housing deduction (``modulos/puentes.py``).

The transitional deduction (disposición transitoria 18.ª LIRPF) applies articles 67.1, 68.1, 70.1, 77.1 and 78
"en su redacción en vigor a 31 de diciembre de 2012". What enters its base is art. 68.1.1.º of that wording: the
amounts paid for the acquisition, "incluidos los gastos originados que hayan corrido a cargo del adquirente y, en
el caso de financiación ajena, la amortización, los intereses, el coste de los instrumentos de cobertura [...] y
demás gastos derivados de la misma", the hedging income reducing the interest.

``data/deduccion_vivienda.json`` stores, for every concept of that list, whether the bridge adds it (it knows
the amortisation, the interest, the early repayments and their fees) or not (purchase expenses, hedging, other
loan costs), with the literal words of the law that cover it; plus the conditions it cannot check. The rate and
the cap are NOT here: they are the IRPF calculator's (``tax/data/irpf_*.json``, ``vivienda_habitual_transitoria``).

:func:`comprobar` is the self-check (the shared checker of :mod:`hacienda_gpt.conocimiento`, the file being
declared in ``FICHEROS_LEGALES``): every figure is inside its quote, every concept's words are inside its quote and,
given the law texts, every quote is verbatim in the wording it was read from (the 2012 wording is a superseded
version: ``act.php?id=BOE-A-2006-20764&tn=1&p=20121031``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt.conocimiento import (
    FRONTERA_LIBRE,
    CitaLegal,
    FicheroLegal,
    GrupoLegal,
    Ortografia,
    comprimido,
    texto_plano,
)
from hacienda_gpt.modulos.hipoteca.calculo import Cuadro
from hacienda_gpt.modulos.hipoteca.modelo import AmortizacionRealizada
from hacienda_gpt.modulos.hipoteca.normativa import leer_cifra

DEDUCCION_PATH = Path(__file__).resolve().parent / "data" / "deduccion_vivienda.json"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pagina(_Model):
    # The law the page belongs to (the key also carries the version of a superseded wording).
    boe_id: str | None = None
    norma: str
    url: str
    consolidado_a: str
    consultado_el: str
    sha256_html: str


class Cita(_Model):
    boe_id: str
    # AAAAMMDD of the superseded wording the quote was read from (None: the text in force).
    version: str | None = None
    articulo: str
    cita: str
    # Field of the data -> the figure exactly as the quote spells it.
    cifras: dict[str, str] = Field(default_factory=dict)

    @property
    def clave_texto(self) -> str:
        return f"{self.boe_id}@{self.version}" if self.version else self.boe_id


class Concepto(_Model):
    # Key of the bridge's breakdown (``PropuestaHecho.desglose``) when ``incluido``.
    clave: str
    etiqueta: str
    # True: the bridge adds it; False: the law counts it but the module does not know it.
    incluido: bool
    cita: str
    # Literal words of the quote that cover the concept.
    termino: str
    nota: str


class FundamentoPuente(_Model):
    version: str
    fuentes: dict[str, Pagina]
    fecha_limite_adquisicion: date
    citas: dict[str, Cita]
    conceptos: list[Concepto]
    no_modelado: list[str] = Field(default_factory=list)

    def incluidos(self) -> list[Concepto]:
        return [c for c in self.conceptos if c.incluido]

    def no_incluidos(self) -> list[Concepto]:
        return [c for c in self.conceptos if not c.incluido]

    def concepto(self, clave: str) -> Concepto | None:
        return next((c for c in self.conceptos if c.clave == clave), None)


@lru_cache(maxsize=1)
def cargar_fundamento() -> FundamentoPuente:
    return FundamentoPuente.model_validate(json.loads(DEDUCCION_PATH.read_text(encoding="utf-8")))


def pagos_del_ano(
    cuadro: Cuadro, amortizaciones: Iterable[AmortizacionRealizada], ejercicio: int
) -> dict[str, Decimal]:
    """What the loan costs in ``ejercicio``, by concept (keys of :class:`Concepto`): capital and interest of the
    ordinary instalments due that year (from ``cuadro``), the early repayments made that year and their fees."""
    capital, intereses = cuadro.pagos_del_ano(ejercicio)
    del_ano = [a for a in amortizaciones if a.fecha.year == ejercicio]
    return {
        "capital": capital,
        "intereses": intereses,
        "amortizaciones_anticipadas": sum((a.importe for a in del_ano), Decimal(0)),
        "comisiones_amortizacion_anticipada": sum((a.comision_pagada for a in del_ano), Decimal(0)),
    }


def importes_deducibles(
    pagos: Mapping[str, Decimal | float | int | None], fundamento: FundamentoPuente | None = None
) -> dict[str, Decimal]:
    """The concepts of ``pagos`` that enter the transitional deduction's base: those the law counts and the module
    knows (``incluido``), in the data's order. The single definition used by the bridge to the tax module
    (``modulos/puentes.py``) and by the household planner (``planificador/hipoteca.py``)."""
    fundamento = fundamento or cargar_fundamento()
    return {c.clave: Decimal(str(pagos.get(c.clave) or 0)) for c in fundamento.incluidos()}


def base_deducible(
    pagos: Mapping[str, Decimal | float | int | None], fundamento: FundamentoPuente | None = None
) -> Decimal:
    """Sum of :func:`importes_deducibles` to the cent (before the calculator's cap and rate)."""
    return sum(importes_deducibles(pagos, fundamento).values(), Decimal(0)).quantize(Decimal("0.01"))


ORTOGRAFIA = Ortografia(leer_cifra=leer_cifra, frontera=FRONTERA_LIBRE)


def grupos_legales(fundamento: FundamentoPuente) -> list[GrupoLegal]:
    """The file as ONE group of the shared library: its figure (the acquisition date limit) and every quote, each
    named by its key (problems are prefixed by it) and read in the wording it comes from."""
    return [
        GrupoLegal(
            nombre="",
            ruta=["citas"],
            descripcion="fundamento de la deducción por vivienda habitual (DT 18.ª LIRPF)",
            cifras={"fecha_limite_adquisicion": fundamento.fecha_limite_adquisicion},
            citas=[
                CitaLegal(
                    boe_id=cita.boe_id,
                    version=cita.version,
                    articulo=cita.articulo,
                    cita=cita.cita,
                    cifras=dict(cita.cifras),
                    nombre=nombre,
                )
                for nombre, cita in fundamento.citas.items()
            ],
        )
    ]


def _conceptos(fundamento: FundamentoPuente, _textos: Mapping[str, str] | None) -> list[str]:
    """What only this file has: each concept's words are inside the quote that covers it."""
    problemas: list[str] = []
    claves = [c.clave for c in fundamento.conceptos]
    if len(claves) != len(set(claves)):
        problemas.append("conceptos: hay claves repetidas")
    for concepto in fundamento.conceptos:
        de_concepto = fundamento.citas.get(concepto.cita)
        if de_concepto is None:
            problemas.append(f"concepto {concepto.clave}: la cita «{concepto.cita}» no existe")
        elif comprimido(concepto.termino) not in comprimido(de_concepto.cita):
            problemas.append(f"concepto {concepto.clave}: «{concepto.termino}» no está en la cita {concepto.cita}")
    return problemas


FICHEROS_LEGALES = (
    FicheroLegal(
        clave="hipoteca_deduccion",
        modulo="hipoteca",
        titulo="Hipoteca: fundamento de la deducción transitoria por vivienda",
        ruta=DEDUCCION_PATH,
        cargar=FundamentoPuente.model_validate,
        grupos=grupos_legales,
        ortografia=ORTOGRAFIA,
        fuentes=lambda fundamento: set(fundamento.fuentes),
        propias=_conceptos,
        limpiar=lambda: cargar_fundamento.cache_clear(),
        solo_persona=(
            "Lo que entra en la base es una lista de conceptos con las palabras de la ley que los cubren, leída "
            "sobre todo de una redacción derogada (a 31-12-2012) que ya no cambia; no hay cifras que un caso de "
            "prueba pueda contrastar. Una persona la revisa; la verificación semanal comprueba cada cita en su "
            "versión."
        ),
    ),
)


def comprobar(fundamento: FundamentoPuente, textos: dict[str, str] | None = None) -> list[str]:
    """Problems found re-reading the data against its quotes and (with ``textos``: ``"<BOE-ID>"`` or
    ``"<BOE-ID>@<AAAAMMDD>"`` -> page HTML or plain text) the quotes against the law. Empty list = consistent.
    The checker is the shared one of :mod:`hacienda_gpt.conocimiento`."""
    planos = None if textos is None else {clave: texto_plano(texto) for clave, texto in textos.items()}
    return FICHEROS_LEGALES[0].comprobar(fundamento, planos)


def textos_de_directorio(directorio: Path) -> dict[str, str]:
    """``<BOE-ID>.html|.txt`` -> its key; ``<BOE-ID>_p<AAAAMMDD>.html|.txt`` -> ``<BOE-ID>@<AAAAMMDD>``."""
    textos: dict[str, str] = {}
    for path in sorted([*directorio.glob("*.html"), *directorio.glob("*.txt")]):
        boe_id, _, version = path.stem.partition("_p")
        textos[f"{boe_id}@{version}" if version else boe_id] = path.read_text(encoding="utf-8", errors="replace")
    return textos


__all__ = [
    "DEDUCCION_PATH",
    "Cita",
    "Concepto",
    "FundamentoPuente",
    "base_deducible",
    "cargar_fundamento",
    "comprobar",
    "importes_deducibles",
    "pagos_del_ano",
    "textos_de_directorio",
]

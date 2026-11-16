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

:func:`comprobar` is the self-check: every figure is inside its quote, every concept's words are inside its
quote and, given the law texts, every quote is verbatim in the wording it was read from (the 2012 wording is a
superseded version: ``act.php?id=BOE-A-2006-20764&tn=1&p=20121031``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt.modulos.hipoteca.normativa import leer_cifra
from hacienda_gpt.tax.verificacion import texto_plano

DEDUCCION_PATH = Path(__file__).resolve().parent / "data" / "deduccion_vivienda.json"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pagina(_Model):
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


def _comprimido(texto: str) -> str:
    """Like ``tax.verificacion``: NFKC, lower case, no whitespace or quotation marks (links add stray spaces)."""
    return re.sub(r"[\s«»\"“”]", "", unicodedata.normalize("NFKC", texto).lower())


def comprobar(fundamento: FundamentoPuente, textos: dict[str, str] | None = None) -> list[str]:
    """Problems found re-reading the data against its quotes and (with ``textos``: ``"<BOE-ID>"`` or
    ``"<BOE-ID>@<AAAAMMDD>"`` -> page HTML or plain text) the quotes against the law. Empty list = consistent."""
    problemas: list[str] = []
    cifras_datos: dict[str, Decimal | date] = {"fecha_limite_adquisicion": fundamento.fecha_limite_adquisicion}
    con_evidencia: set[str] = set()
    comprimidos = {clave: _comprimido(texto_plano(texto)) for clave, texto in (textos or {}).items()}
    for nombre, cita in fundamento.citas.items():
        if cita.clave_texto not in fundamento.fuentes:
            problemas.append(f"{nombre}: {cita.clave_texto} no figura en fuentes")
        if textos is not None:
            texto = comprimidos.get(cita.clave_texto)
            if texto is None:
                problemas.append(f"{nombre}: no se ha aportado el texto de {cita.clave_texto}")
            elif _comprimido(cita.cita) not in texto:
                problemas.append(f"{nombre}: la cita de {cita.articulo} no aparece literal en {cita.clave_texto}")
        for campo, literal in cita.cifras.items():
            if campo not in cifras_datos:
                problemas.append(f"{nombre}: la cita respalda una cifra inexistente ({campo})")
                continue
            if literal not in cita.cita:
                problemas.append(f"{nombre}.{campo}: «{literal}» no aparece en la cita")
                continue
            if leer_cifra(literal) != cifras_datos[campo]:
                problemas.append(f"{nombre}.{campo}: el dato es {cifras_datos[campo]} y la cita dice «{literal}»")
                continue
            con_evidencia.add(campo)
    problemas += [f"{campo}: cifra sin evidencia" for campo in sorted(set(cifras_datos) - con_evidencia)]
    claves = [c.clave for c in fundamento.conceptos]
    if len(claves) != len(set(claves)):
        problemas.append("conceptos: hay claves repetidas")
    for concepto in fundamento.conceptos:
        de_concepto = fundamento.citas.get(concepto.cita)
        if de_concepto is None:
            problemas.append(f"concepto {concepto.clave}: la cita «{concepto.cita}» no existe")
        elif _comprimido(concepto.termino) not in _comprimido(de_concepto.cita):
            problemas.append(f"concepto {concepto.clave}: «{concepto.termino}» no está en la cita {concepto.cita}")
    return problemas


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
    "cargar_fundamento",
    "comprobar",
    "textos_de_directorio",
]

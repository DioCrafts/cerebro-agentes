"""Legal parameters of the mortgage module, loaded from versioned JSON (``data/normativa.json``).

Every figure (fee caps, periods, dates) is read from the consolidated text on boe.es and stored with the
literal sentence it comes from and how the figure is spelled there, so :func:`comprobar_evidencias`
(the shared checker of :mod:`asistente.conocimiento`) can re-check it mechanically: every figure is inside its
quote and means the stored value, and (given the law texts) every quote is verbatim in its law. No figure lives in
the arithmetic. The file is declared in ``FICHEROS_LEGALES``: the weekly BOE verification and the reading dates
cover it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from pathlib import Path
import re
import sys

from pydantic import BaseModel, ConfigDict, Field

from asistente.conocimiento import (
    FRONTERA_CIFRA,
    FicheroLegal,
    GrupoLegal,
    Ortografia,
    citas_de_evidencia,
    fuente_de,
    hojas,
)
from asistente.conocimiento import textos_de_directorio as textos_de_directorio_comun

DATA_DIR = Path(__file__).resolve().parent / "data"
NORMATIVA_PATH = DATA_DIR / "normativa.json"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FuentePrincipal(_Model):
    norma: str
    boe_id: str
    url: str
    consolidado_a: str
    consultado_el: str
    sha256_html: str


class Fuente(_Model):
    referencia: str
    boe_id: str
    en_vigor_desde: str
    verificado: bool
    nota: str | None = None


class Evidencia(_Model):
    boe_id: str
    articulo: str
    cita: str
    # Field of the group -> the figure exactly as the quote spells it ("0,15 por ciento", "16/06/2019").
    cifras: dict[str, str] = Field(default_factory=dict)


class _Grupo(_Model):
    fuente: Fuente
    evidencia: list[Evidencia] = Field(min_length=1)

    def cita_corta(self) -> str:
        return f"{self.fuente.referencia} ({self.fuente.boe_id})"


class Ley52019(_Grupo):
    entrada_en_vigor: date


class ReembolsoAnticipado(_Grupo):
    preaviso_maximo_meses: Decimal


class ComisionVariable(_Grupo):
    opcion_a_anos: Decimal
    opcion_a_limite: Decimal
    opcion_b_anos: Decimal
    opcion_b_limite: Decimal


class ComisionFijo(_Grupo):
    anos_iniciales: Decimal
    limite_inicial: Decimal
    limite_posterior: Decimal


class CambioAFijo(_Grupo):
    anos: Decimal
    limite: Decimal
    periodo_fijo_minimo_anos: Decimal


class TipoVariable(_Grupo):
    pass


class Ley412007(_Grupo):
    entrada_en_vigor: date
    desistimiento_anos: Decimal
    desistimiento_limite_inicial: Decimal
    desistimiento_limite_posterior: Decimal
    riesgo_tipo_revision_max_meses: Decimal


class Ley21994(_Grupo):
    limite_no_subrogatoria_variable: Decimal
    limite_subrogacion_variable: Decimal
    limite_novacion_ampliacion_plazo: Decimal
    reduccion_arancel_registral: Decimal


class Gastos(_Grupo):
    pass


class Suspension(_Grupo):
    desde: date
    hasta: date


class CodigoBuenasPracticas2022(_Grupo):
    duracion_meses: Decimal
    duracion_meses_dana: Decimal
    precio_adquisicion_maximo: Decimal
    constituidos_hasta: date
    acuerdo_publicado: date


class CodigoBuenasPracticas2012(_Grupo):
    precio_adquisicion_maximo: Decimal


class IndicesOficiales(_Grupo):
    pass


class Normativa(_Model):
    version: str
    fuentes_principales: dict[str, FuentePrincipal]
    no_modelado: list[str] = Field(default_factory=list)
    ley_5_2019: Ley52019
    reembolso_anticipado: ReembolsoAnticipado
    comision_variable: ComisionVariable
    comision_fijo: ComisionFijo
    cambio_a_fijo: CambioAFijo
    tipo_variable: TipoVariable
    ley_41_2007: Ley412007
    ley_2_1994: Ley21994
    gastos: Gastos
    suspension_comisiones_2022_2024: Suspension
    codigo_buenas_practicas_2022: CodigoBuenasPracticas2022
    codigo_buenas_practicas_2012: CodigoBuenasPracticas2012
    indices_oficiales: IndicesOficiales

    def grupos(self) -> dict[str, _Grupo]:
        return {n: v for n in type(self).model_fields if isinstance(v := getattr(self, n), _Grupo)}

    def url(self, boe_id: str) -> str:
        return self.fuentes_principales[boe_id].url


@lru_cache(maxsize=1)
def cargar_normativa() -> Normativa:
    return Normativa.model_validate(json.loads(NORMATIVA_PATH.read_text(encoding="utf-8")))


# --- Self-check ---------------------------------------------------------------------------------

_NUMEROS = {
    "un": 1,
    "una": 1,
    "tres": 3,
    "cinco": 5,
    "diez": 10,
    "doce": 12,
    "treinta y seis": 36,
    "cuarenta y dos": 42,
}
_MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def leer_cifra(literal: str) -> Decimal | date:
    """'0,15 por ciento' -> 0.0015; '1 por 100' -> 0.01; '300.000 euros' -> 300000; '5 primeros años' -> 5;
    'treinta y seis meses' -> 36; 'al menos, 3 años' -> 3; '16/06/2019' and '31 de diciembre de 2024' -> date."""
    texto = " ".join(literal.split())
    if m := re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", texto):
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.fullmatch(r"(\d{1,2}) de (\w+) de (\d{4})", texto):
        return date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1)))
    for palabra, valor in sorted(_NUMEROS.items(), key=lambda kv: -len(kv[0])):
        if re.match(rf"{palabra}\b", texto.lower()):
            return Decimal(valor)
    m = re.search(r"\d[\d.]*(,\d+)?", texto)
    if not m:
        raise InvalidOperation(literal)
    numero = Decimal(m.group(0).replace(".", "").replace(",", "."))
    return numero / 100 if re.search(r"por (ciento|100)\b", texto) else numero


ORTOGRAFIA = Ortografia(leer_cifra=leer_cifra, frontera=FRONTERA_CIFRA)


def grupos_legales(normativa: Normativa) -> list[GrupoLegal]:
    """The file as groups of the shared library: each group's own figures (its fields that hold an amount, a
    percentage, a period or a date) and its quotes."""
    return [
        GrupoLegal(
            nombre=nombre,
            ruta=[nombre],
            descripcion=grupo.cita_corta(),
            fuente=fuente_de(grupo.fuente),
            cifras=hojas(
                grupo, tipos=(Decimal, date), excluir=("fuente", "evidencia"), listas=False, diccionarios=False
            ),
            citas=citas_de_evidencia(grupo.evidencia),
        )
        for nombre, grupo in normativa.grupos().items()
    ]


FICHEROS_LEGALES = (
    FicheroLegal(
        clave="hipoteca",
        modulo="hipoteca",
        titulo="Hipoteca: comisiones, plazos y normas",
        ruta=NORMATIVA_PATH,
        cargar=Normativa.model_validate,
        grupos=grupos_legales,
        ortografia=ORTOGRAFIA,
        fuentes=lambda normativa: set(normativa.fuentes_principales),
        limpiar=lambda: cargar_normativa.cache_clear(),
        solo_persona=(
            "Los límites de las comisiones dependen de la fecha del contrato y de la opción que eligió la entidad "
            "(arts. 23 y 24 Ley 5/2019, DT 1.ª): la calculadora de comisiones no recibe la normativa como "
            "parámetro, así que un caso de prueba no podría contrastar una propuesta. Una persona la relee; la "
            "verificación semanal comprueba cada cita y cada cifra."
        ),
    ),
)


def comprobar_evidencias(normativa: Normativa, textos: dict[str, str] | None = None) -> list[str]:
    """Problems found re-reading the data against its quotes (and, with ``textos``, the quotes against the
    law texts: BOE id -> plain text). Empty list = consistent. The checker is the shared one of
    :mod:`asistente.conocimiento`."""
    return FICHEROS_LEGALES[0].comprobar(normativa, textos)


def textos_de_directorio(directorio: Path) -> dict[str, str]:
    """BOE id -> plain text, from ``<BOE-ID>.html`` (downloaded pages) or ``<BOE-ID>.txt`` (extracts)."""
    return textos_de_directorio_comun(directorio, "BOE-A-*")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - maintenance
    """``python -m asistente.ambitos.hipoteca.normativa <dir con BOE-A-*.html descargados>``."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(main.__doc__)
        return 2
    problemas = comprobar_evidencias(cargar_normativa(), textos_de_directorio(Path(args[0])))
    for problema in problemas:
        print(problema)
    print("Normativa coherente con el texto." if not problemas else f"{len(problemas)} problema(s).")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Figures of the stable public benefits (family A), loaded from versioned JSON.

Same rule as ``asistente.ambitos.fiscal.calculo.autonomos_params``: no figure lives in the arithmetic, and every group
carries the literal quotes of the law it was read from and how each figure is spelled in them, so the
data can be re-checked mechanically against the consolidated texts on boe.es
(:func:`comprobar_evidencias`, the shared checker of :mod:`asistente.conocimiento`). Dates read from the law are
checked the same way. Each year's file is declared in ``FICHEROS_LEGALES``: the weekly BOE verification and the
reading dates cover it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re
import sys

from pydantic import BaseModel, ConfigDict, Field

from asistente.ambitos.fiscal.calculo.params import Fuente, FuentePrincipal
from asistente.conocimiento import (
    FRONTERA_MIXTA,
    FicheroLegal,
    GrupoLegal,
    Ortografia,
    citas_de_evidencia,
    fuente_de,
    hojas,
)
from asistente.conocimiento import textos_de_directorio as textos_de_directorio_comun

DATA_DIR = Path(__file__).resolve().parent / "data"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidencia(_Model):
    """A literal quote of the law and the figures (and dates) read from it."""

    boe_id: str
    articulo: str
    cita: str
    # Path of the figure inside its group ("pension_no_contributiva_anual", "escala_patrimonio.0.factor")
    # -> the figure exactly as the quote spells it ("8.803,20 euros", "1,4", "sesenta y cinco por ciento").
    cifras: dict[str, str] = Field(default_factory=dict)
    # Same for dates: path -> "31 de diciembre de 2026".
    fechas: dict[str, str] = Field(default_factory=dict)


class Grupo(_Model):
    fuente: Fuente
    evidencia: list[Evidencia] = Field(min_length=1)
    # Caveats the evaluators repeat whenever they use this group.
    avisos: list[str] = Field(default_factory=list)


class IPREM(Grupo):
    mensual: Decimal
    pagas_anuales: Decimal

    @property
    def anual_14_pagas(self) -> Decimal:
        return self.mensual * self.pagas_anuales


class FilaEscala(_Model):
    adultos: int
    menores: int
    # "tres o más menores": the row also covers more minors.
    o_mas: bool = False
    factor: Decimal


class IMV(Grupo):
    pension_no_contributiva_anual: Decimal
    porcentaje_pnc: Decimal
    meses: Decimal
    complemento_discapacidad: Decimal
    grado_discapacidad_complemento: Decimal
    incremento_por_miembro: Decimal
    escala_maxima: Decimal
    complemento_monoparental: Decimal
    margen_minimo: Decimal
    importe_minimo: Decimal
    patrimonio_veces: Decimal
    activos_veces: Decimal
    edad_minima_titular: Decimal
    escala_patrimonio: list[FilaEscala] = Field(min_length=1)
    escala_patrimonio_otros: Decimal

    @property
    def renta_garantizada_individual(self) -> Decimal:
        """Monthly guaranteed income of one person living alone (art. 13.2.a)."""
        return (self.pension_no_contributiva_anual * self.porcentaje_pnc / self.meses).quantize(Decimal("0.01"))

    def escala(self, adultos: int, menores: int) -> Decimal:
        """Art. 13.2.b): +30 % per member from the second, up to 220 %."""
        miembros = max(1, adultos + menores)
        return min(Decimal(1) + self.incremento_por_miembro * (miembros - 1), self.escala_maxima)

    def factor_patrimonio(self, adultos: int, menores: int) -> Decimal:
        """Anexo II: increments of the wealth limit by kind of household (1 for a person alone)."""
        if adultos <= 1 and menores == 0:
            return Decimal(1)
        for fila in self.escala_patrimonio:
            if fila.adultos == adultos and (menores == fila.menores or (fila.o_mas and menores > fila.menores)):
                return fila.factor
        return self.escala_patrimonio_otros


class CAPI(Grupo):
    umbral_ingresos: Decimal
    umbral_patrimonio: Decimal
    # Amounts in the wording of the law; NOT the 2026 amounts (see the group's note).
    cuantia_ley_menor_3: Decimal
    cuantia_ley_3_a_6: Decimal
    cuantia_ley_6_a_18: Decimal


class HijoDiscapacidad(Grupo):
    edad_limite: Decimal
    grado_menor: Decimal
    grado_mayor: Decimal
    grado_asistencia: Decimal
    importe_menor: Decimal
    importe_mayor_65: Decimal
    importe_mayor_75: Decimal


class Nacimiento(Grupo):
    importe: Decimal
    grado_progenitor: Decimal
    incremento_por_hijo: Decimal
    limite_general: Decimal
    limite_familia_numerosa: Decimal
    incremento_familia_numerosa: Decimal
    diferencia_minima: Decimal
    prescripcion_anios: Decimal

    def limite(self, hijos_a_cargo: int, familia_numerosa: bool) -> Decimal:
        """Income ceiling of art. 357.3 LGSS with the 2026 figures."""
        general = self.limite_general * (1 + self.incremento_por_hijo * max(0, hijos_a_cargo - 1))
        if not familia_numerosa:
            return general
        numerosa = self.limite_familia_numerosa + self.incremento_familia_numerosa * max(0, hijos_a_cargo - 3)
        return max(general, numerosa)


class BonoSocialElectrico(Grupo):
    multiplicador_base: Decimal
    incremento_adulto: Decimal
    incremento_menor: Decimal
    incremento_circunstancias: Decimal
    grado_discapacidad_circunstancia: Decimal
    otros_ingresos_pensionista: Decimal
    severo_fraccion: Decimal
    severo_pensionista: Decimal
    severo_familia_numerosa: Decimal
    descuento_vulnerable: Decimal
    descuento_severo: Decimal
    descuentos_hasta: date
    descuento_vulnerable_general: Decimal
    descuento_severo_general: Decimal
    duracion_anios: Decimal


class BonoSocialTermico(Grupo):
    ayuda_minima: Decimal
    incremento_severo: Decimal


class TituloMonoparental(Grupo):
    edad_hijos: Decimal
    umbral_especial_iprem: Decimal
    solicitable_desde: date


class Remisiones(Grupo):
    """Quotes without figures (procedural rules, cross-references)."""


class PrestacionesParams(_Model):
    ejercicio: int
    consultado_el: date
    fuentes_principales: dict[str, FuentePrincipal]
    no_modelado: list[str] = Field(default_factory=list)
    iprem: IPREM
    imv: IMV
    capi: CAPI
    hijo_discapacidad: HijoDiscapacidad
    nacimiento: Nacimiento
    bono_social_electrico: BonoSocialElectrico
    bono_social_termico: BonoSocialTermico
    titulo_monoparental: TituloMonoparental
    plazos: Remisiones
    remisiones_fiscales: Remisiones

    def grupos(self) -> dict[str, Grupo]:
        return {name: value for name in type(self).model_fields if isinstance(value := getattr(self, name), Grupo)}

    def fuente_texto(self, *boe_ids: str) -> str:
        return "; ".join(
            f"{fp.norma}, {fp.boe_id}, consolidado a {fp.consolidado_a}"
            for fp in (self.fuentes_principales[boe_id] for boe_id in boe_ids)
        )


class EjercicioNoDisponibleError(LookupError):
    pass


def ejercicios_disponibles() -> list[int]:
    return sorted(int(path.stem.rsplit("_", 1)[1]) for path in DATA_DIR.glob("prestaciones_*.json"))


@lru_cache(maxsize=8)
def load_prestaciones(ejercicio: int | None = None) -> PrestacionesParams:
    """The benefits' figures of ``ejercicio`` (the latest available when None)."""
    disponibles = ejercicios_disponibles()
    if ejercicio is None:
        ejercicio = max(disponibles)
    path = DATA_DIR / f"prestaciones_{ejercicio}.json"
    if not path.exists():
        raise EjercicioNoDisponibleError(
            f"No hay cifras de ayudas cargadas para {ejercicio} (disponibles: {', '.join(map(str, disponibles))})."
        )
    return PrestacionesParams.model_validate(json.loads(path.read_text(encoding="utf-8")))


def ejercicio_para(hoy: date) -> int:
    """The year whose figures apply on ``hoy``: that year if loaded, else the latest earlier one."""
    disponibles = [year for year in ejercicios_disponibles() if year <= hoy.year]
    return max(disponibles) if disponibles else min(ejercicios_disponibles())


# --- Self-check of the data against the law ---------------------------------

_NUMEROS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "doce": 12,
    "dieciocho": 18,
    "veintiún": 21,
    "veintiuno": 21,
    "veintitrés": 23,
    "treinta y tres": 33,
    "sesenta y cinco": 65,
    "ciento cincuenta": 150,
}
_UNIDADES = ("por ciento", "%", "euros", "veces", "vez", "años", "pagas")
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


def cifra_a_decimal(literal: str) -> Decimal:
    """'42,5 por ciento' -> 0.425; '8.803,20 euros' -> 8803.20; 'sesenta y cinco por ciento' -> 0.65; 'dos veces' -> 2."""
    texto = literal.strip().lower()
    porcentaje = "por ciento" in texto or "%" in texto
    for unidad in _UNIDADES:
        texto = texto.replace(unidad, " ")
    texto = " ".join(texto.split()).rstrip(".")
    if texto in _NUMEROS:
        number = Decimal(_NUMEROS[texto])
    else:
        number = Decimal(texto.replace(".", "").replace(",", "."))
    return number / 100 if porcentaje else number


def fecha_literal(literal: str) -> date:
    """'31 de diciembre de 2026' -> date(2026, 12, 31)."""
    match = re.fullmatch(r"(\d{1,2}) de (\w+) de (\d{4})", literal.strip().lower())
    if not match or match.group(2) not in _MESES:
        raise ValueError(literal)
    return date(int(match.group(3)), _MESES[match.group(2)], int(match.group(1)))


def _hojas(value: object, prefix: str = "") -> dict[str, object]:
    """Every Decimal and date inside a group, by dotted path."""
    return dict(hojas(value, tipos=(Decimal, date), diccionarios=False, prefijo=prefix))


ORTOGRAFIA = Ortografia(
    leer_cifra=cifra_a_decimal, leer_fecha=fecha_literal, frontera=FRONTERA_MIXTA, sustantivo="dato"
)


def grupos_legales(params: PrestacionesParams) -> list[GrupoLegal]:
    """The file as groups of the shared library."""
    return [
        GrupoLegal(
            nombre=nombre,
            ruta=[nombre],
            descripcion=f"{nombre} ({grupo.fuente.referencia})",
            fuente=fuente_de(grupo.fuente),
            cifras=hojas(grupo, tipos=(Decimal, date), diccionarios=False),
            citas=citas_de_evidencia(grupo.evidencia),
        )
        for nombre, grupo in params.grupos().items()
    ]


def _cargar(raw: dict[str, object]) -> PrestacionesParams:
    return PrestacionesParams.model_validate(raw)


FICHEROS_LEGALES = tuple(
    FicheroLegal(
        clave=f"ayudas_{ejercicio}",
        modulo="ayudas",
        titulo=f"Ayudas: cifras de las prestaciones de {ejercicio}",
        ruta=DATA_DIR / f"prestaciones_{ejercicio}.json",
        cargar=_cargar,
        grupos=grupos_legales,
        ortografia=ORTOGRAFIA,
        fuentes=lambda params: set(params.fuentes_principales),
        limpiar=lambda: load_prestaciones.cache_clear(),
        solo_persona=(
            "Las cifras de cada año salen de varias normas a la vez (la ley de cada prestación, la de "
            "presupuestos o el real decreto-ley de revalorización, el IPREM): un grupo se apoya en citas de leyes "
            "distintas y el evaluador de ayudas no recibe un fichero alternativo. Una persona las relee; la "
            "verificación semanal comprueba cada cita y cada cifra."
        ),
    )
    for ejercicio in ejercicios_disponibles()
)


def comprobar_evidencias(params: PrestacionesParams, textos: dict[str, str] | None = None) -> list[str]:
    """Problems found when re-reading the figures against their quotes (empty: consistent).

    Always checked: every figure and date of every group is spelled in one of the group's quotes and
    that spelling means the stored value. With ``textos`` (BOE id -> plain text of the law, or of the
    quoted articles) each quote must also appear verbatim in its law. The checker is the shared one of
    :mod:`asistente.conocimiento`.
    """
    fichero = next((f for f in FICHEROS_LEGALES if f.clave == f"ayudas_{params.ejercicio}"), None)
    if fichero is None:  # a year built in memory (tests): same declaration, no file
        fichero = replace(FICHEROS_LEGALES[-1], clave=f"ayudas_{params.ejercicio}")
    return fichero.comprobar(params, textos)


def textos_de_directorio(directorio: Path) -> dict[str, str]:
    """BOE id -> plain text, from ``<BOE-ID>.html`` (downloaded pages) or ``<BOE-ID>.txt`` extracts."""
    return textos_de_directorio_comun(directorio, "BOE-A-*")


def main(argv: list[str] | None = None) -> int:
    """``python -m asistente.ambitos.ayudas.parametros <dir with BOE-A-*.html|txt> [ejercicio]``."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (1, 2):
        print("uso: python -m asistente.ambitos.ayudas.parametros <directorio con BOE-A-*.html> [ejercicio]")
        return 2
    params = load_prestaciones(int(args[1]) if len(args) == 2 else None)
    problemas = comprobar_evidencias(params, textos_de_directorio(Path(args[0])))
    for problema in problemas:
        print(problema)
    print("Cifras coherentes con el texto." if not problemas else f"{len(problemas)} problema(s).")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())

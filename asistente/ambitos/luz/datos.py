"""Regulated figures of a Spanish household electricity bill (2.0TD), loaded from versioned JSON.

Every figure was read on www.boe.es and is stored with the literal sentence (or table row) it comes from,
like ``asistente/ambitos/fiscal/calculo/autonomos_params.py``. :func:`comprobar_evidencias` re-checks the data with the shared
checker of :mod:`asistente.conocimiento` (the file is declared in ``FICHEROS_LEGALES``, so the weekly BOE
verification and the reading dates cover it): each figure is spelled in one of its quotes and, given the law texts,
each quote is verbatim in its law.

Figures that change during the year are stored as dated items (``desde``/``hasta``, both inclusive); the
calculators split a billing period wherever one of them changes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from asistente.ambitos.fiscal.calculo.autonomos_params import cifra_a_decimal
from asistente.conocimiento import (
    FRONTERA_CIFRA,
    VERSION_DIARIO,
    CitaLegal,
    FicheroLegal,
    GrupoLegal,
    Ortografia,
    citas_de_evidencia,
    fuente_de,
    hojas,
)
from asistente.conocimiento import textos_de_directorio as textos_de_directorio_comun

DATA = Path(__file__).resolve().parent / "data" / "regulados.json"
Periodo3 = Literal["p1", "p2", "p3"]
PERIODOS: tuple[Periodo3, ...] = ("p1", "p2", "p3")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FuentePrincipal(_Model):
    norma: str
    boe_id: str
    url: str
    # Consolidated texts: date of the last update shown by the BOE; diary texts: publication date.
    consolidado_a: str
    tipo_texto: Literal["consolidado", "diario"]
    consultado_el: str
    sha256_html: str


class Fuente(_Model):
    referencia: str
    en_vigor_desde: str
    verificado: bool
    nota: str | None = None


class Evidencia(_Model):
    boe_id: str
    articulo: str
    cita: str
    # Dotted path of a figure in its group -> the figure exactly as the quote spells it.
    cifras: dict[str, str] = Field(default_factory=dict)


class _Grupo(_Model):
    fuente: Fuente
    evidencia: list[Evidencia] = Field(min_length=1)


class _Vigencia(_Grupo):
    desde: date
    hasta: date

    def cubre(self, dia: date) -> bool:
        return self.desde <= dia <= self.hasta


class PrecioRegulado(_Vigencia):
    """Tolls (peajes) or charges (cargos) of 2.0TD: power in €/kW and year, energy in €/kWh."""

    potencia_eur_kw_anio: dict[Literal["p1", "p2"], Decimal]
    energia_eur_kwh: dict[Periodo3, Decimal]


class BonoSocial(_Vigencia):
    eur_cups_anio: Decimal


class AlquilerContador(_Grupo):
    monofasico_eur_mes: Decimal
    trifasico_eur_mes: Decimal


class PVPC(_Grupo):
    ccf_eur_kw_anio: Decimal


class IEEGeneral(_Grupo):
    tipo: Decimal
    minimo_eur_mwh: Decimal


class IVAGeneral(_Grupo):
    tipo: Decimal


class TramoFiscal(_Vigencia):
    """A temporary tax measure for some days: applied, not applied, or conditional on a CPI figure."""

    estado: Literal["aplicada", "no_aplicada", "condicional"]
    iee_tipo: Decimal | None = None
    iee_minimo_eur_mwh: Decimal | None = None
    iva_tipo: Decimal | None = None
    iva_potencia_max_kw: Decimal | None = None
    condicion: str | None = None


class Impuestos(_Model):
    iee_general: IEEGeneral
    iva_general: IVAGeneral
    tramos_temporales: list[TramoFiscal]


class HorasPeriodo(_Model):
    p1: list[tuple[int, int]]
    p2: list[tuple[int, int]]
    p3: list[tuple[int, int]]


class Periodos2TD(_Grupo):
    peninsula_baleares_canarias: HorasPeriodo
    ceuta_melilla: HorasPeriodo


class Festivo(_Model):
    fecha: date
    boe_id: str
    cita: str


class Regulados(_Model):
    consultado_el: str
    fuentes_principales: dict[str, FuentePrincipal]
    no_modelado: list[str]
    periodos_2_0td: Periodos2TD
    festivos: list[Festivo]
    festivos_nota: str
    peajes: list[PrecioRegulado]
    cargos: list[PrecioRegulado]
    bono_social: list[BonoSocial]
    alquiler_contador: AlquilerContador
    pvpc: PVPC
    impuestos: Impuestos

    # --- lookups ------------------------------------------------------------------------------------

    def _vigente[T: _Vigencia](self, items: Sequence[T], dia: date, que: str) -> T:
        for item in items:
            if item.cubre(dia):
                return item
        raise DatoNoDisponibleError(
            f"No hay {que} cargados para el {dia:%d-%m-%Y} (los datos cubren "
            f"{min(i.desde for i in items):%d-%m-%Y} a {max(i.hasta for i in items):%d-%m-%Y}). "
            "Hay que leer en el BOE la norma de ese año y añadirla a los datos del módulo."
        )

    def peaje(self, dia: date) -> PrecioRegulado:
        return self._vigente(self.peajes, dia, "peajes de transporte y distribución")

    def cargo(self, dia: date) -> PrecioRegulado:
        return self._vigente(self.cargos, dia, "cargos del sistema eléctrico")

    def bono(self, dia: date) -> BonoSocial:
        return self._vigente(self.bono_social, dia, "valores de financiación del bono social")

    def tramo_fiscal(self, dia: date) -> TramoFiscal | None:
        return next((t for t in self.impuestos.tramos_temporales if t.cubre(dia)), None)

    def anios_festivos(self) -> set[int]:
        return {f.fecha.year for f in self.festivos}

    def citas(self, *boe_ids: str) -> tuple[str, ...]:
        return tuple(f"{self.fuentes_principales[b].norma} ({b}, {self.fuentes_principales[b].url})" for b in boe_ids)

    def grupos(self) -> dict[str, _Grupo]:
        found: dict[str, _Grupo] = {
            "periodos_2_0td": self.periodos_2_0td,
            "alquiler_contador": self.alquiler_contador,
            "pvpc": self.pvpc,
            "iee_general": self.impuestos.iee_general,
            "iva_general": self.impuestos.iva_general,
        }
        for nombre, items in (
            ("peajes", self.peajes),
            ("cargos", self.cargos),
            ("bono_social", self.bono_social),
            ("tramos_temporales", self.impuestos.tramos_temporales),
        ):
            for item in items:
                found[f"{nombre}[{item.desde}]"] = item
        return found


class DatoNoDisponibleError(LookupError):
    """A regulated figure is not loaded for that date. The message is Spanish, for the user."""


@lru_cache(maxsize=1)
def cargar_regulados() -> Regulados:
    return Regulados.model_validate(json.loads(DATA.read_text(encoding="utf-8")))


# --- Self-check against the law (the shared checker of asistente.conocimiento) ----------------------

_NO_CIFRAS = ("fuente", "evidencia", "desde", "hasta", "estado", "condicion")
ORTOGRAFIA = Ortografia(leer_cifra=cifra_a_decimal, frontera=FRONTERA_CIFRA)


def _hojas(value: object, prefix: str = "") -> dict[str, Decimal]:
    """Every Decimal of a group by dotted path (dates, states and conditions are not figures)."""
    return {
        ruta: valor
        for ruta, valor in hojas(value, excluir=_NO_CIFRAS, listas=False, prefijo=prefix).items()
        if isinstance(valor, Decimal)
    }


def grupos_legales(datos: Regulados) -> list[GrupoLegal]:
    """The file as groups of the shared library: every regulated group, and each holiday row with its quote."""
    versiones: dict[str, str | None] = {
        b: VERSION_DIARIO if f.tipo_texto == "diario" else None for b, f in datos.fuentes_principales.items()
    }
    out = [
        GrupoLegal(
            nombre=nombre,
            ruta=[nombre],
            descripcion=f"{nombre} ({grupo.fuente.referencia})",
            fuente=fuente_de(grupo.fuente),
            cifras=dict(_hojas(grupo)),
            citas=citas_de_evidencia(grupo.evidencia, versiones=versiones),
        )
        for nombre, grupo in datos.grupos().items()
    ]
    for festivo in datos.festivos:
        nombre = f"festivos[{festivo.fecha}]"
        out.append(
            GrupoLegal(
                nombre=nombre,
                ruta=["festivos", nombre],
                descripcion=f"fiesta nacional del {festivo.fecha:%d-%m-%Y}",
                citas=[
                    CitaLegal(
                        boe_id=festivo.boe_id,
                        version=versiones.get(festivo.boe_id),
                        articulo="anexo",
                        cita=festivo.cita,
                    )
                ],
            )
        )
    return out


def _compacto(text: str) -> str:
    return " ".join(text.split())


_MESES = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)


def _mes_de_la_fila(texto: str, cita: str) -> int | None:
    """Month heading (a line 'Enero', 'Febrero'...) under which a holiday row is printed."""
    lineas = texto.splitlines()
    mes = None
    for linea in lineas:
        limpia = linea.strip()
        if limpia in _MESES:
            mes = _MESES.index(limpia) + 1
        elif _compacto(limpia) == _compacto(cita):
            return mes
    return None


def _comprobaciones_propias(datos: Regulados, textos: Mapping[str, str] | None) -> list[str]:
    """What only this module knows: the hour ranges of the periods and the layout of the holidays table."""
    problemas: list[str] = []
    # Hour ranges of the periods: every "a h-b h" must be printed in the table quote, in this order.
    tabla = _compacto(datos.periodos_2_0td.evidencia[0].cita)
    esperado: list[str] = []
    for horas in (datos.periodos_2_0td.peninsula_baleares_canarias, datos.periodos_2_0td.ceuta_melilla):
        for periodo in PERIODOS:
            esperado.extend(f"{a} h-{b} h" for a, b in getattr(horas, periodo))
    if " ".join(esperado) not in tabla:
        problemas.append("periodos_2_0td: las franjas horarias no coinciden con la tabla citada")

    # Holidays: the quote is the row of the BOE table, with the day number first and under its month.
    for festivo in datos.festivos:
        nombre = f"festivos[{festivo.fecha}]"
        if not festivo.cita.startswith(f"{festivo.fecha.day} "):
            problemas.append(f"{nombre}: la fila citada no empieza por el día {festivo.fecha.day}")
        if festivo.cita.count("*") != 19 or "**" in festivo.cita:
            problemas.append(f"{nombre}: la fila no es de fiesta nacional no sustituible en todas las comunidades")
        if textos is not None and festivo.boe_id in textos:
            mes = _mes_de_la_fila(textos[festivo.boe_id], festivo.cita)
            if mes != festivo.fecha.month:
                problemas.append(f"{nombre}: la fila está bajo el mes {mes}, no {festivo.fecha.month}")
    return problemas


FICHEROS_LEGALES = (
    FicheroLegal(
        clave="luz",
        modulo="luz",
        titulo="Luz: cifras reguladas de la factura",
        ruta=DATA,
        cargar=Regulados.model_validate,
        grupos=grupos_legales,
        ortografia=ORTOGRAFIA,
        fuentes=lambda datos: set(datos.fuentes_principales),
        propias=_comprobaciones_propias,
        limpiar=lambda: cargar_regulados.cache_clear(),
        solo_persona=(
            "Peajes, cargos y bono social se fijan cada año en una resolución de la CNMC o una orden ministerial "
            "nueva (texto del BOE diario, no consolidado), con tablas por período: no es la nueva redacción de un "
            "artículo que el bucle pueda releer. Al publicarse la del año siguiente, una persona la lee y añade sus "
            "cifras con su cita; la verificación semanal comprueba después cada cita y cada cifra."
        ),
    ),
)


def comprobar_evidencias(datos: Regulados, textos: dict[str, str] | None = None) -> list[str]:
    """Problems found re-reading the data against its quotes (and, with ``textos``, against the law).

    ``textos`` maps BOE id -> plain text of the page (or an extract with the quoted paragraphs). The checks are
    the shared ones of :mod:`asistente.conocimiento` plus this module's own (period table, holidays).
    """
    return FICHEROS_LEGALES[0].comprobar(datos, textos)


def textos_de_directorio(directorio: Path) -> dict[str, str]:
    """BOE id -> plain text, from ``<BOE-ID>.html`` (pages from boe.es) or ``<BOE-ID>.txt`` (extracts)."""
    return textos_de_directorio_comun(directorio)


def main(argv: list[str] | None = None) -> int:
    """``python -m asistente.ambitos.luz.datos <dir with BOE-*.html>``: re-check against the law."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("uso: python -m asistente.ambitos.luz.datos <directorio con BOE-*.html>")
        return 2
    problemas = comprobar_evidencias(cargar_regulados(), textos_de_directorio(Path(args[0])))
    for problema in problemas:
        print(problema)
    print("Datos coherentes con el texto." if not problemas else f"{len(problemas)} problema(s).")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())

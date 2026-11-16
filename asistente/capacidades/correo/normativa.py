"""The legal rules the module quotes (``data/notificaciones.json``), read from the BOE with literal quotes.

* Ley 39/2015, art. 43.2: an electronic notification is deemed rejected after ten calendar days without
  being opened (when electronic notification is compulsory or was chosen) — used for the deadline of an
  administration notice;
* Ley 39/2015, art. 41.6 and RD 203/2021 art. 43.1: the e-mail notice is merely informative;
* RD 203/2021 art. 18.2: state e-offices (sedes electrónicas) use the «.gob.es» domain — a phishing signal.

``python -m asistente.capacidades.correo.normativa <dir with BOE-A-*.html|txt>`` re-checks every quote with the
shared checker of :mod:`asistente.conocimiento`; the file is declared in ``FICHEROS_LEGALES``, so the weekly BOE
verification and the reading dates cover it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
import sys

from pydantic import BaseModel, ConfigDict, Field

from asistente.conocimiento import (
    FRONTERA_PALABRA,
    FicheroLegal,
    GrupoLegal,
    Ortografia,
    citas_de_evidencia,
    fuente_de,
)

DATOS = Path(__file__).resolve().parent / "data" / "notificaciones.json"


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FuentePrincipal(_Modelo):
    norma: str
    boe_id: str
    url: str
    consolidado_a: date
    consultado_el: date
    sha256_html: str = Field(pattern=r"^[0-9a-f]{64}$")


class FuenteRegla(_Modelo):
    referencia: str
    en_vigor_desde: str
    verificado: bool
    nota: str = ""


class Evidencia(_Modelo):
    boe_id: str
    articulo: str
    cita: str
    cifras: dict[str, str] = Field(default_factory=dict)


class Regla(_Modelo):
    titulo: str
    resumen: str
    fuente: FuenteRegla
    valores: dict[str, Decimal] = Field(default_factory=dict)
    evidencia: list[Evidencia] = Field(min_length=1)


class Normativa(_Modelo):
    version: str
    descripcion: str
    fuentes_principales: dict[str, FuentePrincipal]
    reglas: dict[str, Regla]

    def regla(self, clave: str) -> Regla:
        return self.reglas[clave]

    def entero(self, clave: str, valor: str) -> int:
        return int(self.reglas[clave].valores[valor])

    def url(self, boe_id: str) -> str:
        return self.fuentes_principales[boe_id].url

    def citas(self, clave: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.url(ev.boe_id) for ev in self.reglas[clave].evidencia))


@lru_cache(maxsize=1)
def cargar() -> Normativa:
    return Normativa.model_validate_json(DATOS.read_text(encoding="utf-8"))


_NUMEROS = {
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "quince": 15,
    "veinte": 20,
    "treinta": 30,
}


def cifra_a_decimal(literal: str) -> Decimal | None:
    """The number a quoted figure states («diez días naturales» -> 10, «15 días» -> 15)."""
    primera = literal.strip().split()[0].lower() if literal.strip() else ""
    if primera in _NUMEROS:
        return Decimal(_NUMEROS[primera])
    try:
        return Decimal(primera.replace(".", "").replace(",", "."))
    except ArithmeticError:
        return None


def _leer(literal: str) -> Decimal:
    valor = cifra_a_decimal(literal)
    if valor is None:
        raise ValueError(literal)
    return valor


ORTOGRAFIA = Ortografia(leer_cifra=_leer, frontera=FRONTERA_PALABRA)


def grupos_legales(datos: Normativa) -> list[GrupoLegal]:
    """The file as groups of the shared library: each rule, its figures (``valores``) and its quotes."""
    return [
        GrupoLegal(
            nombre=clave,
            ruta=["reglas", clave],
            descripcion=f"{regla.titulo} ({regla.fuente.referencia})",
            fuente=fuente_de(regla.fuente),
            cifras=dict(regla.valores),
            citas=citas_de_evidencia(regla.evidencia),
        )
        for clave, regla in datos.reglas.items()
    ]


FICHEROS_LEGALES = (
    FicheroLegal(
        clave="correo",
        modulo="correo",
        titulo="Correo: reglas de las notificaciones administrativas",
        ruta=DATOS,
        cargar=Normativa.model_validate,
        grupos=grupos_legales,
        ortografia=ORTOGRAFIA,
        fuentes=lambda datos: set(datos.fuentes_principales),
        limpiar=lambda: cargar.cache_clear(),
        solo_persona=(
            "Tres reglas de procedimiento (un plazo de diez días, el aviso informativo, el dominio de las sedes) "
            "que el módulo explica y aplica a avisos y enlaces, no a importes: no hay casos de prueba que "
            "contrasten una propuesta. Una persona las relee; la verificación semanal comprueba cada cita y cifra."
        ),
    ),
)


def comprobar(datos: Normativa, textos: dict[str, str] | None = None) -> list[str]:
    """Problems re-reading the data against its quotes (and against the laws, given ``textos``), with the shared
    checker of :mod:`asistente.conocimiento`."""
    return FICHEROS_LEGALES[0].comprobar(datos, textos)


def main(argv: list[str] | None = None) -> int:
    from asistente.ambitos.fiscal.calculo.autonomos_params import textos_de_directorio

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("uso: python -m asistente.capacidades.correo.normativa <directorio con BOE-A-*.html|txt>")
        return 2
    problemas = comprobar(cargar(), textos_de_directorio(Path(args[0])))
    for problema in problemas:
        print(problema)
    print("Datos coherentes con el texto de la ley." if not problemas else f"{len(problemas)} problema(s).")
    return 1 if problemas else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["FICHEROS_LEGALES", "Normativa", "Regla", "cargar", "cifra_a_decimal", "comprobar"]

"""Consumer-rights data read from the BOE (``data/derechos_consumidor.json``) and its self-check.

Each right carries literal quotes of the consolidated law (``evidencia``) and the figures read from them
(``valores``, with the literal spelling in ``cifras``). :func:`comprobar` re-reads the data with the shared
checker of :mod:`asistente.conocimiento`: every figure must be spelled in one of its quotes and mean the stored
value, and, given the law texts, every quote must appear verbatim in its law. The file is declared in
``FICHEROS_LEGALES``: the weekly BOE verification and the reading dates cover it. ``python -m asistente.ambitos.suscripciones.derechos <dir>`` runs the check
against freshly downloaded pages (``<BOE-ID>.html`` or ``.txt``).
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import sys

from pydantic import BaseModel, ConfigDict, Field

from asistente.ambitos.fiscal.calculo.params import Fuente, FuentePrincipal
from asistente.conocimiento import (
    FRONTERA_PALABRA,
    FicheroLegal,
    GrupoLegal,
    Ortografia,
    citas_de_evidencia,
    fuente_de,
)
from asistente.conocimiento import textos_de_directorio as _textos_de_directorio

DATA = Path(__file__).parent / "data" / "derechos_consumidor.json"


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidencia(_Modelo):
    boe_id: str
    articulo: str
    cita: str
    cifras: dict[str, str] = Field(default_factory=dict)


class Derecho(_Modelo):
    titulo: str
    resumen: str
    sectores: list[str]
    fuente: Fuente
    valores: dict[str, Decimal] = Field(default_factory=dict)
    evidencia: list[Evidencia] = Field(min_length=1)


class DerechosConsumidor(_Modelo):
    version: str
    descripcion: str
    fuentes_principales: dict[str, FuentePrincipal]
    no_modelado: list[str]
    derechos: dict[str, Derecho]

    def derecho(self, clave: str) -> Derecho:
        return self.derechos[clave]

    def valor(self, clave: str, campo: str) -> Decimal:
        return self.derechos[clave].valores[campo]

    def entero(self, clave: str, campo: str) -> int:
        return int(self.valor(clave, campo))

    def url(self, boe_id: str) -> str:
        return self.fuentes_principales[boe_id].url

    def citas(self, *claves: str) -> tuple[str, ...]:
        """Sources of the given rights, as 'BOE-A-… (url)' for the tool's ``citas``."""
        vistos: list[str] = []
        for clave in claves:
            for ev in self.derechos[clave].evidencia:
                texto = f"{ev.boe_id} {self.url(ev.boe_id)}"
                if texto not in vistos:
                    vistos.append(texto)
        return tuple(vistos)

    def norma(self, boe_id: str) -> str:
        return self.fuentes_principales[boe_id].norma.removesuffix(" (texto consolidado)")


@lru_cache(maxsize=1)
def cargar() -> DerechosConsumidor:
    return DerechosConsumidor.model_validate(json.loads(DATA.read_text(encoding="utf-8")))


_PALABRAS = {
    "un": 1,
    "una": 1,
    "dos": 2,
    "cinco": 5,
    "ocho": 8,
    "diez": 10,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "veinticuatro": 24,
}


def cifra_a_decimal(literal: str) -> Decimal:
    """'catorce días naturales' -> 14; 'un mes' -> 1; '250 personas' -> 250."""
    token = literal.split()[0].lower()
    if token in _PALABRAS:
        return Decimal(_PALABRAS[token])
    return Decimal(token.replace(".", "").replace(",", "."))


ORTOGRAFIA = Ortografia(leer_cifra=cifra_a_decimal, frontera=FRONTERA_PALABRA)


def grupos_legales(datos: DerechosConsumidor) -> list[GrupoLegal]:
    """The file as groups of the shared library: each right, its figures (``valores``) and its quotes."""
    return [
        GrupoLegal(
            nombre=clave,
            ruta=["derechos", clave],
            descripcion=f"{derecho.titulo} ({derecho.fuente.referencia})",
            fuente=fuente_de(derecho.fuente),
            cifras=dict(derecho.valores),
            citas=citas_de_evidencia(derecho.evidencia),
        )
        for clave, derecho in datos.derechos.items()
    ]


FICHEROS_LEGALES = (
    FicheroLegal(
        clave="suscripciones",
        modulo="suscripciones",
        titulo="Suscripciones: derechos de la persona consumidora",
        ruta=DATA,
        cargar=DerechosConsumidor.model_validate,
        grupos=grupos_legales,
        ortografia=ORTOGRAFIA,
        fuentes=lambda datos: set(datos.fuentes_principales),
        limpiar=lambda: cargar.cache_clear(),
        solo_persona=(
            "Cada derecho lleva un resumen en lenguaje llano que el usuario lee como consejo, y ninguna calculadora "
            "convierte sus plazos en un importe que un caso de prueba pueda contrastar. Una persona los relee; la "
            "verificación semanal comprueba cada cita y cada cifra."
        ),
    ),
)


def comprobar(datos: DerechosConsumidor, textos: dict[str, str] | None = None) -> list[str]:
    """Problems found re-reading the data against its quotes (and against the laws, given ``textos``), with the
    shared checker of :mod:`asistente.conocimiento`."""
    return FICHEROS_LEGALES[0].comprobar(datos, textos)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("uso: python -m asistente.ambitos.suscripciones.derechos <directorio con BOE-A-*.html|txt>")
        return 2
    problemas = comprobar(cargar(), _textos_de_directorio(Path(args[0]), "BOE-A-*"))
    for problema in problemas:
        print(problema)
    print("Datos coherentes con el texto de la ley." if not problemas else f"{len(problemas)} problema(s).")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())

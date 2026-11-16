"""The legal rules the module quotes (``data/notificaciones.json``), read from the BOE with literal quotes.

* Ley 39/2015, art. 43.2: an electronic notification is deemed rejected after ten calendar days without
  being opened (when electronic notification is compulsory or was chosen) — used for the deadline of an
  administration notice;
* Ley 39/2015, art. 41.6 and RD 203/2021 art. 43.1: the e-mail notice is merely informative;
* RD 203/2021 art. 18.2: state e-offices (sedes electrónicas) use the «.gob.es» domain — a phishing signal.

``python -m hacienda_gpt.modulos.correo.normativa <dir with BOE-A-*.html|txt>`` re-checks every quote.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
import re
import sys

from pydantic import BaseModel, ConfigDict, Field

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


def _compacto(texto: str) -> str:
    return " ".join(texto.split())


def comprobar(datos: Normativa, textos: dict[str, str] | None = None) -> list[str]:
    """Problems re-reading the data against its quotes (and against the laws, given ``textos``)."""
    problemas: list[str] = []
    for clave, regla in datos.reglas.items():
        sin_evidencia = set(regla.valores)
        for ev in regla.evidencia:
            if ev.boe_id not in datos.fuentes_principales:
                problemas.append(f"{clave}: {ev.boe_id} no figura en fuentes_principales")
            if textos is not None:
                texto = textos.get(ev.boe_id)
                if texto is None:
                    problemas.append(f"{clave}: no se ha aportado el texto de {ev.boe_id}")
                elif _compacto(ev.cita) not in _compacto(texto):
                    problemas.append(f"{clave}: la cita de {ev.articulo} no aparece literal en {ev.boe_id}")
            for campo, literal in ev.cifras.items():
                if campo not in regla.valores:
                    problemas.append(f"{clave}: la evidencia cita una cifra inexistente ({campo})")
                    continue
                if not re.search(rf"(?<![\w.,]){re.escape(literal)}(?![\w]|[.,]\d)", ev.cita):
                    problemas.append(f"{clave}.{campo}: «{literal}» no aparece en la cita de {ev.articulo}")
                    continue
                if cifra_a_decimal(literal) != regla.valores[campo]:
                    problemas.append(f"{clave}.{campo}: el dato es {regla.valores[campo]} y la cita dice «{literal}»")
                sin_evidencia.discard(campo)
        problemas.extend(f"{clave}.{campo}: cifra sin evidencia" for campo in sorted(sin_evidencia))
        try:
            date.fromisoformat(regla.fuente.en_vigor_desde)
        except ValueError:
            problemas.append(f"{clave}: en_vigor_desde no es una fecha")
    return problemas


def main(argv: list[str] | None = None) -> int:
    from hacienda_gpt.tax.autonomos_params import textos_de_directorio

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("uso: python -m hacienda_gpt.modulos.correo.normativa <directorio con BOE-A-*.html|txt>")
        return 2
    problemas = comprobar(cargar(), textos_de_directorio(Path(args[0])))
    for problema in problemas:
        print(problema)
    print("Datos coherentes con el texto de la ley." if not problemas else f"{len(problemas)} problema(s).")
    return 1 if problemas else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["Normativa", "Regla", "cargar", "cifra_a_decimal", "comprobar"]

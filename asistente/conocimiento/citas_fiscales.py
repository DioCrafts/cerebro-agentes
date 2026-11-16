"""Passages of tax law the tax module keeps in its code, declared for the shared legal-data library.

The tax parameter FILES (``irpf_<year>.json`` and the others) have their own verification in
``asistente.conocimiento.verificacion`` and ``params-verify`` already covers them. What lives in code is declared here,
read through public names only: the passages the filing-obligation answer quotes (``CITAS`` of
``asistente.ambitos.fiscal.calculo.obligacion``, some stitched with "[…]") and those the optimiser shows
(``TEXTOS_LEGALES`` of ``asistente.ambitos.fiscal.calculo.optimizador``). They carry no figure of their own (the figures they print
are the parameter files', checked there) and no reading date: they are checked verbatim against today's BOE every
week. When the tax module owns its declarations, this file moves into it.
"""

from __future__ import annotations

from typing import Any

from asistente.conocimiento.comprobacion import partes_de
from asistente.conocimiento.modelo import CitaLegal, GrupoLegal, Ortografia
from asistente.conocimiento.registro import FicheroLegal


def _pasajes(nombre: str) -> Any:
    import importlib

    modulo, _, atributo = nombre.rpartition(".")
    return getattr(importlib.import_module(modulo), atributo)


def _grupos(pasajes: dict[str, Any]) -> list[GrupoLegal]:
    return [
        GrupoLegal(
            nombre=clave,
            ruta=[clave],
            descripcion=str(pasaje.referencia),
            citas=[
                CitaLegal(
                    boe_id=str(pasaje.boe_id),
                    articulo=str(pasaje.referencia),
                    cita=str(pasaje.texto),
                    cortes=len(partes_de(str(pasaje.texto))) > 1,
                )
            ],
        )
        for clave, pasaje in pasajes.items()
    ]


_SIN_CIFRAS = Ortografia(leer_cifra=lambda literal: literal)
_MOTIVO = (
    "Pasajes sin cifras propias que el módulo fiscal muestra al usuario; están en el código, que una persona "
    "cambia. Las cifras que imprimen son las de los ficheros de parámetros, que sí recodifica el bucle."
)

FICHEROS_LEGALES = (
    FicheroLegal(
        clave="fiscal_obligacion",
        modulo="fiscal",
        titulo="Fiscal: pasajes de la obligación de declarar",
        ruta=None,
        cargar=lambda _raw: _pasajes("asistente.ambitos.fiscal.calculo.obligacion.CITAS"),
        grupos=_grupos,
        ortografia=_SIN_CIFRAS,
        solo_persona=_MOTIVO,
    ),
    FicheroLegal(
        clave="fiscal_optimizador",
        modulo="fiscal",
        titulo="Fiscal: pasajes que muestra el optimizador",
        ruta=None,
        cargar=lambda _raw: _pasajes("asistente.ambitos.fiscal.calculo.optimizador.TEXTOS_LEGALES"),
        grupos=_grupos,
        ortografia=_SIN_CIFRAS,
        solo_persona=_MOTIVO,
    ),
)

__all__ = ["FICHEROS_LEGALES"]

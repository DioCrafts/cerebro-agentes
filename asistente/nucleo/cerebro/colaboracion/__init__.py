"""The household brain's collaboration runtime (see ``docs/cerebro/colaboracion.md``).

* :class:`Despachador` (``despacho``) — runs a turn's encargos as a DAG, in parallel, with timeouts, the question's
  budget, dedupe and failure isolation; reports come back in plan order with turn-unique figure ids.
* :class:`Pizarra` (``pizarra``) — the brain's switchboard: a specialist asks another one for a datum THROUGH it
  (bounded depth and count, no cycles, reuse, single-flight, audited).
* :class:`EspecialistaDeModulo` (``modulos``) — a household module as a specialist of the brain.
* :func:`extraer_cifras` / :func:`escanear` (``cifras``) — Spanish figures in text, and what is not a figure.
* :func:`revisar_cifras` / :func:`mensaje_de_revision` (``puerta``) — the figures gate on the final answer.
* :func:`material_de_sintesis` / :func:`tabla_de_procedencia` (``sintesis``) — the synthesis' data block and the UI's
  provenance table.
"""

from asistente.nucleo.cerebro.colaboracion.cifras import Numero, escanear, extraer_cifras, formatear_valor
from asistente.nucleo.cerebro.colaboracion.despacho import Despachador, ondas
from asistente.nucleo.cerebro.colaboracion.modulos import EspecialistaDeModulo
from asistente.nucleo.cerebro.colaboracion.pizarra import LineaPizarra, Pizarra, RegistroPeticion
from asistente.nucleo.cerebro.colaboracion.puerta import (
    Combinacion,
    buscar_combinacion,
    mensaje_de_revision,
    revisar_cifras,
)
from asistente.nucleo.cerebro.colaboracion.sintesis import material_de_sintesis, tabla_de_procedencia

__all__ = [
    "Combinacion",
    "Despachador",
    "EspecialistaDeModulo",
    "LineaPizarra",
    "Numero",
    "Pizarra",
    "RegistroPeticion",
    "buscar_combinacion",
    "escanear",
    "extraer_cifras",
    "formatear_valor",
    "material_de_sintesis",
    "mensaje_de_revision",
    "ondas",
    "revisar_cifras",
    "tabla_de_procedencia",
]

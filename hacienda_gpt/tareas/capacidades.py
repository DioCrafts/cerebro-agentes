"""Capabilities a task step can use: e-mail, browser, documents, research.

A step's capability is a small adapter with a CLOSED list of operations, each with a JSON Schema for its
parameters (checked by the plan gate, :mod:`hacienda_gpt.tareas.validacion`). E-mail, browser and documents are
the REGISTERED capabilities (``hacienda_gpt/capacidades``), run through ``capacidades.pedir`` with the core as the
caller (``ambito="nucleo"``, ``origen="tarea"``): consent comes from the modules on for the case's owner, and the
result type is the capabilities' own (``capacidades.contrato.ResultadoCapacidad``). Every operation offered here
is deterministic, never sends or submits anything, and degrades to «the user does it» when the capability is off:

* ``correo`` — ``resumen``: what the Gmail capability (read-only) classified, when the user switched it on;
  otherwise the step says so.
* ``navegador`` — ``consumo_datadis``: tells the user how to get the hourly consumption into the electricity
  module: with the browser agent (operator switch and module on) from the «Navegador» panel, where THEY press
  «Empezar» and log in; otherwise downloading the CSV themselves. The browser task itself lives in the API
  process, so the step never starts it: it explains and a later ``espera`` step waits for the data to arrive.
* ``documentos`` — ``lista_de_comprobacion``, ``borrador`` (never sent), ``pedir_documento``
  (``capacidades/documentos/tramites.py``).
* ``investigacion`` — ``webs_oficiales``: official information about a procedure, ONLY under the official
  site roots in :func:`raices_oficiales` (from the modules' versioned data). Without a configured searcher
  (none on this machine: only www.boe.es is reachable) it points the user to those roots and says it did not
  read them; a searcher's result outside the roots is dropped. By default the step uses
  ``capacidades.investigacion.tareas.CapacidadInvestigacionTareas``, which adds ``investigar`` (the research
  capability, through ``pedir``).

Tests replace any capability with a fake through :class:`hacienda_gpt.tareas.motor.Motor`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
import logging
from typing import Any, Protocol

from hacienda_gpt.capacidades.contrato import ContextoPeticion, PeticionDenegada, pedir
from hacienda_gpt.capacidades.contrato import ResultadoCapacidad as ResultadoComun
from hacienda_gpt.capacidades.documentos import tramites
from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.modulos.contrato import Modulo

logger = logging.getLogger(__name__)

RAIZ_BOE = "https://www.boe.es/"


@dataclass(frozen=True)
class Operacion:
    nombre: str
    descripcion: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ContextoCapacidad:
    case: CaseState
    hoy: date
    modulos_activos: Sequence[Modulo]
    # Stable per (task, step): what an operation creates elsewhere must use it, so a retry repeats nothing.
    clave: str
    perfil: Mapping[str, Any] = field(default_factory=dict)

    def modulo(self, clave: str) -> Modulo | None:
        return next((m for m in self.modulos_activos if m.clave == clave), None)


# The tasks engine's result IS the capabilities' result (``capacidades/contrato.py``): ``resumen``, ``estado``,
# ``borrador``, ``aviso``, ``es_error`` and ``evidencias`` (its ``citas``).
ResultadoCapacidad = ResultadoComun


class Capacidad(Protocol):
    @property
    def clave(self) -> str: ...

    @property
    def nombre(self) -> str: ...

    @property
    def operaciones(self) -> Mapping[str, Operacion]: ...

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad: ...


def _schema(propiedades: dict[str, Any], requeridas: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": propiedades,
        **({"required": list(requeridas)} if requeridas else {}),
    }


# --------------------------------------------------------------------------- #
# Registered capabilities, through ``pedir``
# --------------------------------------------------------------------------- #


class CapacidadPorPedir:
    """A task-step view of a registered capability: a CLOSED list of its operations, run with ``pedir``.

    ``pedir`` enforces the contract (consent from the case owner's active modules, effect, input schema, metering
    purpose, untrusted results). A step whose capability the user has not switched on gets ``sin_consentimiento``
    (the user does it); any other refusal is a failed step.
    """

    clave: str = ""
    nombre: str = ""
    expuestas: tuple[str, ...] = ()
    sin_consentimiento: str = ""

    @property
    def operaciones(self) -> Mapping[str, Operacion]:
        from hacienda_gpt.capacidades.registro import registro

        salida: dict[str, Operacion] = {}
        for nombre in self.expuestas:
            op = registro().operacion(self.clave, nombre)
            if op is not None:
                salida[nombre] = Operacion(nombre, op.descripcion, dict(op.entrada))
        return salida

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        if operacion not in self.expuestas:
            return ResultadoCapacidad(resumen=f"La operación {operacion} no está disponible.", es_error=True)
        contexto = ContextoPeticion(
            ambito="nucleo",
            hoy=ctx.hoy,
            case_id=ctx.case.case_id,
            perfil=dict(ctx.perfil),
            activos=frozenset(m.clave for m in ctx.modulos_activos),
            clave_idempotencia=ctx.clave,
            origen="tarea",
        )
        try:
            return pedir(self.clave, operacion, parametros, contexto)
        except PeticionDenegada as exc:
            if exc.motivo == "consentimiento" and self.sin_consentimiento:
                return ResultadoCapacidad(resumen=self.sin_consentimiento)
            return ResultadoCapacidad(resumen=str(exc), es_error=True)


class CapacidadCorreo(CapacidadPorPedir):
    clave = "correo"
    nombre = "Correo (solo lectura)"
    expuestas = ("resumen",)
    sin_consentimiento = (
        "El módulo de correo no está activado: no se ha leído ningún correo. Si quieres que se use, "
        "actívalo en «Módulos»; si no, aporta tú los datos."
    )


class CapacidadNavegador(CapacidadPorPedir):
    clave = "navegador"
    nombre = "Agente con navegador"
    expuestas = ("consumo_datadis",)


ORIGENES_LISTA = tramites.ORIGENES_LISTA
MODELOS_BORRADOR = tramites.MODELOS_BORRADOR


class CapacidadDocumentos(CapacidadPorPedir):
    clave = "documentos"
    nombre = "Documentos"
    expuestas = ("lista_de_comprobacion", "borrador", "pedir_documento")


# --------------------------------------------------------------------------- #
# Research (official sites only)
# --------------------------------------------------------------------------- #

# Searcher: (topic, roots) -> [(url, extract)]. None on this machine.
Buscador = Callable[[str, Sequence[str]], Sequence[tuple[str, str]]]


@lru_cache(maxsize=1)
def raices_oficiales() -> frozenset[str]:
    """The official site roots research may use: the BOE and the procedures' sites of the modules' data."""
    raices = {RAIZ_BOE}
    try:
        from hacienda_gpt.modulos.caducidades.reglas import cargar_reglas

        for grupo in cargar_reglas().grupos().values():
            tramite = getattr(grupo, "tramite", None)
            raices.update(str(w) for w in (getattr(tramite, "webs", None) or ()))
    except Exception:  # the expiry module's data unreadable: only the BOE
        logger.exception("Official roots of the expiry module could not be read")
    return frozenset(r if r.endswith("/") else r + "/" for r in raices)


def bajo_raiz(url: str, raices: Sequence[str]) -> bool:
    return any(url == r.rstrip("/") or url.startswith(r) for r in raices)


class CapacidadInvestigacion:
    clave = "investigacion"
    nombre = "Investigación en webs oficiales"

    def __init__(self, buscador: Buscador | None = None) -> None:
        self.buscador = buscador
        self.operaciones = {
            "webs_oficiales": Operacion(
                "webs_oficiales",
                "Información oficial de un trámite, solo de las webs oficiales indicadas.",
                _schema(
                    {
                        "tema": {"type": "string", "minLength": 3, "maxLength": 120},
                        "raices": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 5,
                            "items": {"type": "string", "enum": sorted(raices_oficiales())},
                        },
                    },
                    ["tema", "raices"],
                ),
            )
        }

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        tema = str(parametros["tema"])
        raices = [str(r) for r in parametros["raices"] if str(r) in raices_oficiales()]
        if self.buscador is None:
            return ResultadoCapacidad(
                resumen=(
                    f"No he consultado automáticamente estas webs (desde este servidor no se llega a ellas). Para «{tema}», "
                    "la información oficial está en: " + ", ".join(raices) + "."
                ),
                citas=tuple(raices),
            )
        try:
            encontrados = list(self.buscador(tema, raices))
        except Exception:
            logger.exception("Official-site search failed")
            return ResultadoCapacidad(resumen="La búsqueda en las webs oficiales falló.", es_error=True)
        validos = [(u, t) for u, t in encontrados if bajo_raiz(u, raices)]
        descartados = len(encontrados) - len(validos)
        lineas = [f"- {t.strip()[:400]} ({u})" for u, t in validos[:8]] or ["- (no se ha encontrado nada)"]
        if descartados:
            lineas.append(f"Se han descartado {descartados} resultado(s) de webs no oficiales.")
        return ResultadoCapacidad(
            resumen=f"Información oficial sobre «{tema}»:\n" + "\n".join(lineas),
            citas=tuple(u for u, _t in validos[:8]) or tuple(raices),
        )


def capacidades_por_defecto(buscador: Buscador | None = None) -> dict[str, Capacidad]:
    # The research capability's task view: ``webs_oficiales`` (above) plus ``investigar`` through ``pedir``
    # (imported here: that module builds on this one).
    from hacienda_gpt.capacidades.investigacion.tareas import CapacidadInvestigacionTareas

    return {
        "correo": CapacidadCorreo(),
        "navegador": CapacidadNavegador(),
        "documentos": CapacidadDocumentos(),
        "investigacion": CapacidadInvestigacionTareas(buscador),
    }


__all__ = [
    "MODELOS_BORRADOR",
    "ORIGENES_LISTA",
    "RAIZ_BOE",
    "Buscador",
    "Capacidad",
    "CapacidadCorreo",
    "CapacidadDocumentos",
    "CapacidadInvestigacion",
    "CapacidadNavegador",
    "ContextoCapacidad",
    "Operacion",
    "ResultadoCapacidad",
    "bajo_raiz",
    "capacidades_por_defecto",
    "raices_oficiales",
]

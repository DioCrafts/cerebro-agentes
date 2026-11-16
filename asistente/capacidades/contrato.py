"""The contract of a CAPABILITY: something any domain can use (mail, browser, documents, research).

A domain (``ámbito``: fiscal, luz, caducidades…; see ``asistente/nucleo/modulos/contrato.py``) owns data,
calculators and notices about one area of the household. A capability owns a MEANS: reading the user's mail,
driving a browser on an official website, reading an uploaded document, searching with sources. Domains never
import each other's or a capability's internals: they ask through :func:`pedir`::

    resultado = pedir("documentos", "leer_campos", {...}, ContextoPeticion(ambito="caducidades", hoy=hoy))

A capability is a package ``asistente.capacidades.<clave>`` whose ``__init__`` exposes ``CAPACIDAD``, an
object satisfying :class:`Capacidad`. Each operation declares:

* its input and output as JSON Schema objects (checked by :func:`pedir` on every call, both ways);
* its effect: ``lectura`` (reads, no side effect), ``prepara`` (prepares something the USER must confirm before
  it is applied: the result is a proposal and :func:`pedir` marks it so) or ``actua`` (acts outside the app:
  never runs without a :class:`Confirmacion` of the user for exactly that operation and those arguments);
* the metering purpose of the model calls it makes (``proposito``; ``None``: it never calls a model): the
  provider passed in the context is re-purposed for it and the calls are charged to the user and case;
* whether its result carries third-party data (``datos_de_terceros``): then the text meant for a model is
  wrapped as untrusted data (:func:`envolver_no_confiable`), never as instructions;
* which domains may call it (``ambitos``; ``None`` = as the capability says; the capability's ``None`` = any).

The capability as a whole declares whether it needs the user's consent (``consentimiento="usuario"``: the user
switched it on; the choice is stored with the modules' choices, under the capability's key) or not
(``"ninguno"``: it only processes what the user hands over in that same request, such as an upload).

This file is the stable interface between domains, the core and the capabilities: change it only additively.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from typing import Any, Literal, Protocol, runtime_checkable

Efecto = Literal["lectura", "prepara", "actua"]
Consentimiento = Literal["ninguno", "usuario"]
# Who started the request: the user in the UI or chat, the central agent, a long-running task, a proactive job.
Origen = Literal["usuario", "agente", "tarea", "proactivo"]
MotivoDenegacion = Literal[
    "desconocida",  # no such capability (or it failed validation, or the operator switched it off)
    "operacion",  # no such operation
    "ambito",  # the calling domain may not use it
    "consentimiento",  # the user has not switched the capability on
    "confirmacion",  # an ``actua`` operation without the user's confirmation for exactly this request
    "entrada",  # arguments do not match the input schema
    "salida",  # the operation's result does not match its output schema (a bug of the capability)
]


class PeticionDenegadaError(PermissionError):
    """:func:`pedir` refused the request. ``motivo`` says which rule; the message is Spanish, for the user."""

    def __init__(self, mensaje: str, *, motivo: MotivoDenegacion) -> None:
        super().__init__(mensaje)
        self.motivo: MotivoDenegacion = motivo


# The name the contract was first published with (domains and capabilities may use either).
PeticionDenegada = PeticionDenegadaError


@dataclass(frozen=True)
class Confirmacion:
    """The user's explicit confirmation of ONE ``actua`` request (from a pending action they accepted).

    ``huella`` is :func:`huella_de` of the exact arguments: a confirmation never authorises other arguments,
    another operation or another capability.
    """

    capacidad: str
    operacion: str
    huella: str
    case_id: str | None = None


def huella_de(args: Mapping[str, Any]) -> str:
    """A stable digest of a request's arguments (what a :class:`Confirmacion` authorises)."""
    canonico = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContextoPeticion:
    """Who asks and on whose behalf.

    ``ambito`` is the key of the calling domain (``"caducidades"``) or ``"nucleo"`` for the core itself (the
    central agent, the API). ``proveedor`` is an LLM provider for operations that call a model (re-purposed by
    :func:`pedir` for the operation's ``proposito``). ``confirmacion`` is required by ``actua`` operations.
    """

    ambito: str
    hoy: date
    case_id: str | None = None
    user_id: str | None = None
    proveedor: Any | None = None
    db_path: str | None = None
    confirmacion: Confirmacion | None = None
    origen: Origen = "usuario"
    # The household profile of the case (fact -> value), when the caller has it (a task step, a domain tool).
    perfil: Mapping[str, Any] | None = None
    # The modules/capabilities the caller already knows are on for the case's owner (a task step): consent is
    # then read from it instead of the store. None: :func:`pedir` reads the user's choices.
    activos: frozenset[str] | None = None
    # Stable per logical request (a task's step): whatever an operation creates elsewhere must reuse it, so a
    # retry repeats nothing.
    clave_idempotencia: str | None = None


@dataclass(frozen=True)
class ResultadoCapacidad:
    """What an operation returns.

    The operation fills ``datos`` (matching its output schema), ``resumen`` (one line, for traces), ``citas``
    (sources) and ``avisos`` (Spanish caveats for the user). :func:`pedir` fills the rest:

    * ``texto_para_modelo`` — ``datos`` as text, wrapped as untrusted data when the operation declares
      ``datos_de_terceros`` (the only form in which a domain may hand the result to a model);
    * ``no_confiable`` — the result carries third-party data;
    * ``propuesta`` — the operation's effect is ``prepara``: nothing may be applied without the user's confirmation.

    ``datos`` defaults to empty so a task-facing result (``resumen``, ``estado``, ``aviso``, ``borrador``,
    ``es_error``) can be built alone; the tasks engine reads those fields (``asistente/nucleo/tareas``).
    """

    datos: Mapping[str, Any] = field(default_factory=dict)
    resumen: str = ""
    citas: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()
    texto_para_modelo: str = ""
    no_confiable: bool = False
    propuesta: bool = False
    # For long-running tasks (asistente/nucleo/tareas): the step is done, or waits for the user; a draft the user
    # reviews (never sent); a notice for the user (title, body), created once per step; a failure to retry.
    estado: Literal["hecho", "esperando_usuario"] = "hecho"
    borrador: str | None = None
    aviso: tuple[str, str] | None = None
    es_error: bool = False

    @property
    def evidencias(self) -> tuple[str, ...]:
        """The sources (``citas``), under the name the tasks engine uses."""
        return self.citas


Ejecutor = Callable[[Mapping[str, Any], ContextoPeticion], ResultadoCapacidad]


@dataclass(frozen=True)
class OperacionCapacidad:
    clave: str  # "leer_campos", "descargar_consumo"… unique within the capability
    descripcion: str  # Spanish, what it does (shown in GET /capacidades)
    entrada: Mapping[str, Any]  # JSON Schema, type object
    salida: Mapping[str, Any]  # JSON Schema, type object
    ejecutar: Ejecutor
    efecto: Efecto = "lectura"
    # Metering/routing purpose of the model calls the operation makes (agent/enrutado.py); None: no model calls.
    proposito: str | None = None
    # The result quotes content written by third parties (a mail, a web page, a document): untrusted.
    datos_de_terceros: bool = True
    # Domains allowed to call this operation; None: the capability's ``ambitos``.
    ambitos: frozenset[str] | None = None
    # False: the operation runs without the user's consent to the capability, because it does not use what the
    # consent is about (e.g. it only explains how to do something by hand). None: as the capability says.
    requiere_consentimiento: bool | None = None


@runtime_checkable
class Capacidad(Protocol):
    clave: str  # "correo", "navegador", "documentos", "investigacion"
    nombre: str  # Spanish, for the UI
    descripcion: str  # Spanish
    consentimiento: Consentimiento
    operaciones: Sequence[OperacionCapacidad]
    # Domains allowed to call it (None: any domain and the core).
    ambitos: frozenset[str] | None


@dataclass
class CapacidadBase:
    """Convenience base for a capability."""

    clave: str
    nombre: str
    descripcion: str
    consentimiento: Consentimiento = "usuario"
    operaciones: Sequence[OperacionCapacidad] = field(default_factory=tuple)
    ambitos: frozenset[str] | None = None
    # The capability's legal data files (``conocimiento.FicheroLegal``), like a domain's ``Ganchos.ficheros_legales``:
    # the maintenance loop (``params-verify``, ``params-check``) covers them without being told. Optional; read
    # with ``getattr`` so a capability that is not a ``CapacidadBase`` may declare it too.
    ficheros_legales: tuple[Any, ...] = ()

    def operacion(self, clave: str) -> OperacionCapacidad | None:
        return next((op for op in self.operaciones if op.clave == clave), None)

    def borrar_datos_de_caso(self, case_id: str, db_path: str) -> None:
        """Erase what the capability keeps for a case (consent withdrawn with «borrar datos»): nothing here."""
        return None


# The invocation API lives in ``peticion.py`` (it needs the registry); re-exported here so a domain imports one
# file: ``from asistente.capacidades.contrato import ContextoPeticion, pedir``.
def pedir(
    capacidad: str,
    operacion: str,
    args: Mapping[str, Any],
    contexto: ContextoPeticion,
    **opciones: Any,
) -> ResultadoCapacidad:
    """Ask capability ``capacidad`` to run ``operacion`` with ``args`` (see :func:`peticion.pedir`)."""
    from asistente.capacidades.peticion import pedir as _pedir

    return _pedir(capacidad, operacion, args, contexto, **opciones)


def envolver_no_confiable(origen: str, texto: str) -> str:
    """``texto`` (third-party content) between untrusted-data marks, inert (see :func:`peticion.envolver`)."""
    from asistente.capacidades.peticion import envolver

    return envolver(origen, texto)


__all__ = [
    "Capacidad",
    "CapacidadBase",
    "Confirmacion",
    "Consentimiento",
    "ContextoPeticion",
    "Efecto",
    "Ejecutor",
    "MotivoDenegacion",
    "OperacionCapacidad",
    "Origen",
    "PeticionDenegada",
    "PeticionDenegadaError",
    "ResultadoCapacidad",
    "envolver_no_confiable",
    "huella_de",
    "pedir",
]

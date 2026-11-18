"""Stable adapters between the evaluation and the household brain's packages, resolved LAZILY.

The suite has cases that need code of other packages (``requiere`` in the case): the brain
(``asistente.nucleo.cerebro``), the proactive pass (``asistente.nucleo.repaso``) and the conversation memory
(``asistente.nucleo.recuerdos``). Nothing here imports them at start-up: :func:`pendiente` tries the import when a
case is about to run, and a case whose code is missing is SKIPPED with the reason (never failed, never run half):
that is what lets the suite grow before the code lands, and the same suite run after integration.

What each requirement needs (the only coupling, in one table, :data:`REQUISITOS`):

* ``cerebro`` — ``asistente.nucleo.cerebro.cerebro.Cerebro`` (built with the evaluation's provider, searcher and
  verifier: ``asesor``, ``proveedor``, ``verificador``, ``memoria``, ``fabrica``) and
  ``asistente.nucleo.cerebro.construir.especialistas_del_hogar`` (the household specialists of the case's
  modules). Its ``run(query, chat_history=…, case=…, modulos=…, conversacion_id=…, user_id=…)`` returns an ``AgentResult``
  whose ``cerebro`` is the turn's :class:`~asistente.nucleo.cerebro.contrato.InformeCerebro`. The case's memory
  is :class:`MemoriaDeCaso` (the case's ``memoria`` items, each answered only to its own user).
* ``recuerdos`` — the real conversation memory (``asistente.nucleo.recuerdos.servicio.ServicioMemoria`` over a
  throw-away database), seeded with the case's ``memoria`` items through its store (``almacen.Recuerdo``,
  ``almacen.Titular``): other users' items are stored under THEIR user and case, so isolation is tested for real.
* ``repaso`` — ``asistente.nucleo.repaso.evaluacion.ejecutar(entrada: dict, *, proveedor) -> dict``: the pass on
  the case's ``entrada_repaso`` (``{"hoy", "senales": [{"id", "tipo", "titulo", "detalle", "fecha", "importe"}]}``)
  returning :class:`SalidaRepaso` (``{"texto", "afirmaciones": [{"texto", "senales": [ids]}], "propuestas":
  [{"texto", "senal": id | None}]}``). That function is the repaso package's side of this contract.
* ``consejo`` — the household council's engine, ``asistente.nucleo.consejo.deliberacion.Deliberacion`` (and its
  factory ``construir_motor``): a ``decision``/``sesgo`` case runs the engine on the case's dossier fixture (no
  specialists), through :mod:`asistente.nucleo.evaluacion.consejo_eval`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import importlib
import inspect
from pathlib import Path
import re
import tempfile
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.cerebro.contrato import (
    BloqueMemoria,
    MemoriaNula,
    ProveedorMemoria,
    RecuerdoMemoria,
    TurnoConversacion,
)
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion

REQUISITOS: dict[str, tuple[tuple[str, str], ...]] = {
    "cerebro": (
        ("asistente.nucleo.cerebro.cerebro", "Cerebro"),
        ("asistente.nucleo.cerebro.construir", "especialistas_del_hogar"),
    ),
    "recuerdos": (
        ("asistente.nucleo.recuerdos.servicio", "ServicioMemoria"),
        ("asistente.nucleo.recuerdos.almacen", "Recuerdo"),
        ("asistente.nucleo.recuerdos.almacen", "Titular"),
    ),
    "repaso": (("asistente.nucleo.repaso.evaluacion", "ejecutar"),),
    "consejo": (
        ("asistente.nucleo.consejo.deliberacion", "Deliberacion"),
        ("asistente.nucleo.consejo.deliberacion", "construir_motor"),
    ),
}


def _resolver(modulo: str, atributo: str) -> Any:
    return getattr(importlib.import_module(modulo), atributo)


def pendiente(requisito: str) -> str | None:
    """Why ``requisito`` cannot run here (Spanish), or None when its code is available."""
    for modulo, atributo in REQUISITOS.get(requisito, ()):
        try:
            _resolver(modulo, atributo)
        except Exception as exc:  # missing package or attribute, or one that fails to import
            return f"requiere {requisito}: {modulo}.{atributo} no está disponible ({type(exc).__name__})"
    if requisito not in REQUISITOS:
        return f"requisito desconocido «{requisito}»"
    return None


def pendientes(caso: CasoEvaluacion, *, excepto: Sequence[str] = ()) -> list[str]:
    """The reasons a case cannot run here (empty: it can). ``excepto``: requirements the caller provides itself
    (e.g. a scripted council engine)."""
    return [motivo for r in caso.requiere if r not in excepto and (motivo := pendiente(r)) is not None]


# --------------------------------------------------------------------------- #
# The case's memory
# --------------------------------------------------------------------------- #


class MemoriaDeCaso:
    """The case's ``memoria`` as a ``ProveedorMemoria``: each item answered only to its own user (the case's user
    unless the item says another). Records every retrieval, to check the brain asked for the right user."""

    def __init__(self, caso: CasoEvaluacion) -> None:
        self._caso = caso
        self.consultas: list[dict[str, Any]] = []
        self.turnos: list[TurnoConversacion] = []

    def recuperar(
        self,
        *,
        case_id: str | None,
        user_id: str | None,
        consulta: str,
        conversacion_id: str | None = None,
        limite: int = 8,
    ) -> BloqueMemoria:
        self.consultas.append({"case_id": case_id, "user_id": user_id, "consulta": consulta})
        propios = [
            RecuerdoMemoria(**r.model_dump(exclude={"usuario", "relevancia"}), relevancia=1.0)
            for r in self._caso.memoria
            if (r.usuario or self._caso.usuario) == user_id
        ][:limite]
        return BloqueMemoria(recuerdos=propios, vacio_por=None if propios else "sin_resultados")

    def registrar_turno(self, turno: TurnoConversacion) -> None:
        self.turnos.append(turno)


def caso_de_usuario(caso: CasoEvaluacion, usuario: str) -> str:
    return f"eval_{re.sub(r'[^A-Za-z0-9_-]', '_', caso.id)}_{usuario}"[:64]


def memoria_real(caso: CasoEvaluacion, directorio: Path) -> ProveedorMemoria:
    """The real conversation memory over a throw-away database, seeded with the case's items (each under its own
    user and case)."""
    servicio_cls = _resolver("asistente.nucleo.recuerdos.servicio", "ServicioMemoria")
    recuerdo_cls = _resolver("asistente.nucleo.recuerdos.almacen", "Recuerdo")
    titular_cls = _resolver("asistente.nucleo.recuerdos.almacen", "Titular")
    db = str(directorio / "recuerdos.sqlite3")
    parametros = inspect.signature(servicio_cls).parameters
    servicio = (
        servicio_cls(db, usar_incrustador_configurado=False)
        if "usar_incrustador_configurado" in parametros
        else servicio_cls(db)
    )
    ahora = datetime.now(UTC)
    for r in caso.memoria:
        usuario = r.usuario or caso.usuario
        titular = titular_cls.de(caso_de_usuario(caso, usuario), usuario)
        datos = {
            "id": r.id,
            "tipo": r.tipo,
            "clave": r.id,
            "texto": r.texto,
            "origen": {"conversacion_id": r.origen.conversacion_id, "fecha": r.origen.fecha, "cita": r.origen.cita},
            "creado": ahora,
            "actualizado": ahora,
            "corregido": r.corregido,
        }
        servicio.store.guardar(
            titular, recuerdo_cls(**{k: v for k, v in datos.items() if k in recuerdo_cls.model_fields})
        )
    return servicio  # type: ignore[no-any-return]


def memoria_para(caso: CasoEvaluacion, directorio: Path | None = None) -> ProveedorMemoria:
    if "recuerdos" in caso.requiere:
        return memoria_real(caso, directorio or Path(tempfile.mkdtemp(prefix="evaluacion-recuerdos-")))
    if caso.memoria:
        return MemoriaDeCaso(caso)
    return MemoriaNula()


# --------------------------------------------------------------------------- #
# The brain
# --------------------------------------------------------------------------- #


def conversacion_de(caso: CasoEvaluacion) -> str:
    return caso_de_usuario(caso, "c").ljust(8, "_")[:64]


def construir_cerebro(*, asesor: Any, proveedor: Any, verificador: Any, memoria: ProveedorMemoria) -> Any:
    """The brain with the evaluation's pieces (only the keyword arguments its constructor takes)."""
    cerebro_cls = _resolver("asistente.nucleo.cerebro.cerebro", "Cerebro")
    fabrica = _resolver("asistente.nucleo.cerebro.construir", "especialistas_del_hogar")
    disponibles = {
        "asesor": asesor,
        "proveedor": proveedor,
        "verificador": verificador,
        "memoria": memoria,
        "fabrica": fabrica,
    }
    parametros = inspect.signature(cerebro_cls).parameters
    return cerebro_cls(**{k: v for k, v in disponibles.items() if k in parametros})


def ejecutar_cerebro(
    caso: CasoEvaluacion,
    *,
    asesor: Any,
    proveedor: Any,
    verificador: Any,
    modulos: Sequence[Any],
    case: Any,
    memoria: ProveedorMemoria,
) -> Any:
    """One turn of the brain on the case: the ``AgentResult`` (with ``cerebro``)."""
    cerebro = construir_cerebro(asesor=asesor, proveedor=proveedor, verificador=verificador, memoria=memoria)
    argumentos: dict[str, Any] = {
        "chat_history": list(caso.historial),
        "case": case,
        "modulos": list(modulos),
        "conversacion_id": conversacion_de(caso),
    }
    if "user_id" in inspect.signature(cerebro.run).parameters:
        argumentos["user_id"] = caso.usuario  # whose memory the turn reads
    return cerebro.run(caso.pregunta, **argumentos)


# --------------------------------------------------------------------------- #
# The proactive pass
# --------------------------------------------------------------------------- #


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AfirmacionRepaso(_Modelo):
    texto: str
    senales: list[str] = Field(default_factory=list)


class PropuestaRepaso(_Modelo):
    texto: str
    senal: str | None = None


class SalidaRepaso(_Modelo):
    """What the evaluation reads of a pass: every claim and proposal with the signals it rests on."""

    texto: str = ""
    afirmaciones: list[AfirmacionRepaso] = Field(default_factory=list)
    propuestas: list[PropuestaRepaso] = Field(default_factory=list)


def ejecutar_repaso(caso: CasoEvaluacion, *, proveedor: Any) -> SalidaRepaso:
    if caso.entrada_repaso is None:
        raise ValueError("el caso no tiene «entrada_repaso»")
    funcion = _resolver("asistente.nucleo.repaso.evaluacion", "ejecutar")
    salida = funcion(caso.entrada_repaso.model_dump(mode="json"), proveedor=proveedor)
    return SalidaRepaso.model_validate(salida)


__all__ = [
    "REQUISITOS",
    "AfirmacionRepaso",
    "MemoriaDeCaso",
    "PropuestaRepaso",
    "SalidaRepaso",
    "construir_cerebro",
    "conversacion_de",
    "ejecutar_cerebro",
    "ejecutar_repaso",
    "memoria_para",
    "memoria_real",
    "pendiente",
    "pendientes",
]

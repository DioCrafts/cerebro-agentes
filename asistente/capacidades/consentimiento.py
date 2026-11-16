"""The user's consent per capability, stored with the modules' choices (same store, same rules).

A capability with ``consentimiento="usuario"`` (mail, browser) works for a user only after they switched it
on. The choice is the same append-only record as a module's (``auth.record_module_choice`` under the
capability's key): a capability that also has a chat front as a module (``correo``, ``navegador``) has ONE
switch, whichever screen the user flips it in. Without accounts (``AUTH_MODE=off``) the operator's defaults
(``MODULOS_ACTIVOS_POR_DEFECTO``) are the only switch. ``MODULOS_DESACTIVADOS`` and ``CAPACIDADES_DESACTIVADAS``
switch it off for everyone. A capability with ``consentimiento="ninguno"`` is always on: it only processes what
the user hands over in the same request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from asistente.capacidades.contrato import Capacidad, ContextoPeticion
from asistente.capacidades.registro import RegistroCapacidades, desactivadas_por_operador, registro
from asistente.plataforma import settings


@dataclass(frozen=True)
class EstadoCapacidad:
    capacidad: Capacidad
    activa: bool
    # True when the user decided (False: the default applies, or no consent is needed).
    elegida: bool = False
    cuando: datetime | None = None

    @property
    def requiere_consentimiento(self) -> bool:
        return self.capacidad.consentimiento == "usuario"


def _apagadas() -> frozenset[str]:
    from asistente.nucleo.modulos.registro import desactivados_por_operador

    return desactivadas_por_operador() | desactivados_por_operador()


def _por_defecto(clave: str) -> bool:
    valor = settings.MODULOS_ACTIVOS_POR_DEFECTO.strip().lower()
    if valor in {"todos", "all", "*"}:
        return True
    return clave in {part.strip() for part in valor.split(",") if part.strip()}


def estado_para_usuario(
    user_id: str | None, auth: Any | None, reg: RegistroCapacidades | None = None
) -> list[EstadoCapacidad]:
    """Every capability the operator offers and whether it is on for ``user_id`` (None: no accounts)."""
    reg = reg or registro()
    apagadas = _apagadas()
    elecciones = auth.module_choices(user_id) if (user_id is not None and auth is not None) else {}
    estados: list[EstadoCapacidad] = []
    for cap in reg:
        if cap.clave in apagadas:
            continue
        if cap.consentimiento == "ninguno":
            estados.append(EstadoCapacidad(capacidad=cap, activa=True))
            continue
        eleccion = elecciones.get(cap.clave)
        if eleccion is not None:
            estados.append(
                EstadoCapacidad(capacidad=cap, activa=eleccion.activo, elegida=True, cuando=eleccion.timestamp)
            )
        else:
            estados.append(EstadoCapacidad(capacidad=cap, activa=_por_defecto(cap.clave)))
    return estados


def _dueno(contexto: ContextoPeticion, auth: Any) -> str | None:
    if contexto.case_id is not None:
        owner = auth.owner_of(contexto.case_id)
        if owner is not None:
            return str(owner)
    return contexto.user_id


def consentida(capacidad: Capacidad, contexto: ContextoPeticion, auth: Any | None = None) -> bool:
    """Whether the user behind ``contexto`` (the case's owner, else ``user_id``) has the capability on."""
    if capacidad.clave in _apagadas():
        return False
    if capacidad.consentimiento == "ninguno":
        return True
    if contexto.activos is not None:  # the caller already read the owner's choices (a task step)
        return capacidad.clave in contexto.activos
    if settings.AUTH_MODE != "required":
        return _por_defecto(capacidad.clave)
    if auth is None:
        from asistente.plataforma.auth.deps import get_auth_store

        auth = get_auth_store()
    dueno = _dueno(contexto, auth)
    if dueno is None:
        return _por_defecto(capacidad.clave)
    eleccion = auth.module_choices(dueno).get(capacidad.clave)
    return bool(eleccion.activo) if eleccion is not None else _por_defecto(capacidad.clave)


__all__ = ["EstadoCapacidad", "consentida", "estado_para_usuario"]

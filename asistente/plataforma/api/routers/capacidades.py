"""Capabilities (``asistente/capacidades``): what each one does, its operations and the caller's consent.

* ``GET /capacidades`` — every capability the operator offers: its operations (effect, metering purpose, whether
  their results are third-party data), whether it needs the user's consent and whether the caller gave it, and
  which domains declare that they use it (``Ganchos.capacidades``).
* ``PUT /me/capacidades/{clave}`` — the user gives or withdraws consent to a capability (accounts only). The
  choice is stored with the modules' choices (same append-only record, exported and erased with the account). A
  capability that is also a module (``correo``, ``navegador``) has ONE switch: ``PUT /me/modulos/{clave}``,
  which also removes its notices and pending actions and can erase its data; here it answers 409 pointing there.

The module deliberately has no ``from __future__ import annotations`` (FastAPI resolves the local aliases).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from asistente.capacidades.consentimiento import EstadoCapacidad, estado_para_usuario
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access, require_session


class OperacionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clave: str
    descripcion: str
    efecto: Literal["lectura", "prepara", "actua"]
    # Metering purpose of its model calls (None: it makes none).
    proposito: str | None = None
    datos_de_terceros: bool
    requiere_consentimiento: bool


class CapacidadInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clave: str
    nombre: str
    descripcion: str
    requiere_consentimiento: bool
    activa: bool
    elegida: bool
    elegida_el: datetime | None = None
    # A module with the same key is its chat front: the switch is ``PUT /me/modulos/{clave}``.
    modulo: str | None = None
    # Domains that declare they use it (contract v2).
    usada_por: list[str] = Field(default_factory=list)
    operaciones: list[OperacionInfo] = Field(default_factory=list)


class CapacidadesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configurable: bool
    capacidades: list[CapacidadInfo]


class CapacidadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activa: bool


def _usos() -> dict[str, list[str]]:
    from asistente.nucleo.modulos.ganchos import ganchos_de
    from asistente.nucleo.modulos.registro import registro

    usos: dict[str, list[str]] = {}
    for modulo in registro().activos():
        for pedida in ganchos_de(modulo).capacidades:
            lista = usos.setdefault(pedida.split(".", 1)[0], [])
            if modulo.clave not in lista:
                lista.append(modulo.clave)
    return usos


def _modulos() -> set[str]:
    from asistente.nucleo.modulos.registro import registro

    return set(registro().modulos)


def _info(estado: EstadoCapacidad, usos: dict[str, list[str]], modulos: set[str]) -> CapacidadInfo:
    cap = estado.capacidad
    return CapacidadInfo(
        clave=cap.clave,
        nombre=cap.nombre,
        descripcion=cap.descripcion,
        requiere_consentimiento=estado.requiere_consentimiento,
        activa=estado.activa,
        elegida=estado.elegida,
        elegida_el=estado.cuando,
        modulo=cap.clave if cap.clave in modulos else None,
        usada_por=sorted(usos.get(cap.clave, [])),
        operaciones=[
            OperacionInfo(
                clave=op.clave,
                descripcion=op.descripcion,
                efecto=op.efecto,
                proposito=op.proposito,
                datos_de_terceros=op.datos_de_terceros,
                requiere_consentimiento=(
                    op.requiere_consentimiento
                    if op.requiere_consentimiento is not None
                    else cap.consentimiento == "usuario"
                ),
            )
            for op in cap.operaciones
        ],
    )


def build_router() -> APIRouter:
    router = APIRouter(tags=["capacidades"])
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]

    def _respuesta(access: CaseAccess) -> CapacidadesResponse:
        user_id = access.user.id if access.user is not None else None
        usos, modulos = _usos(), _modulos()
        return CapacidadesResponse(
            configurable=access.user is not None,
            capacidades=[_info(e, usos, modulos) for e in estado_para_usuario(user_id, access.store)],
        )

    @router.get(
        "/capacidades",
        response_model=CapacidadesResponse,
        dependencies=[Depends(require_api_key), Depends(require_session)],
    )
    def get_capacidades(access: access_dep) -> CapacidadesResponse:
        return _respuesta(access)

    @router.put("/me/capacidades/{clave}", response_model=CapacidadesResponse, dependencies=[Depends(require_api_key)])
    def put_capacidad(clave: str, payload: CapacidadUpdate, access: access_dep) -> CapacidadesResponse:
        if access.user is None or access.store is None:
            raise HTTPException(
                status_code=404,
                detail="Sin cuentas de usuario, las capacidades las activa el operador del servicio.",
            )
        estados = {e.capacidad.clave: e for e in estado_para_usuario(access.user.id, access.store)}
        estado = estados.get(clave)
        if estado is None:
            raise HTTPException(status_code=404, detail="Esa capacidad no existe o no está disponible.")
        if clave in _modulos():
            raise HTTPException(
                status_code=409,
                detail=f"«{estado.capacidad.nombre}» se activa y se desactiva en «Módulos».",
            )
        if not estado.requiere_consentimiento:
            raise HTTPException(
                status_code=409, detail="Esta capacidad no necesita tu permiso: solo usa lo que tú le das."
            )
        if estado.activa != payload.activa or not estado.elegida:
            access.store.record_module_choice(access.user.id, clave, payload.activa)
        return _respuesta(access)

    return router


__all__ = ["CapacidadInfo", "CapacidadesResponse", "build_router"]

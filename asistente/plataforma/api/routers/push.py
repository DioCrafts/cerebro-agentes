"""Notices on the user's devices (Web Push): opt-in, devices, a test message.

* ``GET /push/config`` — whether this service sends push notices, its VAPID public key (what the browser needs
  to subscribe), whether the caller opted in and how many devices they have.
* ``POST /push/suscripciones`` — opt in and add this device (``consentimiento`` must be ``true``: the explicit
  opt-in, recorded with the version of the wording the user saw).
* ``POST /push/suscripciones/baja`` — remove this device (the opt-in stays for the others).
* ``DELETE /push/consentimiento`` — withdraw the opt-in: every device of the user is forgotten.
* ``GET /push/suscripciones`` — the user's devices and opt-in history (access right; no keys, no endpoints).
* ``POST /push/prueba`` — send the fixed test message to the caller's devices.

Same access rules as the rest of the API: API key when configured, a session when accounts are on (the owner
is the account; without accounts it is the operator, :data:`~asistente.plataforma.push.almacen.OPERADOR`). The store is
the cases' SQLite file, so the subscriptions are erased with the account (triggers on ``users``).

Built by :func:`build_router` with the case store passed in (see ``api.py``), like the other routers. No
``from __future__ import annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable
from datetime import datetime
import threading
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access
from asistente.plataforma.push.almacen import OPERADOR, VERSION_TEXTO_CONSENTIMIENTO, PushStore, RegistroConsentimiento
from asistente.plataforma.push.cifrado import CifradoError, b64url_decode, cargar_publica
from asistente.plataforma.push.envio import EndpointNoPermitidoError, carga_para, enviar_a_propietario, validar_endpoint
from asistente.plataforma.push.vapid import ClavesVapid, ConfiguracionVapidError, claves_desde_entorno

_NO_DISPONIBLE = "Los avisos en el móvil no están activados en este servicio."
# One test message per owner every this many seconds.
_PAUSA_PRUEBA_SEGUNDOS = 30.0

_stores_lock = threading.Lock()
_stores: dict[str, PushStore] = {}
_ultima_prueba: dict[str, float] = {}


def _store_para(db_path: str) -> PushStore:
    with _stores_lock:
        store = _stores.get(db_path)
        if store is None:
            store = _stores[db_path] = PushStore(db_path)
        return store


class ClavesSuscripcion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=80, max_length=100)
    auth: str = Field(min_length=16, max_length=32)


class SuscripcionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=10, max_length=2048)
    claves: ClavesSuscripcion
    # The explicit opt-in; anything else is a 422.
    consentimiento: Literal[True]


class BajaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=10, max_length=2048)


class PushConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disponible: bool
    clave_publica: str | None
    consentimiento: bool
    dispositivos: int
    version_texto: str


class EstadoPush(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consentimiento: bool
    dispositivos: int


class Dispositivo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    servicio: str
    creada: datetime
    ultimo_envio: datetime | None


class SuscripcionesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispositivos: list[Dispositivo]
    consentimientos: list[RegistroConsentimiento]


class PruebaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enviadas: int
    errores: int
    caducadas: int


def _propietario(access: CaseAccess) -> str:
    return access.user.id if access.user is not None else OPERADOR


def _claves() -> ClavesVapid | None:
    try:
        return claves_desde_entorno()
    except ConfiguracionVapidError:
        # The operator sees the reason in ``ops deliver``; the user only that it is not available.
        return None


def _claves_o_503() -> ClavesVapid:
    claves = _claves()
    if claves is None:
        raise HTTPException(status_code=503, detail=_NO_DISPONIBLE)
    return claves


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(prefix="/push", tags=["push"], dependencies=[Depends(require_api_key)])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]

    def _estado(store: PushStore, propietario: str) -> EstadoPush:
        return EstadoPush(
            consentimiento=store.tiene_consentimiento(propietario), dispositivos=len(store.de(propietario))
        )

    @router.get("/config", response_model=PushConfig)
    def get_config(cases: case_store_dep, access: access_dep) -> PushConfig:
        claves = _claves()
        estado = _estado(_store_para(cases.db_path), _propietario(access))
        return PushConfig(
            disponible=claves is not None,
            clave_publica=claves.publica_b64 if claves is not None else None,
            consentimiento=estado.consentimiento,
            dispositivos=estado.dispositivos,
            version_texto=VERSION_TEXTO_CONSENTIMIENTO,
        )

    @router.post("/suscripciones", response_model=EstadoPush, status_code=201)
    def post_suscripcion(payload: SuscripcionRequest, cases: case_store_dep, access: access_dep) -> EstadoPush:
        _claves_o_503()
        try:
            validar_endpoint(payload.endpoint)
        except EndpointNoPermitidoError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            cargar_publica(b64url_decode(payload.claves.p256dh))
            if len(b64url_decode(payload.claves.auth)) != 16:
                raise CifradoError("auth")
        except CifradoError as exc:
            raise HTTPException(status_code=422, detail="Las claves de la suscripción no son válidas.") from exc
        store = _store_para(cases.db_path)
        propietario = _propietario(access)
        store.registrar_consentimiento(propietario, True)
        store.suscribir(propietario, payload.endpoint, payload.claves.p256dh, payload.claves.auth)
        return _estado(store, propietario)

    @router.post("/suscripciones/baja", response_model=EstadoPush)
    def post_baja(payload: BajaRequest, cases: case_store_dep, access: access_dep) -> EstadoPush:
        store = _store_para(cases.db_path)
        propietario = _propietario(access)
        store.baja(propietario, payload.endpoint)
        return _estado(store, propietario)

    @router.delete("/consentimiento", response_model=EstadoPush)
    def delete_consentimiento(cases: case_store_dep, access: access_dep) -> EstadoPush:
        store = _store_para(cases.db_path)
        propietario = _propietario(access)
        store.borrar_de(propietario)
        if store.consentimiento(propietario) is not None:
            store.registrar_consentimiento(propietario, False)
        return _estado(store, propietario)

    @router.get("/suscripciones", response_model=SuscripcionesResponse)
    def get_suscripciones(cases: case_store_dep, access: access_dep) -> SuscripcionesResponse:
        store = _store_para(cases.db_path)
        propietario = _propietario(access)
        return SuscripcionesResponse(
            dispositivos=[
                Dispositivo(id=s.id, servicio=s.servicio, creada=s.creada, ultimo_envio=s.ultimo_envio)
                for s in store.de(propietario)
            ],
            consentimientos=store.historial_consentimiento(propietario),
        )

    @router.post("/prueba", response_model=PruebaResponse)
    def post_prueba(cases: case_store_dep, access: access_dep) -> PruebaResponse:
        claves = _claves_o_503()
        store = _store_para(cases.db_path)
        propietario = _propietario(access)
        if not store.tiene_consentimiento(propietario):
            raise HTTPException(status_code=409, detail="Activa primero los avisos en este dispositivo.")
        ahora = time.monotonic()
        with _stores_lock:
            anterior = _ultima_prueba.get(propietario)
            if anterior is not None and ahora - anterior < _PAUSA_PRUEBA_SEGUNDOS:
                raise HTTPException(status_code=429, detail="Espera unos segundos antes de enviar otra prueba.")
            _ultima_prueba[propietario] = ahora
        resultado = enviar_a_propietario(store, propietario, carga_para(None, tipo="prueba"), claves=claves)
        return PruebaResponse(enviadas=resultado.enviadas, errores=resultado.errores, caducadas=resultado.caducadas)

    return router


__all__ = ["build_router"]

"""Talking to the household brain from a messaging app (SimpleX): pairing a chat and the paired devices.

* ``GET /mensajeria/estado`` — whether the gateway is on, each channel's state (connected, the bot's address to
  scan or open), how many chats the caller has paired and whether a pairing code of theirs is live.
* ``POST /mensajeria/emparejar`` — a one-time pairing code for the caller's case (shown ONCE: only its hash is
  stored), valid ``MENSAJERIA_CODIGO_MINUTOS``, with the bot's address. The caller sends the code from the chat.
* ``GET /mensajeria/dispositivos`` — the caller's paired chats (no message text: there is none stored).
* ``PUT /mensajeria/dispositivos/{id}/pausa`` — pause or resume the notices to that chat.
* ``DELETE /mensajeria/dispositivos/{id}`` — unpair it: the binding goes now; the worker says goodbye in the chat
  and removes the contact on the channel (the channel runs in the worker, not here).

Same access rules as the rest of the API (API key when configured; a session when accounts are on — the owner is
the account; without accounts, the operator). Another user's case or device reads exactly like one that does not
exist (404). Built by :func:`build_router` with the case store passed in (see ``api.py``). No
``from __future__ import annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma import settings
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access
from asistente.plataforma.mensajeria import emparejamiento
from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria, Vinculo, almacen
from asistente.plataforma.mensajeria.contrato import EstadoCanal

_NO_ACTIVA = "La mensajería no está activada en este servicio."
_NO_EXISTE = "No existe ese dispositivo."
# A channel state older than this means the worker (or the channel) is not running.
_ESTADO_VIGENTE = timedelta(seconds=120)
NOMBRES_CANAL = {"simplex": "SimpleX Chat", "signal": "Signal", "telegram": "Telegram", "prueba": "Canal de prueba"}


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmparejarRequest(_Modelo):
    case_id: str = Field(min_length=1, max_length=200)
    canal: str | None = Field(default=None, max_length=20)


class CodigoEmparejamiento(_Modelo):
    codigo: str  # shown once
    caduca: datetime
    segundos: int
    canal: str
    nombre_canal: str
    direccion: str | None
    conectado: bool
    instrucciones: str


class CanalVista(_Modelo):
    canal: str
    nombre: str
    conectado: bool
    detalle: str
    direccion: str | None
    actualizado: datetime | None


class EstadoMensajeria(_Modelo):
    activa: bool
    canales: list[CanalVista]
    dispositivos: int
    max_dispositivos: int
    codigo_caduca: datetime | None


class Dispositivo(_Modelo):
    id: str
    canal: str
    nombre_canal: str
    nombre_mostrado: str | None
    case_id: str
    creado: datetime
    ultimo_uso: datetime | None
    avisos_en_pausa: bool


class DispositivosResponse(_Modelo):
    dispositivos: list[Dispositivo]


class PausaRequest(_Modelo):
    pausado: bool


def _activa() -> bool:
    return bool(getattr(settings, "MENSAJERIA_ACTIVA", False))


def _canales() -> list[str]:
    from asistente.plataforma.mensajeria.servicio import canales_configurados

    return canales_configurados()


def _max_dispositivos() -> int:
    return max(1, int(getattr(settings, "MENSAJERIA_CONTACTOS_POR_USUARIO", 1)))


def _propietario(access: CaseAccess) -> str | None:
    return access.user.id if access.user is not None else None


def _dispositivo(v: Vinculo) -> Dispositivo:
    return Dispositivo(
        id=v.id,
        canal=v.canal,
        nombre_canal=NOMBRES_CANAL.get(v.canal, v.canal),
        nombre_mostrado=v.nombre_mostrado,
        case_id=v.case_id,
        creado=v.creado,
        ultimo_uso=v.ultimo_uso,
        avisos_en_pausa=v.pausado,
    )


def _vistas(store: AlmacenMensajeria, nombres: list[str], ahora: datetime) -> list[CanalVista]:
    publicados: dict[str, tuple[EstadoCanal, datetime]] = {
        str(estado.canal): (estado, momento) for estado, momento in store.estados()
    }
    vistas: list[CanalVista] = []
    for nombre in nombres:
        publicado = publicados.get(nombre)
        if publicado is None:
            vistas.append(
                CanalVista(
                    canal=nombre,
                    nombre=NOMBRES_CANAL.get(nombre, nombre),
                    conectado=False,
                    detalle="El servicio de mensajería todavía no se ha puesto en marcha.",
                    direccion=None,
                    actualizado=None,
                )
            )
            continue
        estado, momento = publicado
        vigente = ahora - momento <= _ESTADO_VIGENTE
        vistas.append(
            CanalVista(
                canal=nombre,
                nombre=NOMBRES_CANAL.get(nombre, nombre),
                conectado=estado.conectado and vigente,
                detalle=estado.detalle if vigente else "El servicio de mensajería no responde ahora mismo.",
                # The bot's address is stable: shown even while the worker is down (pairing waits for it).
                direccion=estado.direccion,
                actualizado=momento,
            )
        )
    return vistas


def _auditar(cases: SQLiteCaseStateStore, case_id: str, accion: str, **datos: object) -> None:
    try:
        cases.append_audit_event(case_id, {"event_type": "mensajeria", "accion": accion, **datos})
    except Exception:  # the audit trail never blocks the user's privacy controls
        pass


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(prefix="/mensajeria", tags=["mensajeria"], dependencies=[Depends(require_api_key)])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]

    def _vinculo_propio(store: AlmacenMensajeria, dispositivo_id: str, access: CaseAccess) -> Vinculo:
        vinculo = store.vinculo(dispositivo_id, user_id=_propietario(access))
        if vinculo is None:
            raise HTTPException(status_code=404, detail=_NO_EXISTE)
        return vinculo

    @router.get("/estado", response_model=EstadoMensajeria)
    def get_estado(cases: case_store_dep, access: access_dep) -> EstadoMensajeria:
        store = almacen(cases.db_path)
        ahora = datetime.now(UTC)
        nombres = _canales()
        propietario = _propietario(access)
        caducas = [c for c in (store.codigo_vigente(propietario, n, ahora) for n in nombres) if c is not None]
        return EstadoMensajeria(
            activa=_activa(),
            canales=_vistas(store, nombres, ahora),
            dispositivos=len(store.vinculos_de_usuario(propietario)),
            max_dispositivos=_max_dispositivos(),
            codigo_caduca=max(caducas) if caducas else None,
        )

    @router.post("/emparejar", response_model=CodigoEmparejamiento, status_code=201)
    def post_emparejar(payload: EmparejarRequest, cases: case_store_dep, access: access_dep) -> CodigoEmparejamiento:
        access.check(payload.case_id)
        if cases.get_case(payload.case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        if not _activa():
            raise HTTPException(status_code=503, detail=_NO_ACTIVA)
        nombres = _canales()
        canal = payload.canal or (nombres[0] if nombres else "")
        if canal not in nombres:
            raise HTTPException(status_code=422, detail="Ese canal de mensajería no está disponible en este servicio.")
        store = almacen(cases.db_path)
        propietario = _propietario(access)
        ahora = datetime.now(UTC)
        if sum(1 for v in store.vinculos_de_usuario(propietario) if v.canal == canal) >= _max_dispositivos():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ya tienes el máximo de chats vinculados en {NOMBRES_CANAL.get(canal, canal)}. Desvincula uno "
                    "para emparejar otro."
                ),
            )
        por_hora = max(1, int(getattr(settings, "MENSAJERIA_CODIGOS_POR_HORA", 6)))
        if store.codigos_desde(propietario, ahora - timedelta(hours=1)) >= por_hora:
            raise HTTPException(
                status_code=429,
                detail="Has generado muchos códigos en poco tiempo. Espera un rato antes de pedir otro.",
                headers={"Retry-After": "600"},
            )
        nuevo = emparejamiento.nuevo_codigo(
            store, canal=canal, user_id=propietario, case_id=payload.case_id, momento=ahora
        )
        vista = _vistas(store, [canal], ahora)[0]
        _auditar(cases, payload.case_id, "codigo_generado", canal=canal)
        minutos = emparejamiento.minutos_codigo()
        return CodigoEmparejamiento(
            codigo=nuevo.codigo,
            caduca=nuevo.caduca,
            segundos=int((nuevo.caduca - ahora).total_seconds()),
            canal=canal,
            nombre_canal=vista.nombre,
            direccion=vista.direccion,
            conectado=vista.conectado,
            instrucciones=(
                f"Abre {vista.nombre}, conecta con el asistente (escanea el código QR o abre el enlace) y envíale este "
                f"código en un mensaje. Caduca en {minutos} minutos y solo sirve una vez."
            ),
        )

    @router.get("/dispositivos", response_model=DispositivosResponse)
    def get_dispositivos(cases: case_store_dep, access: access_dep) -> DispositivosResponse:
        store = almacen(cases.db_path)
        return DispositivosResponse(
            dispositivos=[_dispositivo(v) for v in store.vinculos_de_usuario(_propietario(access))]
        )

    @router.put("/dispositivos/{dispositivo_id}/pausa", response_model=Dispositivo)
    def put_pausa(dispositivo_id: str, payload: PausaRequest, cases: case_store_dep, access: access_dep) -> Dispositivo:
        store = almacen(cases.db_path)
        vinculo = _vinculo_propio(store, dispositivo_id, access)
        store.pausar(vinculo.id, payload.pausado)
        actualizado = store.vinculo(vinculo.id, user_id=_propietario(access))
        if actualizado is None:  # unpaired meanwhile
            raise HTTPException(status_code=404, detail=_NO_EXISTE)
        return _dispositivo(actualizado)

    @router.delete("/dispositivos/{dispositivo_id}", status_code=204)
    def delete_dispositivo(dispositivo_id: str, cases: case_store_dep, access: access_dep) -> Response:
        store = almacen(cases.db_path)
        vinculo = _vinculo_propio(store, dispositivo_id, access)
        if store.desvincular(vinculo.id, motivo="app", encolar=True, momento=datetime.now(UTC)) is not None:
            _auditar(cases, vinculo.case_id, "desemparejado", canal=vinculo.canal, vinculo_id=vinculo.id, motivo="app")
        return Response(status_code=204)

    return router


__all__ = ["build_router"]

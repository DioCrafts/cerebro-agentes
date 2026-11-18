"""«Lo que sé de ti» and «Conversaciones anteriores»: the conversation memory, visible, correctable, erasable.

* ``GET /recuerdos?case_id=`` — every remembered item with its origin (conversation, date, the user's words),
  the earlier versions it replaced, the on/off switch and how long raw messages are kept.
* ``PATCH /recuerdos/{id}?case_id=`` — the user rewrites an item (their wording wins: ``corregido``).
* ``DELETE /recuerdos/{id}?case_id=`` — forget one (with its earlier versions; it is not learnt again from the
  same messages).
* ``DELETE /recuerdos?case_id=`` — «Olvidar todo»: every item, conversation, summary and message.
* ``GET|PUT /recuerdos/interruptor?case_id=`` — pause / resume the memory (pausing deletes nothing).
* ``GET /conversaciones?case_id=`` — the conversations, with titles and summaries.
* ``GET /conversaciones/{id}?case_id=`` — one, with its messages while they are retained and what was learnt.
* ``DELETE /conversaciones/{id}?case_id=`` — its messages, its summary and the items that rested only on it.

Whose memory: with ``case_id`` the case must be the caller's (somebody else's case, item or conversation is a
404, exactly like one that does not exist) and the owner is (case, the account — or the case's ``user_id`` when
accounts are off); without it, the signed-in user's memory outside any case. Same guards as every other router
(API key, session). Listing and erasing work even with ``MEMORIA_CONVERSACIONES`` off: what was stored can always
be seen and deleted.

Built by :func:`build_router` with the case store passed in (see ``api.py``). No ``from __future__ import
annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.recuerdos.almacen import Conversacion, Fuente, Recuerdo, Titular
from asistente.nucleo.recuerdos.servicio import CorreccionInvalidaError, ServicioMemoria, servicio
from asistente.plataforma import settings
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FuenteVista(_Modelo):
    conversacion_id: str
    fecha: date
    cita: str
    # The conversation's title while it exists (None: it was deleted or purged).
    titulo_conversacion: str | None = None


class RecuerdoVista(_Modelo):
    id: str
    tipo: Literal["dato", "preferencia", "contexto"]
    texto: str
    origen: FuenteVista
    fuentes: list[FuenteVista]
    corregido: bool
    sensible: bool
    estado: Literal["activo", "sustituido"]
    sustituido_por: str | None = None
    creado: datetime
    actualizado: datetime
    usos: int
    ultimo_uso: datetime | None = None


class RecuerdosResponse(_Modelo):
    # The operator has the memory on (MEMORIA_CONVERSACIONES); the user's own switch is ``activa``.
    disponible: bool
    activa: bool
    # Neither a case nor an account: there is nobody to remember anything about.
    sin_cuenta: bool
    retencion_dias: int
    explicacion: str
    recuerdos: list[RecuerdoVista]
    historial: list[RecuerdoVista]
    conversaciones: int


class Correccion(_Modelo):
    texto: str = Field(min_length=1, max_length=600)
    tipo: Literal["dato", "preferencia", "contexto"] | None = None


class OlvidoTotal(_Modelo):
    recuerdos: int
    conversaciones: int
    mensajes: int


class Interruptor(_Modelo):
    disponible: bool
    activa: bool
    explicacion: str


class CambioInterruptor(_Modelo):
    activa: bool


class ConversacionVista(_Modelo):
    id: str
    titulo: str
    creada: datetime
    actualizada: datetime
    estado: str
    resumen: str | None = None
    cita: str | None = None
    mensajes: int
    retenidos: int
    recuerdos: int


class ConversacionesResponse(_Modelo):
    retencion_dias: int
    conversaciones: list[ConversacionVista]


class MensajeVista(_Modelo):
    n: int
    rol: Literal["user", "assistant"]
    texto: str
    fecha: datetime


class ConversacionDetalle(_Modelo):
    conversacion: ConversacionVista
    mensajes: list[MensajeVista]
    recuerdos: list[RecuerdoVista]
    retencion_dias: int


def _retencion() -> int:
    return max(1, int(getattr(settings, "MEMORIA_RETENCION_MENSAJES_DIAS", 30)))


def _disponible() -> bool:
    return bool(getattr(settings, "MEMORIA_CONVERSACIONES", True))


def explicacion() -> str:
    """What the switch does, in plain Spanish (the panel shows it next to it)."""
    if not _disponible():
        return (
            "La memoria de conversaciones está desactivada en este servicio: no guardo ni uso nada nuevo. Lo que "
            "se guardó antes sigue aquí para que lo veas, lo corrijas o lo borres."
        )
    return (
        "Con la memoria activada guardo nuestras conversaciones, un resumen de cada una y lo que me cuentas de ti "
        "(siempre con tus palabras y la fecha en que me lo dijiste), para no preguntarte dos veces. Los mensajes "
        f"tal cual se borran a los {_retencion()} días; los resúmenes y lo que sé de ti se quedan hasta que los "
        "borres. Nunca guardo contraseñas ni números completos de cuenta, tarjeta o documento, y no recuerdo "
        "datos de salud, religión, ideología u otros especialmente protegidos salvo que me pidas expresamente "
        "que lo haga. Si la pausas, no guardo ni uso nada nuevo; lo que ya está sigue aquí para que lo veas o lo "
        "borres. Todo se incluye en la exportación de tu cuenta y se borra con ella."
    )


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["recuerdos"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key)]

    def _titular(case_id: str | None, access: CaseAccess, cases: SQLiteCaseStateStore) -> Titular:
        if case_id:
            access.check(case_id)
            case = cases.get_case(case_id)
            if case is None:
                raise HTTPException(status_code=404, detail="case not found")
            return Titular.de(case_id, access.user.id if access.user is not None else case.user_id)
        return Titular.de(None, access.user.id if access.user is not None else None)

    def _con_dueno(titular: Titular) -> Titular:
        if titular.vacio:
            raise HTTPException(status_code=404, detail="Sin cuenta ni expediente no hay memoria.")
        return titular

    def _memoria(cases: SQLiteCaseStateStore) -> ServicioMemoria:
        return servicio(cases.db_path)

    def _fuente(f: Fuente, titulos: dict[str, str]) -> FuenteVista:
        return FuenteVista(
            conversacion_id=f.conversacion_id,
            fecha=f.fecha,
            cita=f.cita,
            titulo_conversacion=titulos.get(f.conversacion_id),
        )

    def _vista(r: Recuerdo, titulos: dict[str, str]) -> RecuerdoVista:
        return RecuerdoVista(
            id=r.id,
            tipo=r.tipo,
            texto=r.texto,
            origen=_fuente(r.origen, titulos),
            fuentes=[_fuente(f, titulos) for f in r.fuentes],
            corregido=r.corregido,
            sensible=r.sensible,
            estado="sustituido" if r.estado == "sustituido" else "activo",
            sustituido_por=r.sustituido_por,
            creado=r.creado,
            actualizado=r.actualizado,
            usos=r.usos,
            ultimo_uso=r.ultimo_uso,
        )

    def _titulos(memoria: ServicioMemoria, titular: Titular) -> dict[str, str]:
        return {c.id: c.titulo for c in memoria.store.conversaciones(titular)}

    def _conversacion(c: Conversacion, retenidos: int, recuerdos: int) -> ConversacionVista:
        return ConversacionVista(
            id=c.id,
            titulo=c.titulo,
            creada=c.creada,
            actualizada=c.actualizada,
            estado=c.estado,
            resumen=c.resumen,
            cita=c.cita,
            mensajes=c.mensajes,
            retenidos=retenidos,
            recuerdos=recuerdos,
        )

    @router.get("/recuerdos", response_model=RecuerdosResponse, dependencies=guarded)
    def get_recuerdos(cases: case_store_dep, access: access_dep, case_id: str | None = None) -> RecuerdosResponse:
        titular = _titular(case_id, access, cases)
        if titular.vacio:
            return RecuerdosResponse(
                disponible=_disponible(),
                activa=False,
                sin_cuenta=True,
                retencion_dias=_retencion(),
                explicacion=explicacion(),
                recuerdos=[],
                historial=[],
                conversaciones=0,
            )
        memoria = _memoria(cases)
        activos, historia = memoria.listar(titular)
        titulos = _titulos(memoria, titular)
        return RecuerdosResponse(
            disponible=_disponible(),
            activa=memoria.activa(titular),
            sin_cuenta=False,
            retencion_dias=_retencion(),
            explicacion=explicacion(),
            recuerdos=[_vista(r, titulos) for r in activos],
            historial=[_vista(r, titulos) for r in historia],
            conversaciones=len(titulos),
        )

    # Declared before /recuerdos/{recuerdo_id} so «interruptor» is never read as an id.
    @router.get("/recuerdos/interruptor", response_model=Interruptor, dependencies=guarded)
    def get_interruptor(cases: case_store_dep, access: access_dep, case_id: str | None = None) -> Interruptor:
        titular = _titular(case_id, access, cases)
        activa = False if titular.vacio else _memoria(cases).activa(titular)
        return Interruptor(disponible=_disponible(), activa=activa, explicacion=explicacion())

    @router.put("/recuerdos/interruptor", response_model=Interruptor, dependencies=guarded)
    def put_interruptor(
        payload: CambioInterruptor, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> Interruptor:
        titular = _con_dueno(_titular(case_id, access, cases))
        memoria = _memoria(cases)
        memoria.fijar_activa(titular, payload.activa)
        return Interruptor(disponible=_disponible(), activa=memoria.activa(titular), explicacion=explicacion())

    @router.patch("/recuerdos/{recuerdo_id}", response_model=RecuerdoVista, dependencies=guarded)
    def patch_recuerdo(
        recuerdo_id: str,
        payload: Correccion,
        cases: case_store_dep,
        access: access_dep,
        case_id: str | None = None,
    ) -> RecuerdoVista:
        titular = _con_dueno(_titular(case_id, access, cases))
        memoria = _memoria(cases)
        try:
            corregido = memoria.corregir(titular, recuerdo_id, texto=payload.texto, tipo=payload.tipo)
        except CorreccionInvalidaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if corregido is None:
            raise HTTPException(status_code=404, detail="recuerdo no encontrado")
        return _vista(corregido, _titulos(memoria, titular))

    @router.delete("/recuerdos/{recuerdo_id}", status_code=204, dependencies=guarded)
    def delete_recuerdo(
        recuerdo_id: str, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> Response:
        titular = _con_dueno(_titular(case_id, access, cases))
        if not _memoria(cases).olvidar(titular, recuerdo_id):
            raise HTTPException(status_code=404, detail="recuerdo no encontrado")
        return Response(status_code=204)

    @router.delete("/recuerdos", response_model=OlvidoTotal, dependencies=guarded)
    def delete_recuerdos(cases: case_store_dep, access: access_dep, case_id: str | None = None) -> OlvidoTotal:
        titular = _con_dueno(_titular(case_id, access, cases))
        return OlvidoTotal(**_memoria(cases).olvidar_todo(titular))

    @router.get("/conversaciones", response_model=ConversacionesResponse, dependencies=guarded)
    def get_conversaciones(
        cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> ConversacionesResponse:
        titular = _titular(case_id, access, cases)
        if titular.vacio:
            return ConversacionesResponse(retencion_dias=_retencion(), conversaciones=[])
        memoria = _memoria(cases)
        activos, historia = memoria.listar(titular)
        por_conversacion: dict[str, int] = {}
        for r in [*activos, *historia]:
            for conversacion_id in {f.conversacion_id for f in r.fuentes}:
                por_conversacion[conversacion_id] = por_conversacion.get(conversacion_id, 0) + 1
        return ConversacionesResponse(
            retencion_dias=_retencion(),
            conversaciones=[
                _conversacion(c, retenidos, por_conversacion.get(c.id, 0))
                for c, retenidos in memoria.conversaciones(titular)
            ],
        )

    @router.get("/conversaciones/{conversacion_id}", response_model=ConversacionDetalle, dependencies=guarded)
    def get_conversacion(
        conversacion_id: str, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> ConversacionDetalle:
        titular = _con_dueno(_titular(case_id, access, cases))
        memoria = _memoria(cases)
        encontrada = memoria.conversacion(titular, conversacion_id)
        if encontrada is None:
            raise HTTPException(status_code=404, detail="conversación no encontrada")
        conversacion, mensajes, recuerdos = encontrada
        titulos = {conversacion.id: conversacion.titulo, **_titulos(memoria, titular)}
        return ConversacionDetalle(
            conversacion=_conversacion(conversacion, len(mensajes), len(recuerdos)),
            mensajes=[MensajeVista(n=m.n, rol=m.rol, texto=m.texto, fecha=m.fecha) for m in mensajes],
            recuerdos=[_vista(r, titulos) for r in recuerdos],
            retencion_dias=_retencion(),
        )

    @router.delete("/conversaciones/{conversacion_id}", status_code=204, dependencies=guarded)
    def delete_conversacion(
        conversacion_id: str, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> Response:
        titular = _con_dueno(_titular(case_id, access, cases))
        if not _memoria(cases).borrar_conversacion(titular, conversacion_id):
            raise HTTPException(status_code=404, detail="conversación no encontrada")
        return Response(status_code=204)

    return router

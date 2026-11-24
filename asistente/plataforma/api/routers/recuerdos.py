"""«Lo que sé de ti» and «Conversaciones anteriores»: the conversation memory (Hindsight), visible, correctable,
erasable.

* ``GET /recuerdos?case_id=`` — the facts Hindsight remembers about the owner, each with its conversation and date;
  the user's switch and how many conversations there are.
* ``PATCH /recuerdos/{id}?case_id=`` — the user corrects a fact: the correction is retained as their own words and
  the old fact is invalidated. Returns what was learnt from the correction.
* ``DELETE /recuerdos/{id}?case_id=`` — forget one: the message it came from goes, with everything learnt from it.
* ``DELETE /recuerdos?case_id=`` — «Olvidar todo»: the owner's whole Hindsight bank.
* ``GET|PUT /recuerdos/interruptor?case_id=`` — pause / resume the memory (pausing deletes nothing).
* ``GET /conversaciones?case_id=`` — the conversations with retained messages.
* ``GET /conversaciones/{id}?case_id=`` — one, with the user's retained messages and what was learnt from them.
* ``DELETE /conversaciones/{id}?case_id=`` — its messages and everything learnt from them.

Whose memory: with ``case_id`` the case must be the caller's (somebody else's case, item or conversation is a 404,
exactly like one that does not exist) and the owner is (case, the account — or the case's ``user_id`` when accounts
are off); without it, the signed-in user's memory outside any case. Listing and erasing work even with
``MEMORIA_CONVERSACIONES`` off: what was stored can always be seen and deleted. When Hindsight cannot be reached the
answer is a 503 that says so.

Built by :func:`build_router` with the case store passed in (see ``api.py``). No ``from __future__ import
annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.recuerdos.cliente import HindsightError
from asistente.nucleo.recuerdos.servicio import (
    Conversacion,
    CorreccionInvalidaError,
    Recuerdo,
    ServicioMemoria,
    Titular,
    servicio,
)
from asistente.plataforma import settings
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecuerdoVista(_Modelo):
    id: str
    # «hecho»: what the person said about their world; «vivencia»: something that happened to them.
    tipo: Literal["hecho", "vivencia"]
    texto: str
    conversacion_id: str | None = None
    # The conversation's title (the start of its first message) while it exists.
    titulo_conversacion: str | None = None
    fecha: datetime | None = None
    corregido: bool


class RecuerdosResponse(_Modelo):
    # The operator has the memory on (MEMORIA_CONVERSACIONES); the user's own switch is ``activa``.
    disponible: bool
    activa: bool
    # Neither a case nor an account: there is nobody to remember anything about.
    sin_cuenta: bool
    explicacion: str
    recuerdos: list[RecuerdoVista]
    conversaciones: int


class Correccion(_Modelo):
    texto: str = Field(min_length=1, max_length=600)


class CorreccionHecha(_Modelo):
    recuerdos: list[RecuerdoVista]


class OlvidoTotal(_Modelo):
    recuerdos: int
    conversaciones: int


class Interruptor(_Modelo):
    disponible: bool
    activa: bool
    explicacion: str


class CambioInterruptor(_Modelo):
    activa: bool


class ConversacionVista(_Modelo):
    id: str
    titulo: str
    creada: datetime | None = None
    actualizada: datetime | None = None
    mensajes: int


class ConversacionesResponse(_Modelo):
    conversaciones: list[ConversacionVista]


class MensajeVista(_Modelo):
    texto: str
    fecha: datetime | None = None


class ConversacionDetalle(_Modelo):
    conversacion: ConversacionVista
    mensajes: list[MensajeVista]
    recuerdos: list[RecuerdoVista]


def _disponible() -> bool:
    return bool(getattr(settings, "MEMORIA_CONVERSACIONES", True))


def explicacion() -> str:
    """What the memory does, in plain Spanish (the panel shows it next to the switch)."""
    if not _disponible():
        return (
            "La memoria de conversaciones está desactivada en este servicio: no guardo ni uso nada nuevo. Lo que "
            "se guardó antes sigue aquí para que lo veas, lo corrijas o lo borres."
        )
    return (
        "Con la memoria activada guardo lo que tú me escribes (nunca mis respuestas) y saco de ahí lo que sé de ti, "
        "con la conversación y la fecha en que me lo dijiste, para no preguntarte dos veces. Si un dato cambia, me "
        "quedo con el nuevo. Nunca guardo contraseñas ni números completos de cuenta, tarjeta o documento, y no "
        "recuerdo datos de salud, religión, ideología u otros especialmente protegidos salvo que me pidas "
        "expresamente que lo haga. Lo que escribes se guarda en este ordenador, pero para sacar los datos se "
        "envía al mismo proveedor de inteligencia artificial que responde tus preguntas. Si la pausas, no guardo "
        "ni uso nada nuevo; lo que ya está sigue aquí para que lo veas o lo borres. Todo se incluye en la "
        "exportación de tu cuenta y se borra con ella."
    )


@contextmanager
def _hindsight() -> Generator[None]:
    """Hindsight unreachable → 503 with a message the panel can show."""
    try:
        yield
    except HindsightError as exc:
        raise HTTPException(
            status_code=503,
            detail="La memoria no responde ahora mismo (está arrancando o se ha parado). Prueba en un minuto.",
        ) from exc


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

    def _vista(r: Recuerdo, titulos: dict[str, str]) -> RecuerdoVista:
        return RecuerdoVista(
            id=r.id,
            tipo="vivencia" if r.tipo == "vivencia" else "hecho",
            texto=r.texto,
            conversacion_id=r.conversacion_id,
            titulo_conversacion=titulos.get(r.conversacion_id or ""),
            fecha=r.fecha,
            corregido=r.corregido,
        )

    def _conversacion(c: Conversacion, titulo: str) -> ConversacionVista:
        return ConversacionVista(
            id=c.id, titulo=titulo, creada=c.creada, actualizada=c.actualizada, mensajes=len(c.mensajes)
        )

    def _titulos(memoria: ServicioMemoria, titular: Titular) -> dict[str, str]:
        return memoria.titulos(titular, memoria.conversaciones(titular))

    @router.get("/recuerdos", response_model=RecuerdosResponse, dependencies=guarded)
    def get_recuerdos(cases: case_store_dep, access: access_dep, case_id: str | None = None) -> RecuerdosResponse:
        titular = _titular(case_id, access, cases)
        if titular.vacio:
            return RecuerdosResponse(
                disponible=_disponible(),
                activa=False,
                sin_cuenta=True,
                explicacion=explicacion(),
                recuerdos=[],
                conversaciones=0,
            )
        memoria = _memoria(cases)
        with _hindsight():
            recuerdos = memoria.listar(titular)
            titulos = _titulos(memoria, titular)
        return RecuerdosResponse(
            disponible=_disponible(),
            activa=memoria.activa(titular),
            sin_cuenta=False,
            explicacion=explicacion(),
            recuerdos=[_vista(r, titulos) for r in recuerdos],
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

    @router.patch("/recuerdos/{recuerdo_id}", response_model=CorreccionHecha, dependencies=guarded)
    def patch_recuerdo(
        recuerdo_id: str,
        payload: Correccion,
        cases: case_store_dep,
        access: access_dep,
        case_id: str | None = None,
    ) -> CorreccionHecha:
        titular = _con_dueno(_titular(case_id, access, cases))
        memoria = _memoria(cases)
        with _hindsight():
            try:
                nuevos = memoria.corregir(titular, recuerdo_id, texto=payload.texto)
            except CorreccionInvalidaError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if nuevos is None:
                raise HTTPException(status_code=404, detail="recuerdo no encontrado")
            titulos = _titulos(memoria, titular)
        return CorreccionHecha(recuerdos=[_vista(r, titulos) for r in nuevos])

    @router.delete("/recuerdos/{recuerdo_id}", status_code=204, dependencies=guarded)
    def delete_recuerdo(
        recuerdo_id: str, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> Response:
        titular = _con_dueno(_titular(case_id, access, cases))
        with _hindsight():
            if not _memoria(cases).olvidar(titular, recuerdo_id):
                raise HTTPException(status_code=404, detail="recuerdo no encontrado")
        return Response(status_code=204)

    @router.delete("/recuerdos", response_model=OlvidoTotal, dependencies=guarded)
    def delete_recuerdos(cases: case_store_dep, access: access_dep, case_id: str | None = None) -> OlvidoTotal:
        titular = _con_dueno(_titular(case_id, access, cases))
        with _hindsight():
            return OlvidoTotal(**_memoria(cases).olvidar_todo(titular))

    @router.get("/conversaciones", response_model=ConversacionesResponse, dependencies=guarded)
    def get_conversaciones(
        cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> ConversacionesResponse:
        titular = _titular(case_id, access, cases)
        if titular.vacio:
            return ConversacionesResponse(conversaciones=[])
        memoria = _memoria(cases)
        with _hindsight():
            conversaciones = memoria.conversaciones(titular)
            titulos = memoria.titulos(titular, conversaciones)
        return ConversacionesResponse(
            conversaciones=[_conversacion(c, titulos.get(c.id, "Conversación")) for c in conversaciones]
        )

    @router.get("/conversaciones/{conversacion_id}", response_model=ConversacionDetalle, dependencies=guarded)
    def get_conversacion(
        conversacion_id: str, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> ConversacionDetalle:
        titular = _con_dueno(_titular(case_id, access, cases))
        with _hindsight():
            encontrada = _memoria(cases).conversacion(titular, conversacion_id)
        if encontrada is None:
            raise HTTPException(status_code=404, detail="conversación no encontrada")
        conversacion, recuerdos = encontrada
        titulos = {conversacion.id: conversacion.titulo}
        return ConversacionDetalle(
            conversacion=_conversacion(conversacion, conversacion.titulo),
            mensajes=[MensajeVista(texto=m.texto, fecha=m.fecha) for m in conversacion.mensajes],
            recuerdos=[_vista(r, titulos) for r in recuerdos],
        )

    @router.delete("/conversaciones/{conversacion_id}", status_code=204, dependencies=guarded)
    def delete_conversacion(
        conversacion_id: str, cases: case_store_dep, access: access_dep, case_id: str | None = None
    ) -> Response:
        titular = _con_dueno(_titular(case_id, access, cases))
        with _hindsight():
            if not _memoria(cases).borrar_conversacion(titular, conversacion_id):
                raise HTTPException(status_code=404, detail="conversación no encontrada")
        return Response(status_code=204)

    return router

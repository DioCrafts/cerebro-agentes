"""User feedback on the assistant's answers (continuous evaluation).

* ``GET /evaluacion/consentimiento`` — the opt-in text (and its version) the UI shows next to the checkbox,
  and the kinds of correction the user can choose.
* ``POST /evaluacion/feedback`` — 👍/👎 on one answer, optionally «corregir» (what was wrong and the right
  answer or figure). Storing the conversation as an evaluation case needs ``guardar_para_evaluacion`` AND the
  current consent version, every time: there is no standing consent. See
  :mod:`asistente.nucleo.evaluacion.captura` for what is stored and where.
* ``GET /evaluacion/feedback?case_id=…`` — the caller's feedback on one of their cases, with what is stored.
* ``DELETE /evaluacion/feedback/{id}`` — withdraw it: the stored content leaves the case's audit trail.

Only the owner of the case can do any of this; somebody else's case answers 404, like a missing one. Nothing
here reaches the evaluation suite: an operator reviews and promotes cases from the command line.

Built by :func:`build_router` with the case store provider passed in (see ``api.py``), like the other routers.
The module deliberately has no ``from __future__ import annotations`` (FastAPI resolves the local aliases).
"""

from collections.abc import Callable
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.evaluacion.captura import (
    CONSENTIMIENTO_TEXTO,
    CONSENTIMIENTO_VERSION,
    ETIQUETAS_CORRECCION,
    FeedbackEntrada,
    FeedbackRegistrado,
    FeedbackStore,
    RespuestaNoEncontradaError,
    capturar,
)
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access


class TipoCorreccionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clave: str
    etiqueta: str


class ConsentimientoEvaluacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    texto: str
    tipos: list[TipoCorreccionInfo]


class FeedbackRespuesta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    guardado_para_evaluacion: bool
    estado: str
    etiquetas: list[str] = Field(default_factory=list)
    mensaje: str


class FeedbackDetalle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registro: FeedbackRegistrado
    # What is stored for evaluation (None: vote only, or withdrawn).
    contenido: dict[str, Any] | None = None


class FeedbackLista(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    feedback: list[FeedbackDetalle]


_stores: dict[str, FeedbackStore] = {}
_stores_lock = threading.Lock()


def store_para(db_path: str) -> FeedbackStore:
    """One feedback store per database file (the cases' file: one backup and one erasure cover both)."""
    with _stores_lock:
        store = _stores.get(db_path)
        if store is None:
            store = _stores[db_path] = FeedbackStore(db_path)
        return store


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["evaluacion"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key)]

    def _feedback_store(cases: case_store_dep) -> FeedbackStore:
        return store_para(cases.db_path)

    feedback_store_dep = Annotated[FeedbackStore, Depends(_feedback_store)]

    @router.get("/evaluacion/consentimiento", response_model=ConsentimientoEvaluacion, dependencies=guarded)
    def get_consentimiento() -> ConsentimientoEvaluacion:
        return ConsentimientoEvaluacion(
            version=CONSENTIMIENTO_VERSION,
            texto=CONSENTIMIENTO_TEXTO,
            tipos=[TipoCorreccionInfo(clave=k, etiqueta=v) for k, v in ETIQUETAS_CORRECCION.items()],
        )

    @router.post("/evaluacion/feedback", response_model=FeedbackRespuesta, status_code=201, dependencies=guarded)
    def post_feedback(
        payload: dict[str, Any], cases: case_store_dep, access: access_dep, store: feedback_store_dep
    ) -> FeedbackRespuesta:
        # Validated here (not by FastAPI) so a cross-field rule reaches the user as a plain Spanish 422.
        try:
            entrada = FeedbackEntrada.model_validate(payload)
        except ValidationError as exc:
            mensajes = "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
            raise HTTPException(status_code=422, detail=mensajes or "Valoración no válida.") from exc
        case = None
        if entrada.case_id is not None:
            access.check(entrada.case_id)
            case = cases.get_case(entrada.case_id)
            if case is None:
                raise HTTPException(status_code=404, detail="case not found")
        elif entrada.guardar_para_evaluacion:
            raise HTTPException(
                status_code=422, detail="Para guardar la conversación para evaluación hace falta un expediente."
            )
        try:
            registro = capturar(entrada, store, case=case, cases=cases if case is not None else None)
        except RespuestaNoEncontradaError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        mensaje = (
            "Gracias. Hemos guardado la conversación en tu expediente para revisarla; puedes retirarla cuando quieras."
            if registro.guardado_para_evaluacion
            else "Gracias por tu valoración. No se ha guardado el contenido de la conversación."
        )
        return FeedbackRespuesta(
            id=registro.id,
            guardado_para_evaluacion=registro.guardado_para_evaluacion,
            estado=registro.estado,
            etiquetas=registro.etiquetas,
            mensaje=mensaje,
        )

    @router.get("/evaluacion/feedback", response_model=FeedbackLista, dependencies=guarded)
    def get_feedback(
        case_id: str, cases: case_store_dep, access: access_dep, store: feedback_store_dep
    ) -> FeedbackLista:
        access.check(case_id)
        if cases.get_case(case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        return FeedbackLista(
            case_id=case_id,
            feedback=[FeedbackDetalle(registro=r, contenido=store.contenido(r)) for r in store.de_caso(case_id)],
        )

    @router.delete("/evaluacion/feedback/{feedback_id}", status_code=204, dependencies=guarded)
    def delete_feedback(
        feedback_id: str, cases: case_store_dep, access: access_dep, store: feedback_store_dep
    ) -> Response:
        registro = store.get(feedback_id)
        if registro is None or registro.case_id is None:
            raise HTTPException(status_code=404, detail="Valoración no encontrada.")
        access.check(registro.case_id)
        store.retirar_contenido(registro)
        store.borrar(feedback_id)
        return Response(status_code=204)

    return router


__all__ = ["build_router", "store_para"]

"""Long-running tasks over HTTP (``asistente.nucleo.tareas``): list, propose, accept, confirm or skip a step, pause,
resume, retry, cancel.

* ``GET  /tareas/plantillas`` — the templates (id, version, title, description, parameters, step titles).
* ``GET  /tareas?case_id=`` — the case's tasks (each one advanced first, so a confirmed action shows up).
* ``POST /tareas`` — a task from a template, as a PROPOSAL (``aceptar: true`` when the user pressed «Empezar»).
* ``POST /tareas/plan`` — a custom plan through the gate (422 with the gate's errors).
* ``POST /tareas/proponer`` — a model proposes a plan for a goal (gate; 503 without a configured model).
* ``GET  /tareas/{id}`` — the task, its summary and its history.
* ``POST /tareas/{id}/aceptar|avanzar|pausar|reanudar|reintentar|cancelar``.
* ``POST /tareas/{id}/pasos/{paso}/confirmar|omitir`` — a step that waits for the user. A pending action is
  confirmed only with ``POST /acciones/{id}/confirmar`` (its own button), never here.

Same access rules as every case endpoint: API key, session when accounts are on, and ownership (somebody else's
task reads exactly like one that does not exist: 404). Errors come back with one Spanish sentence in ``detail``.
The engine runs here without specialist consultations (``python -m asistente.nucleo.tareas worker`` does those).

Built by :func:`build_router` with the case store passed in (see ``api.py``). No ``from __future__ import
annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import date
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.agent.providers.base import LLMProvider
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.tareas import servicio
from asistente.nucleo.tareas.modelo import EN_MARCHA, EntradaHistorial, PasoSpec, Tarea
from asistente.nucleo.tareas.motor import Motor
from asistente.nucleo.tareas.plantillas import catalogo
from asistente.nucleo.tareas.validacion import PlanInvalidoError
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access, require_session

logger = logging.getLogger(__name__)


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TareaDetalle(_Modelo):
    tarea: Tarea
    resumen: dict[str, Any]
    historial: list[EntradaHistorial]
    # Notes from the gate (a module that is off…), only when the task was just proposed.
    avisos: list[str] = Field(default_factory=list)


class TareasResponse(_Modelo):
    case_id: str
    tareas: list[dict[str, Any]]


class PlantillasResponse(_Modelo):
    plantillas: list[dict[str, Any]]


class CrearDesdePlantilla(_Modelo):
    case_id: str = Field(min_length=1)
    plantilla: str = Field(min_length=1, max_length=60)
    objetivo: str = Field(min_length=1, max_length=1000)
    parametros: dict[str, Any] = Field(default_factory=dict)
    # The user pressed «Empezar» (their acceptance); otherwise it stays a proposal.
    aceptar: bool = False


class CrearPlan(_Modelo):
    case_id: str = Field(min_length=1)
    titulo: str = Field(min_length=1, max_length=120)
    objetivo: str = Field(min_length=1, max_length=1000)
    pasos: list[dict[str, Any]] = Field(min_length=1, max_length=30)


class ProponerPlan(_Modelo):
    case_id: str = Field(min_length=1)
    objetivo: str = Field(min_length=3, max_length=1000)


class Confirmacion(_Modelo):
    fecha: date | None = None
    nota: str = Field(default="", max_length=300)


class Cancelacion(_Modelo):
    motivo: str | None = Field(default=None, max_length=500)


def proveedor_planificador() -> LLMProvider:
    """The model that proposes custom plans (tests override this dependency)."""
    from asistente.nucleo.agent.providers import create_provider
    from asistente.plataforma.ops.consumo import medir

    try:
        # Metered and capped like every other model call (the call runs inside ``contexto_consumo``).
        return medir(create_provider(), "tareas:plan")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="No hay ningún modelo configurado para proponer planes.") from exc


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["tareas"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key)]

    @contextmanager
    def _motor(cases: SQLiteCaseStateStore, access: CaseAccess) -> Generator[Motor]:
        motor = Motor(cases.db_path, ejecutar_consultas=False, auth=access.store)
        try:
            yield motor
        finally:
            motor.close()

    def _caso(case_id: str, cases: SQLiteCaseStateStore, access: CaseAccess) -> None:
        access.check(case_id)
        if cases.get_case(case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")

    def _tarea(motor: Motor, tarea_id: str, access: CaseAccess) -> Tarea:
        tarea = motor.store.get(tarea_id)
        if tarea is None:
            raise HTTPException(status_code=404, detail="tarea no encontrada")
        try:
            access.check(tarea.case_id)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="tarea no encontrada") from exc
        return tarea

    def _detalle(motor: Motor, tarea: Tarea, avisos: list[str] | None = None) -> TareaDetalle:
        return TareaDetalle(
            tarea=tarea, resumen=servicio.resumen(tarea), historial=motor.store.historial(tarea.id), avisos=avisos or []
        )

    def _activos(motor: Motor, case_id: str) -> list[str]:
        return [m.clave for m in motor.modulos_de(case_id)]

    def _user_id(access: CaseAccess) -> str | None:
        return access.user.id if access.user is not None else None

    @router.get(
        "/tareas/plantillas",
        response_model=PlantillasResponse,
        dependencies=[*guarded, Depends(require_session)],
    )
    def get_plantillas() -> PlantillasResponse:
        return PlantillasResponse(plantillas=catalogo())

    @router.get("/tareas", response_model=TareasResponse, dependencies=guarded)
    def get_tareas(case_id: str, cases: case_store_dep, access: access_dep) -> TareasResponse:
        _caso(case_id, cases, access)
        with _motor(cases, access) as motor:
            tareas = []
            for tarea in motor.store.list(case_id):
                if tarea.estado in EN_MARCHA:
                    tarea = motor.avanzar(tarea.id) or tarea
                tareas.append(servicio.resumen(tarea))
        return TareasResponse(case_id=case_id, tareas=tareas)

    @router.post("/tareas", response_model=TareaDetalle, dependencies=guarded)
    def post_tarea(payload: CrearDesdePlantilla, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        _caso(payload.case_id, cases, access)
        with _motor(cases, access) as motor:
            try:
                tarea, avisos = servicio.proponer_desde_plantilla(
                    motor.store,
                    case_id=payload.case_id,
                    user_id=_user_id(access),
                    plantilla_id=payload.plantilla,
                    objetivo=payload.objetivo,
                    hoy=motor.hoy(),
                    parametros=payload.parametros,
                    modulos_activos=_activos(motor, payload.case_id),
                )
            except PlanInvalidoError as exc:
                raise HTTPException(status_code=422, detail="; ".join(exc.errores)) from exc
            if payload.aceptar:
                tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
            return _detalle(motor, tarea, avisos)

    @router.post("/tareas/plan", response_model=TareaDetalle, dependencies=guarded)
    def post_plan(payload: CrearPlan, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        _caso(payload.case_id, cases, access)
        with _motor(cases, access) as motor:
            try:
                tarea, avisos = servicio.proponer_plan(
                    motor.store,
                    case_id=payload.case_id,
                    user_id=_user_id(access),
                    titulo=payload.titulo,
                    objetivo=payload.objetivo,
                    pasos=payload.pasos,
                    modulos_activos=_activos(motor, payload.case_id),
                )
            except PlanInvalidoError as exc:
                raise HTTPException(status_code=422, detail="El plan no es válido: " + "; ".join(exc.errores)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"El plan no es válido: {exc}") from exc
            return _detalle(motor, tarea, avisos)

    @router.post("/tareas/proponer", response_model=TareaDetalle, dependencies=guarded)
    def post_proponer(
        payload: ProponerPlan,
        cases: case_store_dep,
        access: access_dep,
        provider: Annotated[LLMProvider, Depends(proveedor_planificador)],
    ) -> TareaDetalle:
        from asistente.nucleo.tareas.planificacion import proponer_con_modelo

        _caso(payload.case_id, cases, access)
        with _motor(cases, access) as motor:
            modulos = list(motor.modulos_de(payload.case_id))
            try:
                from asistente.plataforma.ops.consumo import contexto_consumo

                with contexto_consumo(user_id=_user_id(access), case_id=payload.case_id):
                    titulo, pasos, _avisos = proponer_con_modelo(provider, objetivo=payload.objetivo, modulos=modulos)
                tarea, avisos = servicio.proponer_plan(
                    motor.store,
                    case_id=payload.case_id,
                    user_id=_user_id(access),
                    titulo=titulo,
                    objetivo=payload.objetivo,
                    pasos=[p.model_dump(include=set(PasoSpec.model_fields)) for p in pasos],
                    modulos_activos=[m.clave for m in modulos],
                )
            except PlanInvalidoError as exc:
                raise HTTPException(
                    status_code=422, detail="No se ha podido proponer un plan válido: " + "; ".join(exc.errores[:5])
                ) from exc
            except Exception as exc:  # ProviderError or any failure of the model: not the user's error
                logger.exception("A plan could not be proposed by the model")
                raise HTTPException(status_code=503, detail="El modelo no está disponible ahora.") from exc
            return _detalle(motor, tarea, avisos)

    @router.get("/tareas/{tarea_id}", response_model=TareaDetalle, dependencies=guarded)
    def get_tarea(tarea_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        with _motor(cases, access) as motor:
            tarea = _tarea(motor, tarea_id, access)
            if tarea.estado in EN_MARCHA:
                tarea = motor.avanzar(tarea.id) or tarea
            return _detalle(motor, tarea)

    def _operar(
        tarea_id: str, cases: SQLiteCaseStateStore, access: CaseAccess, operacion: Callable[[Motor, Tarea], Tarea]
    ) -> TareaDetalle:
        with _motor(cases, access) as motor:
            tarea = _tarea(motor, tarea_id, access)
            try:
                tarea = operacion(motor, tarea)
            except servicio.OperacionNoPermitidaError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except servicio.TareaNoEncontradaError as exc:
                raise HTTPException(status_code=404, detail="paso no encontrado") from exc
            return _detalle(motor, tarea)

    @router.post("/tareas/{tarea_id}/aceptar", response_model=TareaDetalle, dependencies=guarded)
    def post_aceptar(tarea_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(tarea_id, cases, access, lambda m, t: servicio.aceptar(m, t.id, fuente="boton"))

    @router.post("/tareas/{tarea_id}/avanzar", response_model=TareaDetalle, dependencies=guarded)
    def post_avanzar(tarea_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(tarea_id, cases, access, lambda m, t: m.avanzar(t.id) or t)

    @router.post("/tareas/{tarea_id}/pausar", response_model=TareaDetalle, dependencies=guarded)
    def post_pausar(tarea_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(tarea_id, cases, access, lambda m, t: servicio.pausar(m, t.id, fuente="boton"))

    @router.post("/tareas/{tarea_id}/reanudar", response_model=TareaDetalle, dependencies=guarded)
    def post_reanudar(tarea_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(tarea_id, cases, access, lambda m, t: servicio.reanudar(m, t.id, fuente="boton"))

    @router.post("/tareas/{tarea_id}/reintentar", response_model=TareaDetalle, dependencies=guarded)
    def post_reintentar(tarea_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(tarea_id, cases, access, lambda m, t: servicio.reintentar(m, t.id, fuente="boton"))

    @router.post("/tareas/{tarea_id}/cancelar", response_model=TareaDetalle, dependencies=guarded)
    def post_cancelar(tarea_id: str, payload: Cancelacion, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(
            tarea_id,
            cases,
            access,
            lambda m, t: servicio.cancelar(m, t.id, fuente="boton", motivo=payload.motivo, user_id=_user_id(access)),
        )

    @router.post("/tareas/{tarea_id}/pasos/{paso_id}/confirmar", response_model=TareaDetalle, dependencies=guarded)
    def post_confirmar(
        tarea_id: str, paso_id: str, payload: Confirmacion, cases: case_store_dep, access: access_dep
    ) -> TareaDetalle:
        return _operar(
            tarea_id,
            cases,
            access,
            lambda m, t: servicio.confirmar_paso(
                m, t.id, paso_id, fuente="boton", fecha=payload.fecha, nota=payload.nota
            ),
        )

    @router.post("/tareas/{tarea_id}/pasos/{paso_id}/omitir", response_model=TareaDetalle, dependencies=guarded)
    def post_omitir(tarea_id: str, paso_id: str, cases: case_store_dep, access: access_dep) -> TareaDetalle:
        return _operar(tarea_id, cases, access, lambda m, t: servicio.omitir_paso(m, t.id, paso_id, fuente="boton"))

    return router


__all__ = ["Cancelacion", "Confirmacion", "CrearDesdePlantilla", "CrearPlan", "TareaDetalle", "build_router"]

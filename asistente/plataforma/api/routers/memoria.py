"""«Lo que sé de tus preferencias»: the user's decisions, visible and editable, and the notice buttons.

* ``GET /cases/{case_id}/decisiones`` — every decision of the case with its state (in force, may come back,
  expired…), the known ámbitos (for the form) and the history (redacted for what the user deleted).
* ``POST /cases/{case_id}/decisiones`` — the user writes one (or presses «No me interesa» / «Ya lo hice» on
  a lever of the optimiser: ``boton``).
* ``PUT /cases/{case_id}/decisiones/{id}`` — change its kind, words, expiry or reopening conditions.
* ``DELETE /cases/{case_id}/decisiones/{id}`` — forget it (the history keeps no words of it).
* ``POST /cases/{case_id}/decisiones/aviso/{notification_id}`` — the buttons on a notice: ``no_me_interesa``,
  ``ya_lo_hice``, ``recuerdamelo`` (with ``hasta``). The notice leaves the inbox.

Same access rules as every ``/cases/{id}/…`` endpoint (API key, session, ownership: somebody else's case is a
404). The decisions live in the cases' SQLite file; when a change affects the tax optimiser the year-end plan
is rebuilt at once, and this week's digest is rebuilt when it listed a notice that went.

Built by :func:`build_router` with the stores passed in (see ``api.py``), like the other routers. No
``from __future__ import annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable
from datetime import date
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.almacen import tabla_existe
from asistente.nucleo.memoria.ambitos import CATALOGO, ambito_de_palanca
from asistente.nucleo.memoria.modelo import Condicion, Propuesta, TipoDecision, evaluar
from asistente.nucleo.memoria.servicio import (
    DecisionInvalidaError,
    Resultado,
    borrar,
    decisiones_del_caso,
    describir,
    desde_aviso,
    editar,
    registrar,
)
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access, require_case_access

logger = logging.getLogger(__name__)


class AmbitoInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ambito: str
    etiqueta: str


class DecisionesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    hoy: date
    # Each decision as ``describir`` shows it: its fields plus estado, motivo_estado, etiqueta, modulo.
    decisiones: list[dict[str, Any]]
    catalogo: list[AmbitoInfo]
    historial: list[dict[str, Any]]


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Either an ámbito, or the id (and filing option) of a lever of the optimiser's plan.
    ambito: str | None = Field(default=None, max_length=200)
    palanca: str | None = Field(default=None, max_length=80)
    opcion: str | None = Field(default=None, max_length=80)
    tipo: TipoDecision = "rechazo"
    razon: str | None = Field(default=None, max_length=500)
    # Absent: the default of the kind; null: no expiry.
    caducidad: date | None = None
    condiciones_de_reapertura: list[Condicion] = Field(default_factory=list, max_length=5)
    propuesta: Propuesta | None = None
    # A button on a proposal of the optimiser (otherwise the user wrote it in the settings).
    boton: Literal["no_me_interesa", "ya_lo_hice"] | None = None


class DecisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoDecision | None = None
    razon: str | None = Field(default=None, min_length=1, max_length=500)
    caducidad: date | None = None
    condiciones_de_reapertura: list[Condicion] | None = Field(default=None, max_length=5)


class RespuestaAviso(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boton: Literal["no_me_interesa", "ya_lo_hice", "recuerdamelo"]
    razon: str | None = Field(default=None, max_length=400)
    # «Recuérdamelo más tarde»: from when (default: a week). Other buttons: until when (default per kind).
    hasta: date | None = None


class DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: dict[str, Any]
    avisos_retirados: int
    plan_recalculado: bool
    resumen_recalculado: bool


_RAZON_BOTON = {"no_me_interesa": "No me interesa", "ya_lo_hice": "Ya lo hice"}


def build_router(
    *,
    get_case_store: Callable[[], SQLiteCaseStateStore],
    get_notification_store: Callable[[], NotificationStore],
    get_accion_store: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter()
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    notification_store_dep = Annotated[NotificationStore, Depends(get_notification_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key), Depends(require_case_access)]

    def _sin_acciones() -> None:
        return None

    accion_store_dep = Annotated[Any, Depends(get_accion_store or _sin_acciones)]

    def _caso(case_id: str, cases: SQLiteCaseStateStore) -> Any:
        case = cases.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    def _owner(access: CaseAccess) -> str | None:
        return access.user.id if access.user is not None else None

    def _rehacer(
        resultado: Resultado, case: Any, cases: SQLiteCaseStateStore, notifications: NotificationStore, acciones: Any
    ) -> DecisionResponse:
        """Rebuild what the decision removed: the year-end plan (now with it) and this week's digest."""
        plan = resumen = False
        hoy = reloj.hoy()
        if resultado.plan_retirado:
            try:
                from asistente.nucleo.proactive.jobs import plan_fin_de_ejercicio

                plan_fin_de_ejercicio(case, hoy=hoy, store=notifications)
                plan = True
            except Exception:
                logger.exception("Could not rebuild the year-end plan of case %s", case.case_id)
        if resultado.resumen_retirado:
            try:
                from asistente.nucleo.proactive.bandeja import resumen_semanal

                resumen = bool(resumen_semanal(case, hoy=hoy, store=notifications, acciones=acciones))
            except Exception:
                logger.exception("Could not rebuild the weekly digest of case %s", case.case_id)
        hechos = {f.name: f.value for f in case.facts}
        decision = resultado.decision
        return DecisionResponse(
            decision=describir(decision, evaluar(decision, hoy=hoy, hechos=hechos)),
            avisos_retirados=len(resultado.avisos_retirados),
            plan_recalculado=plan,
            resumen_recalculado=resumen,
        )

    @router.get("/cases/{case_id}/decisiones", response_model=DecisionesResponse, dependencies=guarded)
    def get_decisiones(case_id: str, cases: case_store_dep) -> DecisionesResponse:
        case = _caso(case_id, cases)
        hoy = reloj.hoy()
        hechos = {f.name: f.value for f in case.facts}
        decisiones = decisiones_del_caso(cases.db_path, case_id)
        from asistente.nucleo.memoria.almacen import almacen

        historial = almacen(cases.db_path).historial(case_id) if tabla_existe(cases.db_path) else []
        return DecisionesResponse(
            case_id=case_id,
            hoy=hoy,
            decisiones=[describir(d, evaluar(d, hoy=hoy, hechos=hechos)) for d in decisiones],
            catalogo=[AmbitoInfo(ambito=a, etiqueta=e) for a, e in CATALOGO.items()],
            historial=historial,
        )

    @router.post("/cases/{case_id}/decisiones", response_model=DecisionResponse, dependencies=guarded)
    def post_decision(
        case_id: str,
        payload: DecisionCreate,
        cases: case_store_dep,
        notifications: notification_store_dep,
        acciones: accion_store_dep,
        access: access_dep,
    ) -> DecisionResponse:
        case = _caso(case_id, cases)
        ambito = payload.ambito or (ambito_de_palanca(payload.palanca, payload.opcion) if payload.palanca else None)
        if not ambito:
            raise HTTPException(status_code=422, detail="Indica de qué es la decisión («ambito» o «palanca»).")
        razon = (payload.razon or "").strip()
        if payload.boton is not None:
            boton_texto = _RAZON_BOTON[payload.boton]
            razon = f"{boton_texto}: {razon}" if razon else boton_texto
        if not razon:
            raise HTTPException(status_code=422, detail="Escribe con tus palabras qué decides y, si quieres, por qué.")
        tipo: TipoDecision = payload.tipo
        if payload.boton == "ya_lo_hice":
            tipo = "aceptacion"
        elif payload.boton == "no_me_interesa":
            tipo = "rechazo"
        try:
            resultado = registrar(
                cases.db_path,
                case_id=case_id,
                user_id=_owner(access),
                ambito=ambito,
                tipo=tipo,
                razon=razon,
                fuente="boton" if payload.boton else "ajustes",
                boton=payload.boton,
                caducidad=payload.caducidad if "caducidad" in payload.model_fields_set else "por_defecto",
                condiciones=payload.condiciones_de_reapertura,
                propuesta=payload.propuesta,
                hechos={f.name: f.value for f in case.facts},
                avisos_db=notifications.db_path,
            )
        except DecisionInvalidaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _rehacer(resultado, case, cases, notifications, acciones)

    @router.put("/cases/{case_id}/decisiones/{decision_id}", response_model=DecisionResponse, dependencies=guarded)
    def put_decision(
        case_id: str,
        decision_id: str,
        payload: DecisionUpdate,
        cases: case_store_dep,
        notifications: notification_store_dep,
        acciones: accion_store_dep,
    ) -> DecisionResponse:
        case = _caso(case_id, cases)
        cambios = {k: getattr(payload, k) for k in payload.model_fields_set}
        if cambios.get("tipo", "x") is None or cambios.get("razon", "x") is None:
            raise HTTPException(status_code=422, detail="«tipo» y «razon» no pueden quedar vacíos.")
        if "condiciones_de_reapertura" in cambios:
            cambios["condiciones_de_reapertura"] = [c.model_dump() for c in cambios["condiciones_de_reapertura"] or []]
        try:
            resultado = (
                editar(
                    cases.db_path,
                    case_id=case_id,
                    decision_id=decision_id,
                    cambios=cambios,
                    hechos={f.name: f.value for f in case.facts},
                    avisos_db=notifications.db_path,
                )
                if tabla_existe(cases.db_path)
                else None
            )
        except DecisionInvalidaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if resultado is None:
            raise HTTPException(status_code=404, detail="decision not found")
        return _rehacer(resultado, case, cases, notifications, acciones)

    @router.delete("/cases/{case_id}/decisiones/{decision_id}", status_code=204, dependencies=guarded)
    def delete_decision(
        case_id: str,
        decision_id: str,
        cases: case_store_dep,
        notifications: notification_store_dep,
        acciones: accion_store_dep,
    ) -> Response:
        case = _caso(case_id, cases)
        resultado = (
            borrar(cases.db_path, case_id=case_id, decision_id=decision_id, avisos_db=notifications.db_path)
            if tabla_existe(cases.db_path)
            else None
        )
        if resultado is None:
            raise HTTPException(status_code=404, detail="decision not found")
        _rehacer(resultado, case, cases, notifications, acciones)
        return Response(status_code=204)

    @router.post(
        "/cases/{case_id}/decisiones/aviso/{notification_id}", response_model=DecisionResponse, dependencies=guarded
    )
    def post_respuesta_aviso(
        case_id: str,
        notification_id: str,
        payload: RespuestaAviso,
        cases: case_store_dep,
        notifications: notification_store_dep,
        acciones: accion_store_dep,
        access: access_dep,
    ) -> DecisionResponse:
        case = _caso(case_id, cases)
        if payload.hasta is not None and payload.hasta <= reloj.hoy():
            raise HTTPException(status_code=422, detail="La fecha debe ser posterior a hoy.")
        try:
            resultado = desde_aviso(
                cases.db_path,
                case_id=case_id,
                notification_id=notification_id,
                boton=payload.boton,
                razon=payload.razon,
                hasta=payload.hasta,
                user_id=_owner(access),
                hechos={f.name: f.value for f in case.facts},
                avisos_db=notifications.db_path,
            )
        except DecisionInvalidaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if resultado is None:
            raise HTTPException(status_code=404, detail="notification not found")
        return _rehacer(resultado, case, cases, notifications, acciones)

    return router

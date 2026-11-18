"""The household council over HTTP (``asistente/nucleo/consejo``, docs/consejo/arquitectura.md).

* ``GET /cases/{case_id}/consejo`` — the case's sessions, newest first (state, estimate, cost, the recommendation in
  words once there is a ruling).
* ``POST /cases/{case_id}/consejo`` ``{pregunta, opciones?, preferencia?}`` — convene it from the UI: a session in
  ``pendiente_confirmacion`` with its cost and time estimate. No model is called; nothing runs until ``confirmar``.
* ``POST /cases/{case_id}/consejo/detectar`` ``{pregunta}`` — the deterministic detector's verdict (is this a hard
  decision, and why): the chat offers the council with it when the brain answered alone. No model call.
* ``GET /cases/{case_id}/consejo/{sesion_id}`` — one session: live progress while it runs; once it ends, the ruling,
  the dossier (including what was anonymised), the proposals, the critiques and the rankings. Who wrote each
  proposal and who reviewed whom is revealed ONLY once the session is ``terminada`` (before, members show as
  «miembro_a», «miembro_b»…).
* ``POST …/{sesion_id}/confirmar`` (the user accepts the estimate: queued once, started in the background),
  ``…/rechazar``, ``…/cancelar``; ``DELETE …/{sesion_id}``.

Same access rules as every ``/cases/{id}/…`` endpoint (API key, session, ownership: somebody else's case — or a
session of another case — is a 404). Built by :func:`build_router` with the case store passed in (see ``api.py``).
No ``from __future__ import annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.consejo.almacen import PasoProgreso, Registro
from asistente.nucleo.consejo.contrato import (
    CosteSesion,
    Critica,
    Dictamen,
    EstadoSesion,
    ExpedienteConsejo,
    Propuesta,
    Ranking,
)
from asistente.nucleo.consejo.deteccion import Deteccion
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access, require_case_access

_HTTP = {"no_encontrada": 404, "estado": 409, "desactivado": 503, "invalida": 422, "tope": 422}


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConvocarRequest(_Modelo):
    pregunta: str = Field(min_length=1, max_length=4000)
    opciones: list[str] = Field(default_factory=list, max_length=6)
    # The user's own leaning, if they want to say it (sealed: the members read it only in the revision round).
    preferencia: str | None = Field(default=None, max_length=600)


class DetectarRequest(_Modelo):
    pregunta: str = Field(min_length=1, max_length=4000)


class ResumenSesion(_Modelo):
    id: str
    estado: EstadoSesion
    fase: str | None
    pregunta_usuario: str
    creada: datetime
    actualizada: datetime
    estimacion_usd: Decimal
    tope_usd: Decimal
    coste_usd: Decimal
    minutos_estimados: int
    origen: str
    # Once there is a ruling: the recommendation in words, the consensus and the confidence.
    recomendacion: str | None = None
    consenso: str | None = None
    confianza: float | None = None
    # Why the ruling is partial (a stop — the cost cap, the budget — ended the session early), None when complete.
    parcial: str | None = None
    notificacion_id: str | None = None
    error: str | None = None


class SesionVista(ResumenSesion):
    miembros: list[str] = Field(default_factory=list)  # the seats' names (no models, no authorship)
    motivo: str = ""
    opciones_sugeridas: list[str] = Field(default_factory=list)
    precios_conocidos: bool = True
    llamadas_estimadas: int = 0
    desglose_usd: dict[str, Decimal] = Field(default_factory=dict)
    progreso: list[PasoProgreso] = Field(default_factory=list)
    coste: CosteSesion
    expediente: ExpedienteConsejo | None = None
    propuestas: list[Propuesta] = Field(default_factory=list)
    criticas: list[Critica] = Field(default_factory=list)
    rankings: list[Ranking] = Field(default_factory=list)
    dictamen: Dictamen | None = None
    # Authorship (who wrote which proposal, who reviewed whom, which model served each seat) is shown.
    autoria_revelada: bool = False
    # Confirmation: whether THIS request queued it (False: it was already confirmed).
    confirmada_ahora: bool | None = None


class ListaSesiones(_Modelo):
    case_id: str
    disponible: bool
    sesiones: list[ResumenSesion]


def _resumen_campos(registro: Registro) -> dict[str, Any]:
    from asistente.nucleo.consejo.servicio import CONSENSO_EN_PALABRAS, motivo_parcial, recomendacion_en_palabras

    sesion = registro.sesion
    dictamen = sesion.dictamen if sesion.estado == "terminada" else None
    return {
        "id": sesion.id,
        "estado": sesion.estado,
        "fase": sesion.fase,
        "pregunta_usuario": sesion.pregunta_usuario,
        "creada": sesion.creada,
        "actualizada": sesion.actualizada,
        "estimacion_usd": sesion.estimacion_usd,
        "tope_usd": sesion.tope_usd,
        "coste_usd": sesion.coste.usd.quantize(Decimal("0.0001")),
        "minutos_estimados": registro.extra.minutos_estimados,
        "origen": registro.extra.origen,
        "recomendacion": recomendacion_en_palabras(dictamen, sesion) if dictamen is not None else None,
        "consenso": CONSENSO_EN_PALABRAS.get(dictamen.consenso) if dictamen is not None else None,
        "confianza": dictamen.confianza if dictamen is not None else None,
        "parcial": motivo_parcial(dictamen),
        "notificacion_id": registro.notificacion_id,
        "error": sesion.error,
    }


def resumen(registro: Registro) -> ResumenSesion:
    return ResumenSesion(**_resumen_campos(registro))


def _anonimas(
    propuestas: list[Propuesta], criticas: list[Critica], rankings: list[Ranking]
) -> tuple[list[Propuesta], list[Critica], list[Ranking]]:
    """Members as «miembro_a», «miembro_b»… (in order of appearance): authorship hidden until the ruling."""
    alias: dict[str, str] = {}

    def de(seat: str) -> str:
        if seat not in alias:
            alias[seat] = f"miembro_{chr(ord('a') + len(alias))}" if len(alias) < 26 else f"miembro_{len(alias)}"
        return alias[seat]

    return (
        [p.model_copy(update={"miembro_id": de(p.miembro_id)}) for p in propuestas],
        [c.model_copy(update={"evaluador_id": de(c.evaluador_id)}) for c in criticas],
        [r.model_copy(update={"evaluador_id": de(r.evaluador_id)}) for r in rankings],
    )


def vista(registro: Registro, *, confirmada_ahora: bool | None = None) -> SesionVista:
    sesion = registro.sesion
    revelada = sesion.estado == "terminada"
    propuestas, criticas, rankings = list(sesion.propuestas), list(sesion.criticas), list(sesion.rankings)
    if not revelada:
        propuestas, criticas, rankings = _anonimas(propuestas, criticas, rankings)
    return SesionVista(
        **_resumen_campos(registro),
        miembros=list(registro.extra.miembros),
        motivo=registro.extra.motivo,
        opciones_sugeridas=list(registro.extra.opciones_sugeridas),
        precios_conocidos=registro.extra.precios_conocidos,
        llamadas_estimadas=registro.extra.llamadas_estimadas,
        desglose_usd=dict(registro.extra.desglose),
        progreso=list(registro.extra.progreso),
        coste=sesion.coste,
        expediente=sesion.expediente,
        propuestas=propuestas,
        criticas=criticas,
        rankings=rankings,
        dictamen=sesion.dictamen if revelada else None,
        autoria_revelada=revelada,
        confirmada_ahora=confirmada_ahora,
    )


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["consejo"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key), Depends(require_case_access)]

    def _servicio(cases: SQLiteCaseStateStore) -> Any:
        from asistente.nucleo.consejo.servicio import servicio_para

        return servicio_para(cases.db_path)

    def _caso(case_id: str, cases: SQLiteCaseStateStore, access: CaseAccess) -> Any:
        access.check(case_id)
        case = cases.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    def _usuario(access: CaseAccess) -> str | None:
        return access.user.id if access.user is not None else None

    def _error(exc: Exception) -> HTTPException:
        codigo = getattr(exc, "codigo", "invalida")
        return HTTPException(status_code=_HTTP.get(codigo, 422), detail=getattr(exc, "mensaje", str(exc)))

    @router.get("/cases/{case_id}/consejo", response_model=ListaSesiones, dependencies=guarded)
    def get_sesiones(case_id: str, cases: case_store_dep, access: access_dep) -> ListaSesiones:
        from asistente.nucleo.consejo.servicio import activo

        _caso(case_id, cases, access)
        registros = _servicio(cases).listar(case_id)
        return ListaSesiones(case_id=case_id, disponible=activo(), sesiones=[resumen(r) for r in registros])

    @router.post("/cases/{case_id}/consejo", response_model=SesionVista, dependencies=guarded)
    def post_convocar(case_id: str, payload: ConvocarRequest, cases: case_store_dep, access: access_dep) -> SesionVista:
        from asistente.nucleo.consejo.deteccion import es_decision_dificil
        from asistente.nucleo.consejo.servicio import ConsejoError
        from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso

        case = _caso(case_id, cases, access)
        servicio = _servicio(cases)
        try:
            sesion = servicio.convocar(
                case=case,
                pregunta=payload.pregunta,
                user_id=_usuario(access),
                origen="usuario",
                opciones=payload.opciones,
                preferencia=payload.preferencia,
                modulos=modulos_activos_para_caso(case_id, access.store),
                deteccion=es_decision_dificil(payload.pregunta, None, case),
            )
            registro = servicio.obtener(sesion.id, case_id=case_id, user_id=_usuario(access))
        except ConsejoError as exc:
            raise _error(exc) from exc
        return vista(registro)

    @router.post("/cases/{case_id}/consejo/detectar", response_model=Deteccion, dependencies=guarded)
    def post_detectar(case_id: str, payload: DetectarRequest, cases: case_store_dep, access: access_dep) -> Deteccion:
        from asistente.nucleo.consejo.deteccion import es_decision_dificil

        case = _caso(case_id, cases, access)
        return es_decision_dificil(payload.pregunta, None, case)

    @router.get("/cases/{case_id}/consejo/{sesion_id}", response_model=SesionVista, dependencies=guarded)
    def get_sesion(case_id: str, sesion_id: str, cases: case_store_dep, access: access_dep) -> SesionVista:
        from asistente.nucleo.consejo.servicio import ConsejoError

        _caso(case_id, cases, access)
        try:
            registro = _servicio(cases).obtener(sesion_id, case_id=case_id, user_id=_usuario(access))
        except ConsejoError as exc:
            raise _error(exc) from exc
        return vista(registro)

    @router.post("/cases/{case_id}/consejo/{sesion_id}/confirmar", response_model=SesionVista, dependencies=guarded)
    def post_confirmar(case_id: str, sesion_id: str, cases: case_store_dep, access: access_dep) -> SesionVista:
        from asistente.nucleo.consejo.servicio import ConsejoError

        _caso(case_id, cases, access)
        servicio = _servicio(cases)
        try:
            resultado = servicio.confirmar(sesion_id, case_id=case_id, user_id=_usuario(access))
            registro = servicio.obtener(sesion_id, case_id=case_id, user_id=_usuario(access))
        except ConsejoError as exc:
            raise _error(exc) from exc
        return vista(registro, confirmada_ahora=resultado.confirmada_ahora)

    @router.post("/cases/{case_id}/consejo/{sesion_id}/rechazar", response_model=SesionVista, dependencies=guarded)
    def post_rechazar(case_id: str, sesion_id: str, cases: case_store_dep, access: access_dep) -> SesionVista:
        from asistente.nucleo.consejo.servicio import ConsejoError

        _caso(case_id, cases, access)
        servicio = _servicio(cases)
        try:
            servicio.rechazar(sesion_id, case_id=case_id, user_id=_usuario(access))
            registro = servicio.obtener(sesion_id, case_id=case_id, user_id=_usuario(access))
        except ConsejoError as exc:
            raise _error(exc) from exc
        return vista(registro)

    @router.post("/cases/{case_id}/consejo/{sesion_id}/cancelar", response_model=SesionVista, dependencies=guarded)
    def post_cancelar(case_id: str, sesion_id: str, cases: case_store_dep, access: access_dep) -> SesionVista:
        from asistente.nucleo.consejo.servicio import ConsejoError

        _caso(case_id, cases, access)
        servicio = _servicio(cases)
        try:
            servicio.cancelar(sesion_id, case_id=case_id, user_id=_usuario(access))
            registro = servicio.obtener(sesion_id, case_id=case_id, user_id=_usuario(access))
        except ConsejoError as exc:
            raise _error(exc) from exc
        return vista(registro)

    @router.delete("/cases/{case_id}/consejo/{sesion_id}", status_code=204, dependencies=guarded)
    def delete_sesion(case_id: str, sesion_id: str, cases: case_store_dep, access: access_dep) -> Response:
        from asistente.nucleo.consejo.servicio import ConsejoError

        _caso(case_id, cases, access)
        try:
            _servicio(cases).borrar(sesion_id, case_id=case_id, user_id=_usuario(access))
        except ConsejoError as exc:
            raise _error(exc) from exc
        return Response(status_code=204)

    return router


__all__ = ["ListaSesiones", "ResumenSesion", "SesionVista", "build_router", "resumen", "vista"]

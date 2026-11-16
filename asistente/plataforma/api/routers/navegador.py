"""Browser agent: start a task, follow it live, stop it, close it (see ``asistente/capacidades/navegador``).

* ``GET /navegador/estado`` — whether the operator switched it on, the login modes, the tasks offered (with
  their sites, version and whether they were verified against the real site), the consent text, the user's
  consent and how many tasks they have left today. Never 503: the UI uses it to hide or explain the feature.
* ``POST /navegador/tareas`` — prepare (and, with ``iniciar``, start) a task for a case.
* ``POST /navegador/tareas/{id}/iniciar`` — start a prepared task (e.g. one the chat prepared).
* ``GET /navegador/tareas?case_id=…`` / ``GET /navegador/tareas/{id}`` — live state: events and steps (no
  values), the summary of what would be sent, the import result, the pending action.
* ``GET /navegador/tareas/{id}/captura`` — the latest screenshot (base64), ``Cache-Control: no-store``.
* ``POST /navegador/tareas/{id}/detener`` / ``/cerrar`` — stop; discard (window, screenshot, summary).

Every task endpoint answers 503 while ``NAVEGADOR_ACTIVO`` is false; a task of another user's case reads as
one that does not exist. Starting needs the user's consent (the module switched on by the user), the
acknowledgement of the current consent text, an allowed mode and the rate limits.

Built by :func:`build_router` with the stores passed in (see ``api.py``). No ``from __future__ import
annotations`` (FastAPI resolves the local aliases).
"""

import base64
from collections.abc import Callable
from datetime import datetime
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.capacidades.navegador import vigilancia
from asistente.capacidades.navegador.reparacion import Reparador
from asistente.capacidades.navegador.tareas import (
    TEXTO_CONSENTIMIENTO,
    TEXTO_CONSENTIMIENTO_VERSION,
    GestorTareas,
    Servicios,
    Tarea,
    TareaError,
    consentimiento,
    disponibles,
    gestor,
    modos_permitidos,
)
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.proactive.jobs import hoy_en_espana
from asistente.plataforma import settings
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access, require_session

logger = logging.getLogger(__name__)

Modo = Literal["ventana", "sesion"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParametroInfo(_M):
    nombre: str
    tipo: str
    etiqueta: str
    obligatorio: bool
    opciones: list[str] = Field(default_factory=list)
    personal: bool = False


class GuionInfo(_M):
    clave: str
    version: int
    titulo: str
    descripcion: str
    verificado_contra_sitio_real: bool
    nota_verificacion: str
    demostracion: bool
    sitios: list[str]
    parametros: list[ParametroInfo]
    # What happens at the end: "descarga" (to a module) or "detener" (stop before submitting).
    final: str
    modulo_destino: str | None = None


class EstadoNavegador(_M):
    activo: bool
    modos: list[str]
    consentimiento: bool
    texto_consentimiento: str
    texto_version: str
    tareas: list[GuionInfo]
    max_tareas_dia: int
    usadas_hoy: int


class PasoInfo(_M):
    id: str
    descripcion: str
    estado: str


class EventoInfo(_M):
    momento: datetime
    tipo: str
    texto: str
    paso: str | None = None


class FilaResumen(_M):
    etiqueta: str
    valor: str


class TareaInfo(_M):
    id: str
    case_id: str
    guion: str
    version: int
    titulo: str
    modo: str | None
    estado: str
    creada: datetime
    actualizada: datetime
    terminada: datetime | None
    parametros: dict[str, str]
    eventos: list[EventoInfo]
    pasos: list[PasoInfo]
    tiene_captura: bool
    resumen_envio: list[FilaResumen]
    mensaje: str | None = None
    importacion: str | None = None
    importacion_error: bool = False
    accion_id: str | None = None
    origenes_bloqueados: list[str] = Field(default_factory=list)


class TareasResponse(_M):
    case_id: str
    tareas: list[TareaInfo]


class CapturaResponse(_M):
    png_base64: str
    momento: datetime | None


class TareaRequest(_M):
    case_id: str = Field(min_length=1)
    guion: str = Field(min_length=1, max_length=60)
    parametros: dict[str, Any] = Field(default_factory=dict)
    # Start at once (the user is ready to log in) or only prepare.
    iniciar: bool = False
    modo: Modo = "ventana"
    # Playwright storage_state the user hands over for THIS run (modo "sesion"). Never stored or logged.
    sesion: dict[str, Any] | None = None
    acepto: bool = False
    texto_version: str | None = None


class InicioRequest(_M):
    modo: Modo = "ventana"
    sesion: dict[str, Any] | None = None
    acepto: bool = False
    texto_version: str | None = None


def _info(tarea: Tarea) -> TareaInfo:
    with tarea.lock:
        return TareaInfo(
            id=tarea.id,
            case_id=tarea.case_id,
            guion=tarea.clave,
            version=tarea.version,
            titulo=tarea.titulo,
            modo=tarea.modo,
            estado=tarea.estado,
            creada=tarea.creada,
            actualizada=tarea.actualizada,
            terminada=tarea.terminada,
            parametros=tarea.parametros_visibles(),
            eventos=[EventoInfo(momento=e.momento, tipo=e.tipo, texto=e.texto, paso=e.paso) for e in tarea.eventos],
            pasos=[PasoInfo(**p) for p in tarea.pasos],
            tiene_captura=tarea.captura is not None,
            resumen_envio=[FilaResumen(etiqueta=e, valor=v) for e, v in tarea.resumen_envio],
            mensaje=tarea.mensaje,
            importacion=tarea.importacion,
            importacion_error=tarea.importacion_error,
            accion_id=tarea.accion_id,
            origenes_bloqueados=sorted(set(tarea.bloqueadas)),
        )


def _guion_info(spec: Any) -> GuionInfo:
    from urllib.parse import urlsplit

    g = spec.reglas
    ultimo = g.pasos[-1]
    return GuionInfo(
        clave=spec.fuente.removeprefix("nav_"),
        version=spec.version,
        titulo=g.titulo,
        descripcion=g.descripcion,
        verificado_contra_sitio_real=g.verificado_contra_sitio_real,
        nota_verificacion=g.nota_verificacion,
        demostracion=g.demostracion,
        sitios=sorted({urlsplit(o).hostname or o for o in g.origenes.values()}),
        parametros=[
            ParametroInfo(
                nombre=p.nombre,
                tipo=p.tipo,
                etiqueta=p.etiqueta,
                obligatorio=p.obligatorio,
                opciones=p.opciones,
                personal=p.personal,
            )
            for p in g.parametros
        ],
        final="detener" if ultimo.accion == "detener" else "descarga",
        modulo_destino=g.descarga.destino if g.descarga else None,
    )


def _reparador_por_defecto() -> Reparador | None:
    """A model-backed selector repairer when the operator switched it on and a provider can be built."""
    if not settings.NAVEGADOR_REPARAR_SELECTORES:
        return None
    try:
        from asistente.capacidades.navegador.reparacion import ReparadorLLM
        from asistente.nucleo.agent.providers import create_provider
        from asistente.plataforma.ops.consumo import medir

        return ReparadorLLM(medir(create_provider(effort="low"), "navegador_reparacion"))
    except Exception:
        logger.warning("selector repair unavailable", exc_info=True)
        return None


def build_router(
    *,
    get_case_store: Callable[[], SQLiteCaseStateStore],
    get_accion_store: Callable[..., AccionStore],
    get_gestor: Callable[[], GestorTareas] | None = None,
    get_reparador: Callable[[], Reparador | None] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["navegador"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    accion_store_dep = Annotated[AccionStore, Depends(get_accion_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    gestor_dep = Annotated[GestorTareas, Depends(get_gestor or gestor)]
    reparador_dep = Annotated[Reparador | None, Depends(get_reparador or _reparador_por_defecto)]

    def _activo() -> None:
        if not settings.NAVEGADOR_ACTIVO:
            raise HTTPException(status_code=503, detail="El agente con navegador está desactivado en este servicio.")

    activo_dep = [Depends(require_api_key), Depends(_activo)]

    def _user_id(access: CaseAccess) -> str | None:
        return access.user.id if access.user is not None else None

    def _caso(case_id: str, access: CaseAccess, cases: SQLiteCaseStateStore) -> None:
        access.check(case_id)
        if cases.get_case(case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        # Housekeeping on every task call: redacted diagnoses of erased cases go, and old ones expire.
        try:
            vigilancia.purgar(lambda cid: cases.get_case(cid) is not None)
        except OSError:
            logger.warning("could not purge browser diagnoses", exc_info=True)

    def _tarea(tarea_id: str, access: CaseAccess, gestor_: GestorTareas) -> Tarea:
        tarea = gestor_.get(tarea_id)
        if tarea is None:
            raise HTTPException(status_code=404, detail="task not found")
        try:
            access.check(tarea.case_id)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        if access.user is not None and tarea.user_id is not None and tarea.user_id != access.user.id:
            raise HTTPException(status_code=404, detail="task not found")
        return tarea

    def _http(exc: TareaError) -> HTTPException:
        return HTTPException(status_code=exc.codigo, detail=str(exc))

    def _comprobar_acuse(acepto: bool, texto_version: str | None) -> None:
        if not acepto or texto_version != TEXTO_CONSENTIMIENTO_VERSION:
            raise HTTPException(
                status_code=422,
                detail="Lee y acepta lo que va a hacer el agente con navegador antes de empezar (texto vigente).",
            )

    @router.get(
        "/navegador/estado",
        response_model=EstadoNavegador,
        dependencies=[Depends(require_api_key), Depends(require_session)],
    )
    def get_estado(access: access_dep, gestor_: gestor_dep) -> EstadoNavegador:
        activo = bool(settings.NAVEGADOR_ACTIVO)
        user_id = _user_id(access)
        clave_usuario = gestor_.clave_usuario(user_id, "-") if user_id else None
        return EstadoNavegador(
            activo=activo,
            modos=list(modos_permitidos()) if activo else [],
            consentimiento=activo and consentimiento(user_id, access.store),
            texto_consentimiento=TEXTO_CONSENTIMIENTO,
            texto_version=TEXTO_CONSENTIMIENTO_VERSION,
            tareas=[_guion_info(s) for s in disponibles()] if activo else [],
            max_tareas_dia=settings.NAVEGADOR_MAX_TAREAS_DIA,
            usadas_hoy=gestor_.usadas(clave_usuario) if clave_usuario else 0,
        )

    def _iniciar(
        tarea: Tarea,
        payload: InicioRequest | TareaRequest,
        access: CaseAccess,
        gestor_: GestorTareas,
        cases: SQLiteCaseStateStore,
        acciones: AccionStore,
        reparador: Reparador | None,
    ) -> Tarea:
        _comprobar_acuse(payload.acepto, payload.texto_version)
        servicios = Servicios(cases=cases, acciones=acciones, auth=access.store, reparador=reparador)
        try:
            return gestor_.iniciar(
                tarea, modo=payload.modo, servicios=servicios, user_id=_user_id(access), sesion=payload.sesion
            )
        except TareaError as exc:
            raise _http(exc) from exc

    @router.post("/navegador/tareas", response_model=TareaInfo, status_code=201, dependencies=activo_dep)
    def post_tarea(
        payload: TareaRequest,
        cases: case_store_dep,
        acciones: accion_store_dep,
        access: access_dep,
        gestor_: gestor_dep,
        reparador: reparador_dep,
    ) -> TareaInfo:
        _caso(payload.case_id, access, cases)
        if payload.iniciar:
            _comprobar_acuse(payload.acepto, payload.texto_version)
        try:
            tarea = gestor_.preparar(
                case_id=payload.case_id,
                clave=payload.guion,
                parametros=payload.parametros,
                hoy=hoy_en_espana(),
                user_id=_user_id(access),
            )
        except TareaError as exc:
            raise _http(exc) from exc
        if payload.iniciar:
            try:
                _iniciar(tarea, payload, access, gestor_, cases, acciones, reparador)
            except HTTPException:
                gestor_.cerrar(tarea)
                raise
        return _info(tarea)

    @router.post("/navegador/tareas/{tarea_id}/iniciar", response_model=TareaInfo, dependencies=activo_dep)
    def post_iniciar(
        tarea_id: str,
        payload: InicioRequest,
        cases: case_store_dep,
        acciones: accion_store_dep,
        access: access_dep,
        gestor_: gestor_dep,
        reparador: reparador_dep,
    ) -> TareaInfo:
        tarea = _tarea(tarea_id, access, gestor_)
        _caso(tarea.case_id, access, cases)
        return _info(_iniciar(tarea, payload, access, gestor_, cases, acciones, reparador))

    @router.get("/navegador/tareas", response_model=TareasResponse, dependencies=activo_dep)
    def get_tareas(case_id: str, cases: case_store_dep, access: access_dep, gestor_: gestor_dep) -> TareasResponse:
        _caso(case_id, access, cases)
        user_id = _user_id(access)
        tareas = [t for t in gestor_.de_caso(case_id) if user_id is None or t.user_id in (None, user_id)]
        return TareasResponse(case_id=case_id, tareas=[_info(t) for t in tareas])

    @router.get("/navegador/tareas/{tarea_id}", response_model=TareaInfo, dependencies=activo_dep)
    def get_tarea(tarea_id: str, access: access_dep, gestor_: gestor_dep) -> TareaInfo:
        return _info(_tarea(tarea_id, access, gestor_))

    @router.get("/navegador/tareas/{tarea_id}/captura", response_model=CapturaResponse, dependencies=activo_dep)
    def get_captura(tarea_id: str, response: Response, access: access_dep, gestor_: gestor_dep) -> CapturaResponse:
        tarea = _tarea(tarea_id, access, gestor_)
        response.headers["Cache-Control"] = "no-store"
        with tarea.lock:
            png, momento = tarea.captura, tarea.captura_momento
        if png is None:
            raise HTTPException(status_code=404, detail="Esta tarea no tiene captura (o ya se descartó).")
        return CapturaResponse(png_base64=base64.b64encode(png).decode("ascii"), momento=momento)

    @router.post("/navegador/tareas/{tarea_id}/detener", response_model=TareaInfo, dependencies=activo_dep)
    def post_detener(tarea_id: str, access: access_dep, gestor_: gestor_dep) -> TareaInfo:
        return _info(gestor_.detener(_tarea(tarea_id, access, gestor_)))

    @router.post("/navegador/tareas/{tarea_id}/cerrar", response_model=TareaInfo, dependencies=activo_dep)
    def post_cerrar(tarea_id: str, access: access_dep, gestor_: gestor_dep) -> TareaInfo:
        return _info(gestor_.cerrar(_tarea(tarea_id, access, gestor_)))

    return router

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
import json
import logging
import threading
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request


def _thread_safe_singleton[T](factory: Callable[[], T]) -> Callable[[], T]:
    """Lazy, thread-safe replacement for ``functools.lru_cache(maxsize=1)``.

    ``functools.lru_cache`` is documented as **not** thread-safe for
    concurrent first calls: under a request burst (e.g. uvicorn worker
    thread pool serving the first ``/agent``) two threads can both observe a
    cache miss and run ``factory()`` in parallel. For ``_build_agent``
    that meant loading the 1.1 GB MLX embedder twice into RAM. This
    decorator wraps the factory in double-checked locking so the work
    happens exactly once.

    A FAILED build is cached too: a misconfiguration (missing API key,
    absent corpus index, a model that won't load) would otherwise re-run the
    expensive factory — reloading the multi-GB embedder — on *every* request.
    The cached exception is re-raised cheaply instead. ``cache_clear()`` resets
    both success and failure so a fixed deployment can recover in-process (and
    so tests start clean).
    """
    lock = threading.Lock()
    holder: list[T] = []
    error_holder: list[Exception] = []

    def wrapper() -> T:
        if holder:
            return holder[0]
        if error_holder:
            raise error_holder[0]
        with lock:
            if holder:
                return holder[0]
            if error_holder:
                raise error_holder[0]
            try:
                holder.append(factory())
            except Exception as exc:
                error_holder.append(exc)
                raise
            return holder[0]

    def cache_clear() -> None:
        with lock:
            holder.clear()
            error_holder.clear()

    wrapper.cache_clear = cache_clear  # ty: ignore[missing-attribute]  # attribute stored on the function object, which type checkers do not model
    return wrapper


from asistente.ambitos.fiscal.calculo.calendario import Vencimiento, aviso_festivos, proximos_vencimientos
from asistente.ambitos.fiscal.calculo.optimizador import PlanOptimo, Restricciones, detalle_a_texto
from asistente.ambitos.fiscal.calculo.params import EjercicioNoDisponibleError, ejercicios_disponibles
from asistente.ambitos.fiscal.calculo.simulador import Simulacion, simular
from asistente.ambitos.fiscal.documentos import (
    AnalisisDocumento,
    DocumentoNoLegibleError,
    analizar_documento,
    decode_upload,
)
from asistente.ambitos.fiscal.profile import DETALLE_FACT_TYPES, DatosInvalidosError
from asistente.nucleo.agent.agent import AgentResult, TaxAgent
from asistente.nucleo.agent.providers.base import LLMProvider, ProviderError
from asistente.nucleo.cerebro.cerebro import Cerebro
from asistente.nucleo.cerebro.contrato import TurnoConversacion
from asistente.nucleo.decision.audit import build_recommendation_audit_event
from asistente.nucleo.decision.fact_extractor import (
    EXPECTED_FACT_TYPE,
    FactExtractor,
    OpenAIExtractionError,
    _coerce_value,
    default_fact_extractor,
)
from asistente.nucleo.decision.planner import Planner
from asistente.nucleo.decision.rules_engine import evaluate_rules
from asistente.nucleo.decision.schemas import (
    CaseState,
    Fact,
    MissingFact,
    ObligationCandidate,
    parse_fiscal_year,
)
from asistente.nucleo.decision.state_store import CaseVersionConflictError
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.decision.turn_service import process_turn
from asistente.nucleo.modulos.acciones import AccionPendiente, AccionStore
from asistente.nucleo.modulos.perfil import TIPO_DE_HECHO, HechoInvalidoError, hecho_de_modulo, normalizar_valor
from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso
from asistente.nucleo.proactive.bandeja import avisos_de_modulos, kind_de
from asistente.nucleo.proactive.jobs import ejecutar_bandejas_propias, hoy_en_espana
from asistente.nucleo.proactive.store import Notification, NotificationStore
from asistente.plataforma.api import sse
from asistente.plataforma.api.observability import ApiMetrics, observability_middleware
from asistente.plataforma.api.routers import auth as auth_router
from asistente.plataforma.api.routers import autonomos as autonomos_router
from asistente.plataforma.api.routers import capacidades as capacidades_router
from asistente.plataforma.api.routers import consejo as consejo_router
from asistente.plataforma.api.routers import consumo as consumo_router
from asistente.plataforma.api.routers import correo as correo_router
from asistente.plataforma.api.routers import evaluacion as evaluacion_router
from asistente.plataforma.api.routers import eventos as eventos_router
from asistente.plataforma.api.routers import investigacion as investigacion_router
from asistente.plataforma.api.routers import memoria as memoria_router
from asistente.plataforma.api.routers import modulos as modulos_router
from asistente.plataforma.api.routers import navegador as navegador_router
from asistente.plataforma.api.routers import planificador as planificador_router
from asistente.plataforma.api.routers import plataforma as plataforma_router
from asistente.plataforma.api.routers import push as push_router
from asistente.plataforma.api.routers import recuerdos as recuerdos_router
from asistente.plataforma.api.routers import repaso as repaso_router
from asistente.plataforma.api.routers import tareas as tareas_router
from asistente.plataforma.api.security import (
    BodySizeLimitMiddleware,
    RateLimiter,
    comprobar_arranque,
    comprobar_pregunta,
    hosts_permitidos,
    make_rate_limit_middleware,
    make_write_guard_middleware,
    recortar_historial,
    require_api_key,
    require_docs_publicas,
    require_operator,
    security_headers_middleware,
)
from asistente.plataforma.api.sse import respuesta_sse
from asistente.plataforma.auth.deps import (
    CaseAccess,
    get_case_access,
    require_case_access,
    require_document_consent,
)
from asistente.plataforma.ops.consumo import PresupuestoAgotadoError, TurnoCanceladoError, contexto_consumo, medir
from asistente.plataforma.settings import (
    AGENT_MAX_TURNOS_SIMULTANEOS,
    AGENT_STREAM_ESPERA_SEGUNDOS,
    API_RATE_LIMIT_PER_MIN,
    DECISION_STATE_DB_PATH,
)

_logger = logging.getLogger(__name__)

# The documentation routes are declared below, behind ``require_docs_publicas`` (docs/seguridad.md, S-18).


@asynccontextmanager
async def _arranque(_app: FastAPI) -> AsyncGenerator[None]:
    """Refuses to serve with no authentication at all (see ``comprobar_arranque``), and starts the conversation
    memory's Hindsight daemon in the background so the first question does not find it cold."""
    comprobar_arranque()
    from asistente.nucleo.recuerdos.servicio import calentar_memoria

    calentar_memoria()
    yield


app = FastAPI(
    title="HaciendaGPT Decision API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_arranque,
)


def _no_disponible(mensaje: str, exc: BaseException) -> HTTPException:
    """A 503 with a fixed message for the user. The exception's text (configuration, paths, a provider's answer,
    part of the model's output) goes to the log only (docs/seguridad.md, S-11)."""
    _logger.error("%s (%s: %s)", mensaje, type(exc).__name__, exc)
    return HTTPException(status_code=503, detail=mensaje)


# What the user reads when the model provider fails (the provider's own message goes to the log).
_PROVEEDOR_NO_RESPONDE = (
    "No se pudo procesar la consulta: el modelo no ha respondido. Inténtalo de nuevo en unos minutos."
)

_OPENAPI_URL = "/openapi.json"


@app.get(_OPENAPI_URL, include_in_schema=False, dependencies=[Depends(require_docs_publicas)])
def openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_docs_publicas)])
def swagger_ui(request: Request) -> HTMLResponse:
    raiz = str(request.scope.get("root_path", "")).rstrip("/")
    return get_swagger_ui_html(openapi_url=raiz + _OPENAPI_URL, title=f"{app.title} - Swagger UI")


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_docs_publicas)])
def redoc(request: Request) -> HTMLResponse:
    raiz = str(request.scope.get("root_path", "")).rstrip("/")
    return get_redoc_html(openapi_url=raiz + _OPENAPI_URL, title=f"{app.title} - ReDoc")


# CORS: in dev the Vite frontend runs at http://localhost:5173 and
# proxies /api/* to this server, which avoids preflight altogether. We
# still register CORSMiddleware so a non-proxied React build (e.g. a
# preview deploy on a different host) can hit the API directly. The
# allow-list is deliberately narrow — wildcard origins would be a footgun
# the day someone exposes the API publicly.
_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",  # vite preview
    "http://127.0.0.1:4173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Requested-With", "Last-Event-ID"],
    # Lets a cross-origin web client tell "session expired" from "API key missing".
    expose_headers=["WWW-Authenticate"],
    allow_credentials=False,
)

# Observability + rate limiting. Registration order matters: Starlette runs the
# LAST-registered middleware OUTERMOST, so register the limiter first and the
# observability layer second — the latter then times/logs/meters every request
# including the ones the limiter rejects with 429.
app.state.api_metrics = ApiMetrics()
_rate_limiter = RateLimiter(API_RATE_LIMIT_PER_MIN)
app.middleware("http")(make_rate_limit_middleware(_rate_limiter))
# Cross-site writes and non-JSON bodies are refused before any handler runs.
app.middleware("http")(make_write_guard_middleware(_ALLOWED_ORIGINS))
app.middleware("http")(observability_middleware)
# Security headers on every response, and the request-body cap outermost so an oversized body is refused
# before anything reads it (docs/seguridad.md).
app.middleware("http")(security_headers_middleware)
app.add_middleware(BodySizeLimitMiddleware)
# Outermost: a request whose Host is not ours (DNS rebinding against a local API) gets a 400 before anything.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts_permitidos())


@_thread_safe_singleton
def _build_case_store() -> SQLiteCaseStateStore:
    # Single configurable store (DECISION_STATE_DB_PATH).
    # Cached so the schema DDL and the SQLite connection are set up once,
    # not rebuilt on every request (mirrors _build_agent).
    return SQLiteCaseStateStore(DECISION_STATE_DB_PATH)


def get_case_store() -> SQLiteCaseStateStore:
    # Thin wrapper so tests can still override it via app.dependency_overrides.
    return _build_case_store()


@_thread_safe_singleton
def _build_notification_store() -> NotificationStore:
    # Same SQLite file as the cases, so one backup covers both.
    return NotificationStore(DECISION_STATE_DB_PATH)


def get_notification_store() -> NotificationStore:
    # Thin wrapper so tests can override it via app.dependency_overrides.
    return _build_notification_store()


_accion_stores: dict[str, AccionStore] = {}
_accion_stores_lock = threading.Lock()


def get_accion_store(cases: Annotated[SQLiteCaseStateStore, Depends(get_case_store)]) -> AccionStore:
    """Pending actions live in the cases' SQLite file (one backup and one erasure cover them), so they
    follow whichever case store is in use. Overridable via ``app.dependency_overrides``."""
    with _accion_stores_lock:
        store = _accion_stores.get(cases.db_path)
        if store is None:
            store = _accion_stores[cases.db_path] = AccionStore(cases.db_path)
        return store


@_thread_safe_singleton
def _build_fact_extractor() -> FactExtractor:
    # Built once and reused, mirroring _build_agent / _build_case_store. The
    # previous per-request construction spun up a fresh OpenAI() client on every
    # turn; the extractor's backend choice is fixed by the environment at
    # process start, so there is nothing to recompute per request.
    return default_fact_extractor()


def get_fact_extractor() -> FactExtractor:
    # Thin wrapper so tests can still override it via app.dependency_overrides.
    return _build_fact_extractor()


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    jurisdiction: str = Field(default="ES", min_length=2)
    tax_period: str = Field(min_length=4)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str = Field(min_length=1)
    # Prior conversation turns ({"role": "user"|"assistant", "content": str}).
    # Forwarded to the extractor so multi-turn references ("y para 2023…")
    # resolve against earlier turns. Defaults to empty for stateless callers.
    chat_history: list[dict[str, str]] = Field(default_factory=list)


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    facts: list[Fact]
    missing_facts: list[MissingFact]
    candidate_obligation_ids: list[str]
    # Full candidate objects (title/risk/confidence/evidence). The web UI
    # renders these directly; `candidate_obligation_ids` stays for
    # backward-compatible consumers that only need the ids.
    obligations: list[ObligationCandidate] = Field(default_factory=list)
    next_questions: list[str]
    degraded: bool = False
    degraded_facts: list[str] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "ts": datetime.now(UTC).isoformat()}


@app.get("/metrics", dependencies=[Depends(require_operator)])
def metrics() -> Response:
    """Prometheus-format HTTP metrics (request counts, errors, latency).

    Exempt from rate limiting; operator only (the API key, and the operator key with accounts on).
    """
    return Response(
        content=app.state.api_metrics.prometheus_text(),
        media_type="text/plain; version=0.0.4",
    )


@app.post("/cases", response_model=CaseState, dependencies=[Depends(require_api_key)])
def create_case(
    payload: CreateCaseRequest,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> CaseState:
    now = datetime.now(UTC)
    case = CaseState(
        case_id=f"case_{uuid4().hex}",
        user_id=access.owner_id(payload.user_id),
        jurisdiction=payload.jurisdiction,
        tax_period=payload.tax_period,
        created_at=now,
        updated_at=now,
    )
    store.save_case(case)
    access.claim(case.case_id)
    store.append_audit_event(case.case_id, {"event_type": "case_created"})
    return case


@app.get(
    "/cases/{case_id}", response_model=CaseState, dependencies=[Depends(require_api_key), Depends(require_case_access)]
)
def get_case(
    case_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
) -> CaseState:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return case


@app.get("/cases/{case_id}/audit", dependencies=[Depends(require_api_key), Depends(require_case_access)])
def get_case_audit(
    case_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return {"case_id": case_id, "events": store.list_audit_events(case_id)}


@app.post(
    "/cases/{case_id}/turn",
    response_model=TurnResponse,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def post_turn(
    case_id: str,
    payload: TurnRequest,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    extractor: Annotated[FactExtractor, Depends(get_fact_extractor)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> TurnResponse:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    comprobar_pregunta(payload.user_input)

    try:
        # Attributed to the user, so the per-user daily budget applies to the extractor's calls too.
        with contexto_consumo(user_id=_consumidor(access, case), case_id=case_id):
            outcome = process_turn(
                case=case,
                user_input=payload.user_input,
                extractor=extractor,
                chat_history=recortar_historial(payload.chat_history),
            )
    except OpenAIExtractionError as exc:
        if isinstance(exc.__cause__, PresupuestoAgotadoError):
            raise HTTPException(status_code=429, detail=str(exc.__cause__)) from exc
        # The fact extractor (LLM) failed after its own retries. Surface a clean
        # 503 instead of an opaque 500 — and never silently fall back to the
        # weaker regex extractor, which could persist wrong fiscal facts.
        raise _no_disponible(
            "No se pudieron extraer los hechos de la consulta. Inténtalo de nuevo en unos minutos.", exc
        ) from exc

    try:
        updated = store.save_case(outcome.case_state)
    except CaseVersionConflictError as exc:
        # Another turn updated this case between our read and write. Ask the
        # client to retry against the fresh state rather than clobber it.
        raise HTTPException(
            status_code=409,
            detail="El caso se modificó concurrentemente; vuelve a cargarlo y reintenta.",
        ) from exc

    # execute planner for side effect of validation and auditability
    Planner().plan(updated, outcome.rules_result.candidate_obligations)

    store.append_audit_event(case_id, {"event_type": "turn_processed", "input": payload.user_input})
    store.append_audit_event(
        case_id,
        build_recommendation_audit_event(
            case_state=updated,
            interpretation=outcome.interpretation,
            rules_result=outcome.rules_result,
            obligations=outcome.rules_result.candidate_obligations,
        ),
    )

    return TurnResponse(
        case_id=case_id,
        facts=updated.facts,
        missing_facts=updated.missing_facts,
        candidate_obligation_ids=[o.obligation_id for o in updated.obligation_candidates],
        obligations=list(updated.obligation_candidates),
        next_questions=[q.question_text for q in outcome.selected_questions],
        degraded=bool(updated.gave_up_facts),
        degraded_facts=list(updated.gave_up_facts),
    )


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    chat_history: list[dict] = Field(default_factory=list)
    # Optional: attach a case so the agent can read/update the user's fiscal
    # facts and run the rules engine. Without it the agent only searches.
    case_id: str | None = None
    # Optional: the client's id of this chat (one per conversation; a new chat, a new id). The household brain
    # retrieves the conversation memory with it and hands the finished turn to the memory.
    conversacion_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


# The agent behind /agent: the household brain (CEREBRO_MODO=hogar) or the single tax advisor (fiscal).
Agente = TaxAgent | Cerebro


class AgentResponse(AgentResult):
    case_id: str | None = None
    # True when a tool changed the case and the change was persisted.
    case_updated: bool = False
    # Candidate obligations on the attached case after this turn (empty without a case).
    obligations: list[ObligationCandidate] = Field(default_factory=list)
    # What the advisor knows about the user after this turn, so the UI can show
    # (and let the user correct) the facts the answer was based on.
    facts: list[Fact] = Field(default_factory=list)
    tax_period: str | None = None
    # Actions a module specialist prepared in this turn: they wait for the user's confirmation.
    pending_actions: list[AccionPendiente] = Field(default_factory=list)


@_thread_safe_singleton
def _build_agent() -> Agente:
    """Build the agent once (loads the embedder + the corpus index)."""
    from asistente.nucleo.agent.agent import build_agent

    return build_agent()


def get_agent() -> Agente:
    """FastAPI dependency returning the cached agent; a failed build is a 503.

    Kept as a thin wrapper so tests can override it via ``app.dependency_overrides``.
    """
    try:
        return _build_agent()
    except HTTPException:
        raise
    except Exception as exc:
        raise _no_disponible("El agente no está disponible.", exc) from exc


def _consumidor(access: CaseAccess, case: CaseState | None) -> str | None:
    """Who the model calls of a request are charged to (per-user budget): the session's user, else the case's."""
    if access.user is not None:
        return access.user.id
    return case.user_id if case is not None else None


def _load_agent_case(case_id: str | None, store: SQLiteCaseStateStore, access: CaseAccess) -> CaseState | None:
    if case_id is None:
        return None
    access.check(case_id)
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return case


def _modulos_del_turno(case_id: str | None, access: CaseAccess) -> dict[str, Any]:
    """``{"modulos": [...]}`` for ``agent.run`` when the user has a module on that the advisor consults through a
    specialist (``GanchosAgente.especialista``: every household module, not the tax one, whose tools are direct).

    Empty otherwise, so the agent is called exactly as before the modules existed.
    """
    from asistente.nucleo.modulos.ganchos import tiene_especialista

    if case_id is not None:
        modulos = modulos_activos_para_caso(case_id, access.store)
    else:
        from asistente.nucleo.modulos.preferencias import modulos_activos_para

        modulos = modulos_activos_para(access.user.id if access.user is not None else None, access.store)
    return {"modulos": modulos} if any(tiene_especialista(m) for m in modulos) else {}


def _del_cerebro(agent: Any, conversacion_id: str | None, user_id: str | None) -> dict[str, Any]:
    """``conversacion_id`` and ``user_id`` for the household brain's ``run`` (its memory is read for the same owner
    the turn is stored under); nothing for the plain advisor (whose ``run`` has no such arguments), so the legacy
    mode is called exactly as before."""
    if not getattr(agent, "ES_CEREBRO", False):
        return {}
    extra: dict[str, Any] = {}
    if conversacion_id:
        extra["conversacion_id"] = conversacion_id
    if user_id:
        extra["user_id"] = user_id
    return extra


def _usuario_del_turno(access: CaseAccess, case: CaseState | None) -> str | None:
    """Whose turn this is: the signed-in account, else the case's owner (the memory reads and stores for them)."""
    return access.user.id if access.user is not None else (case.user_id if case is not None else None)


def _registrar_en_memoria(
    agent: Any, payload: AgentRequest, case: CaseState | None, result: AgentResult, access: CaseAccess
) -> None:
    """Hand the finished turn to the conversation memory (``ProveedorMemoria.registrar_turno`` of the brain's memory
    service). Only brain turns with a ``conversacion_id``; never fails the request (the memory is optional)."""
    memoria = getattr(agent, "memoria", None) if getattr(agent, "ES_CEREBRO", False) else None
    if memoria is None or not payload.conversacion_id or result.cerebro is None:
        return
    try:
        turno = TurnoConversacion(
            conversacion_id=payload.conversacion_id,
            case_id=payload.case_id,
            user_id=_usuario_del_turno(access, case),
            fecha=datetime.now(UTC),
            pregunta=payload.query[:40_000],
            respuesta=result.answer[:60_000],
            especialistas=[i.especialista for i in result.cerebro.informes],
            modo=result.mode.value,
            recuerdos_usados=list(result.cerebro.recuerdos_usados),
        )
        memoria.registrar_turno(turno)
    except Exception:
        _logger.exception("conversation memory: the turn could not be registered")


# The audit event of a turn stays bounded: the answer's text and the brain's report are capped.
_MAX_RESPUESTA_AUDITADA = 20_000
_MAX_EN_INFORME: dict[str, int] = {"informes": 12, "cifras": 30, "fragment_ids": 30, "peticiones": 8}
_MAX_EN_PUERTA: dict[str, int] = {"violaciones": 20, "atribuciones": 40, "ignorados": 20}


def _informe_cerebro_acotado(result: AgentResult) -> dict[str, Any] | None:
    """The brain's full report for the audit trail (the evaluation reads it), with its lists capped: still a valid
    ``InformeCerebro``, never an unbounded event."""
    if result.cerebro is None:
        return None
    datos = result.cerebro.model_dump(mode="json")
    datos["informes"] = datos["informes"][: _MAX_EN_INFORME["informes"]]
    for informe in datos["informes"]:
        informe["cifras"] = informe["cifras"][: _MAX_EN_INFORME["cifras"]]
        informe["fragment_ids"] = informe["fragment_ids"][: _MAX_EN_INFORME["fragment_ids"]]
    datos["peticiones"] = datos["peticiones"][: _MAX_EN_INFORME["peticiones"]]
    if isinstance(datos.get("puerta"), dict):
        for clave, tope in _MAX_EN_PUERTA.items():
            datos["puerta"][clave] = datos["puerta"][clave][:tope]
    return datos


def _modelos_del_turno(agent: Any, result: AgentResult) -> dict[str, str]:
    """``{purpose: model}`` for the purposes the turn used: the brain says it; for the plain advisor, its own
    provider and verifier. Empty when it cannot be read (never fails the turn)."""
    try:
        modelos_de = getattr(agent, "modelos_del_turno", None)
        if callable(modelos_de):
            return dict(modelos_de(result))
        modelos = {"agente": result.model}
        verificador = getattr(getattr(getattr(agent, "_verifier", None), "_provider", None), "model", None)
        if isinstance(verificador, str) and verificador:
            modelos["verificador"] = verificador
        return modelos
    except Exception:
        _logger.exception("could not read the turn's models for the audit trail")
        return {}


def _variante_en_uso() -> str | None:
    """The prompt/model variant that answered (``evaluacion.variantes``), or None if it cannot be read."""
    try:
        from asistente.nucleo.evaluacion.variantes import variante_en_uso

        return str(variante_en_uso().id)
    except Exception:
        _logger.exception("could not read the prompt variant in use for the audit trail")
        return None


def _guardar_acciones(result: AgentResult, acciones: AccionStore) -> list[AccionPendiente]:
    """Store the actions the specialists proposed (once each: a repeated identical call is one action)."""
    vistas: set[str] = set()
    guardadas: list[AccionPendiente] = []
    for accion in result.acciones_propuestas:
        firma = json.dumps([accion.case_id, accion.herramienta, accion.argumentos], sort_keys=True, default=str)
        if firma in vistas:
            continue
        vistas.add(firma)
        guardadas.append(acciones.add(accion))
    return guardadas


def _finish_agent_turn(
    payload: AgentRequest,
    case: CaseState | None,
    result: AgentResult,
    store: SQLiteCaseStateStore,
    acciones: AccionStore | None = None,
    agent: Any = None,
) -> AgentResponse:
    """Persist what the agent changed on the case, audit the turn, build the response.

    The audit event holds the conversation's record for this case (the question, the answer, the tool trace, the
    verified claims), under the case's rules: exported with the account, erased with the case or the account. With
    the brain it also carries its full report (capped), the prompt variant in use and the model of each purpose,
    which the evaluation reads when the user says an answer was wrong."""
    case_updated = False
    pendientes: list[AccionPendiente] = []
    if payload.case_id is not None:
        if result.updated_case is not None:
            try:
                store.save_case(result.updated_case)
            except CaseVersionConflictError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="El caso se modificó concurrentemente; vuelve a cargarlo y reintenta.",
                ) from exc
            case_updated = True
        if result.acciones_propuestas and acciones is not None:
            pendientes = _guardar_acciones(result, acciones)
        event: dict[str, Any] = {
            "event_type": "agent_answer",
            "input": payload.query,
            "mode": result.mode.value,
            "provider": result.provider,
            "model": result.model,
            "tool_trace": [entry.model_dump(mode="json") for entry in result.tool_trace],
            "citations": [{"fragment_id": c.fragment_id, "locator": c.locator} for c in result.citations],
            "claims": [claim.model_dump(mode="json") for claim in result.claims],
            "warnings": result.warnings,
        }
        if pendientes:
            event["pending_actions"] = [
                {"id": a.id, "modulo": a.modulo, "herramienta": a.herramienta} for a in pendientes
            ]
        # The answer's text, next to the question (``input``) it answers: the record of the conversation.
        event["answer"] = result.answer[:_MAX_RESPUESTA_AUDITADA]
        variante = _variante_en_uso()
        if variante is not None:
            event["variante"] = variante
        modelos = _modelos_del_turno(agent, result)
        if modelos:
            event["modelos"] = modelos
        informe_cerebro = _informe_cerebro_acotado(result)
        if informe_cerebro is not None:
            event["informe_cerebro"] = informe_cerebro
        if result.cerebro is not None:
            # What the household brain did: its plan, who it consulted and what its gates said.
            event["cerebro"] = {
                "ruta": result.cerebro.ruta,
                "tipo": result.cerebro.intencion.tipo,
                "origen": result.cerebro.intencion.origen,
                "especialistas": [i.especialista for i in result.cerebro.informes],
                "revisiones_sintesis": result.cerebro.revisiones_sintesis,
                "violaciones_cifras": len(result.cerebro.puerta.violaciones) if result.cerebro.puerta else 0,
            }
        store.append_audit_event(payload.case_id, event)

    final_case = result.updated_case or case
    return AgentResponse(
        **result.model_dump(),
        case_id=payload.case_id,
        case_updated=case_updated,
        obligations=list(final_case.obligation_candidates) if final_case is not None else [],
        facts=list(final_case.facts) if final_case is not None else [],
        tax_period=final_case.tax_period if final_case is not None else None,
        pending_actions=pendientes,
    )


@app.post("/agent", response_model=AgentResponse, dependencies=[Depends(require_api_key)])
def post_agent(
    payload: AgentRequest,
    agent: Annotated[Agente, Depends(get_agent)],
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
    acciones: Annotated[AccionStore, Depends(get_accion_store)],
    tareas_de_fondo: BackgroundTasks,
) -> AgentResponse:
    """Tool-using advisor: searches the corpus, cites fragments, verifies claims.

    The model decides what to
    look up and how often, and every normative claim in the answer is checked
    against the fragment it cites before the response is labelled ``verified``.
    """
    case = _load_agent_case(payload.case_id, store, access)
    comprobar_pregunta(payload.query)
    try:
        with contexto_consumo(
            user_id=access.user.id if access.user is not None else (case.user_id if case is not None else None),
            case_id=payload.case_id,
        ):
            result = agent.run(
                payload.query,
                chat_history=recortar_historial(payload.chat_history),
                case=case,
                **_modulos_del_turno(payload.case_id, access),
                **_del_cerebro(agent, payload.conversacion_id, _usuario_del_turno(access, case)),
            )
    except PresupuestoAgotadoError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ProviderError as exc:
        raise _no_disponible(_PROVEEDOR_NO_RESPONDE, exc) from exc
    response = _finish_agent_turn(payload, case, result, store, acciones, agent)
    # After the response is sent: the memory may summarise, the user does not wait for it.
    tareas_de_fondo.add_task(_registrar_en_memoria, agent, payload, case, result, access)
    return response


# Seconds between keep-alive comments while the model is working, so proxies and the browser's idle timeout do
# not cut a long (but healthy) run. The client's disconnection is checked more often than that.
_STREAM_HEARTBEAT_SECONDS = 10.0
_STREAM_DISCONNECT_CHECK_SECONDS = 1.0
_STREAM_QUEUE_CHECK_SECONDS = 0.25
_TURNOS_COMPLETOS = "Hay demasiadas consultas en curso en este momento. Inténtalo de nuevo dentro de un minuto."


class _LimiteTurnos:
    """How many agent turns this process streams at once (``AGENT_MAX_TURNOS_SIMULTANEOS``), all users together.

    Not an asyncio primitive: the slot is taken on the event loop and given back by the turn's own thread when it
    really ends, which can be after its client left (the turn then stops at its next model call)."""

    def __init__(self, maximo: int) -> None:
        self.maximo = maximo
        self.en_curso = 0
        self._lock = threading.Lock()

    def tomar(self) -> bool:
        with self._lock:
            if self.en_curso >= self.maximo:
                return False
            self.en_curso += 1
            return True

    def soltar(self) -> None:
        with self._lock:
            self.en_curso = max(0, self.en_curso - 1)


_limite_turnos = _LimiteTurnos(AGENT_MAX_TURNOS_SIMULTANEOS)


@app.post("/agent/stream", dependencies=[Depends(require_api_key)])
async def post_agent_stream(
    payload: AgentRequest,
    request: Request,
    agent: Annotated[Agente, Depends(get_agent)],
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
    acciones: Annotated[AccionStore, Depends(get_accion_store)],
) -> StreamingResponse:
    """Same as ``/agent``, streamed as Server-Sent Events (``text/event-stream``) so the UI shows live progress.

    Named events, each with an increasing ``id``: ``progress`` (``data``: the agent's event — searches issued,
    verification, revision, text deltas) while it works; ``cola`` once if the turn waits for a free slot (the
    process runs at most ``AGENT_MAX_TURNOS_SIMULTANEOS`` turns at once); ``: ping`` comments as keep-alives; then
    exactly one terminal event — ``result`` (``data``: the ``AgentResponse``) or ``error`` (``data``:
    ``{"status", "detail"}``). Request-level failures that happen before the run starts (404 case, 503 agent
    unavailable, 401) are still plain HTTP errors.

    The turn runs in its own thread, not on the event loop nor in the shared worker pool. When the client goes away
    the turn's cancellation token is set: the agent stops before its next model call, so an answer nobody will read
    stops spending.
    """
    case = await run_in_threadpool(_load_agent_case, payload.case_id, store, access)
    comprobar_pregunta(payload.query)
    historial = recortar_historial(payload.chat_history)
    modulos = await run_in_threadpool(_modulos_del_turno, payload.case_id, access)
    del_cerebro = _del_cerebro(agent, payload.conversacion_id, _usuario_del_turno(access, case))

    def turno(publicar: Callable[[dict[str, Any]], None], cancelacion: threading.Event) -> None:
        def progreso(event: dict[str, Any]) -> None:
            # Also checked here: a provider that is not metered (tests, a local model) stops at its next event.
            if cancelacion.is_set():
                raise TurnoCanceladoError("el cliente se ha desconectado")
            publicar({"event": "progress", "data": event})

        try:
            with contexto_consumo(user_id=_consumidor(access, case), case_id=payload.case_id, cancelacion=cancelacion):
                result = agent.run(
                    payload.query,
                    chat_history=historial,
                    case=case,
                    on_event=progreso,
                    **modulos,
                    **del_cerebro,
                )
            response = _finish_agent_turn(payload, case, result, store, acciones, agent)
            publicar({"event": "result", "data": response.model_dump(mode="json")})
            # The user already has the answer: the memory may take its time.
            _registrar_en_memoria(agent, payload, case, result, access)
        except TurnoCanceladoError:
            _logger.info("agent turn cancelled: the client went away")
        except PresupuestoAgotadoError as exc:
            publicar({"event": "error", "data": {"status": 429, "detail": str(exc)}})
        except ProviderError as exc:
            fallo = _no_disponible(_PROVEEDOR_NO_RESPONDE, exc)
            publicar({"event": "error", "data": {"status": 503, "detail": fallo.detail}})
        except HTTPException as exc:
            publicar({"event": "error", "data": {"status": exc.status_code, "detail": str(exc.detail)}})
        except Exception:  # the stream must always end with a terminal event
            # The exception text can carry paths, SQL or data of the case: it goes to the log, not to the client.
            _logger.exception("agent stream failed")
            publicar({"event": "error", "data": {"status": 500, "detail": "Error interno."}})

    return respuesta_sse(_eventos_del_turno(request, turno, _limite_turnos))


async def _eventos_del_turno(
    request: Request,
    turno: Callable[[Callable[[dict[str, Any]], None], threading.Event], None],
    limite: _LimiteTurnos,
) -> AsyncIterator[str]:
    """The SSE frames of one agent turn: wait for a slot, run ``turno`` in a thread, relay what it publishes.

    Whatever way the stream ends before a terminal event (the client disconnected, the server cancelled the
    response), the ``finally`` sets the turn's cancellation token.
    """
    loop = asyncio.get_running_loop()
    cola: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    cancelacion = threading.Event()
    secuencia = 0
    terminado = False
    slot_propio = False

    def frame(nombre: str, datos: Any) -> str:
        nonlocal secuencia
        secuencia += 1
        return sse.evento(nombre, datos, id=str(secuencia))

    def publicar(event: dict[str, Any]) -> None:  # from the turn's thread
        try:
            loop.call_soon_threadsafe(cola.put_nowait, event)
        except RuntimeError:  # the loop is gone: nobody is listening any more
            cancelacion.set()

    def en_hilo() -> None:
        try:
            turno(publicar, cancelacion)
        finally:
            limite.soltar()

    try:
        limite_espera = loop.time() + AGENT_STREAM_ESPERA_SEGUNDOS
        en_cola = False
        while not limite.tomar():
            if await request.is_disconnected():
                return
            if loop.time() >= limite_espera:
                terminado = True
                yield frame("error", {"status": 503, "detail": _TURNOS_COMPLETOS})
                return
            if not en_cola:
                en_cola = True
                yield frame("cola", {"maximo": limite.maximo})
            await asyncio.sleep(_STREAM_QUEUE_CHECK_SECONDS)
        slot_propio = True
        threading.Thread(target=en_hilo, name="agent-stream", daemon=True).start()
        slot_propio = False  # the thread gives it back when the turn really ends

        ultimo_envio = ultima_comprobacion = loop.time()
        while True:
            try:
                event = await asyncio.wait_for(cola.get(), timeout=_STREAM_DISCONNECT_CHECK_SECONDS)
            except TimeoutError:
                event = None
            # Checked on a clock, not only when the turn is quiet: a server on ASGI spec 2.4 does not cancel the
            # response when the client leaves, and a turn streaming text never goes quiet.
            if loop.time() - ultima_comprobacion >= _STREAM_DISCONNECT_CHECK_SECONDS:
                ultima_comprobacion = loop.time()
                if await request.is_disconnected():
                    return
            if event is None:
                if loop.time() - ultimo_envio >= _STREAM_HEARTBEAT_SECONDS:
                    ultimo_envio = loop.time()
                    yield sse.comentario()
                continue
            terminal = event["event"] in ("result", "error")
            terminado = terminado or terminal
            ultimo_envio = loop.time()
            yield frame(event["event"], event["data"])
            if terminal:
                return
    finally:
        if slot_propio:
            limite.soltar()
        if not terminado:
            cancelacion.set()


def _save_case_after_user_edit(
    case: CaseState, facts: list[Fact], store: SQLiteCaseStateStore, audit_event: dict, tax_period: str | None = None
) -> CaseState:
    """Persist a user-driven change to the case facts and re-evaluate obligations."""
    known = {fact.name for fact in facts}
    edited = case.model_copy(
        update={
            "facts": facts,
            "tax_period": tax_period or case.tax_period,
            # CaseState forbids a fact being both known and missing.
            "missing_facts": [m for m in case.missing_facts if m.fact_name not in known],
            "updated_at": datetime.now(UTC),
        }
    )
    rules_result = evaluate_rules(case_state=edited, recent_facts=[])
    edited = edited.model_copy(update={"obligation_candidates": rules_result.candidate_obligations})
    try:
        updated = store.save_case(edited)
    except CaseVersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="El caso se modificó concurrentemente; vuelve a cargarlo y reintenta.",
        ) from exc
    store.append_audit_event(case.case_id, audit_event)
    return updated


class FactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | float | bool


@app.put(
    "/cases/{case_id}/facts/{fact_name}",
    response_model=CaseState,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def put_case_fact(
    case_id: str,
    fact_name: str,
    payload: FactUpdateRequest,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> CaseState:
    """Set one fact of a case to a value the user typed — correcting what the
    advisor recorded, or filling it in directly. The value is coerced to the
    fact's type exactly like an extracted one; obligations are re-evaluated."""
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    expected = EXPECTED_FACT_TYPE.get(fact_name)
    if expected is None:
        return _put_module_fact(case, fact_name, payload.value, store, access)

    raw = payload.value
    value = _coerce_value(
        expected=expected,
        value_string=raw if isinstance(raw, str) else None,
        value_number=raw if isinstance(raw, int | float) and not isinstance(raw, bool) else None,
        value_boolean=raw if isinstance(raw, bool) else None,
    )
    if value is None:
        raise HTTPException(
            status_code=422, detail=f"El valor no es válido para este dato (se espera {expected.value})."
        )
    if fact_name in DETALLE_FACT_TYPES:
        # The structured facts (the children's detail, the spouse's figures, the optimiser's
        # preferences) are JSON text: stored only when they validate, in their canonical form.
        try:
            value = detalle_a_texto(fact_name, value)
        except DatosInvalidosError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    tax_period: str | None = None
    if fact_name == "periodo_fiscal":
        year = parse_fiscal_year(str(value))
        if year is None or not 1990 <= year <= datetime.now(UTC).year + 1:
            raise HTTPException(status_code=422, detail="El ejercicio debe ser un año de cuatro cifras.")
        value = tax_period = str(year)

    fact = Fact(
        fact_id=f"fact_{fact_name}",
        name=fact_name,
        value=value,
        value_type=expected,
        source="user_edit",
        confidence=1.0,
    )
    facts = [*(existing for existing in case.facts if existing.name != fact_name), fact]
    return _save_case_after_user_edit(
        case,
        facts,
        store,
        {"event_type": "fact_edited_by_user", "fact_name": fact_name, "value": value},
        tax_period=tax_period,
    )


def _put_module_fact(
    case: CaseState, fact_name: str, raw: str | float | bool, store: SQLiteCaseStateStore, access: CaseAccess
) -> CaseState:
    """A household module's fact: validated by the module (``validar``), JSON facts stored canonical.

    Only facts of a module the owner of the case has switched on can be written.
    """
    propio = hecho_de_modulo(fact_name)
    if propio is None:
        raise HTTPException(status_code=404, detail="fact not found")
    modulo, hecho = propio
    if modulo.clave not in {m.clave for m in modulos_activos_para_caso(case.case_id, access.store)}:
        raise HTTPException(
            status_code=409, detail=f"El módulo «{modulo.nombre}» no está activado: actívalo para guardar sus datos."
        )
    try:
        value = normalizar_valor(hecho, raw)
    except HechoInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fact = Fact(
        fact_id=f"fact_{fact_name}",
        name=fact_name,
        value=value,
        value_type=TIPO_DE_HECHO[hecho.tipo],
        source="user_edit",
        confidence=1.0,
    )
    facts = [*(existing for existing in case.facts if existing.name != fact_name), fact]
    return _save_case_after_user_edit(
        case,
        facts,
        store,
        {"event_type": "fact_edited_by_user", "fact_name": fact_name, "value": value, "modulo": modulo.clave},
    )


@app.delete(
    "/cases/{case_id}/facts/{fact_name}",
    response_model=CaseState,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def delete_case_fact(
    case_id: str,
    fact_name: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    notifications: Annotated[NotificationStore, Depends(get_notification_store)],
) -> CaseState:
    """Remove one fact from a case. Obligations are re-evaluated without it.

    The value also leaves the case's audit trail (every structured place it was written to: edits, the
    facts a recommendation rested on, the advisor's tool calls) and the year-end plan built from it is
    dropped (the next run rebuilds it from what is left). The removal itself is recorded, without the value.
    """
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    remaining = [fact for fact in case.facts if fact.name != fact_name]
    if len(remaining) == len(case.facts):
        raise HTTPException(status_code=404, detail="fact not found")
    updated = _save_case_after_user_edit(
        case, remaining, store, {"event_type": "fact_removed_by_user", "fact_name": fact_name}
    )
    anonimizados = store.redact_fact_in_audit(case_id, fact_name)
    planes = notifications.remove_superseded(case_id, key_prefix="fin_de_ejercicio:", keep_key="")
    erased: dict[str, object] = {
        "event_type": "fact_value_erased",
        "fact_name": fact_name,
        "audit_events_redacted": anonimizados,
        "year_end_plans_removed": planes,
    }
    propio = hecho_de_modulo(fact_name)
    if propio is not None:
        # The module's notices may quote the value: they go too (the next run rebuilds them from what is left).
        erased["module_notices_removed"] = notifications.remove_kind(case_id, kind_de(propio[0].clave))
    store.append_audit_event(case_id, erased)
    return updated


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # With a case, its facts are the base; `datos` adds or overrides figures.
    case_id: str | None = None
    ejercicio: int | None = None
    datos: dict = Field(default_factory=dict)
    escenarios: list[dict] | None = None
    # Evaluate the benefit catalogue on the last scenario instead of the base
    # one (what an interactive simulator shows while the user edits figures).
    beneficios_sobre_ultimo: bool = False


@app.post("/simulate", response_model=Simulacion, dependencies=[Depends(require_api_key)])
def post_simulate(
    payload: SimulateRequest,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> Simulacion:
    """Deterministic IRPF simulation — no LLM involved, so it is instant and free.

    Returns the base scenario, each alternative with its saving versus the
    base, and the tax-benefit catalogue checked against the profile.
    """
    case = _load_agent_case(payload.case_id, store, access)
    facts = {fact.name: fact.value for fact in case.facts} if case is not None else {}
    year = payload.ejercicio
    if year is None:
        year = (parse_fiscal_year(case.tax_period) if case is not None else None) or max(ejercicios_disponibles())
    try:
        return simular(
            ejercicio=year,
            facts=facts,
            datos=payload.datos,
            escenarios=payload.escenarios,
            beneficios_sobre_ultimo=payload.beneficios_sobre_ultimo,
        )
    except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # With a case, its facts are the user's figures; `datos` adds or overrides some (like /simulate).
    case_id: str | None = None
    ejercicio: int | None = None
    datos: dict = Field(default_factory=dict)
    # The spouse's figures, {field: value} with the calculator's names. Without them joint filing is not compared.
    # With a case, the figures it keeps (``datos_conyuge``) are the default of a married user: these replace them
    # field by field (null removes one), and an explicit ``"conyuge": null`` leaves the spouse out of this run.
    conyuge: dict | None = None
    # With a case, its stored preferences (``preferencias_optimizacion``) fill every field not sent here.
    restricciones: Restricciones = Field(default_factory=Restricciones)
    # The day the plan is made for (default: today in Spain). After 31 December of the fiscal year only the
    # filing choices remain.
    fecha: date | None = None


# Body-validation errors of this route come back as one Spanish sentence (``detail`` is a string, like /simulate).
_optimizador = APIRouter(route_class=autonomos_router._RutaConErroresEnEspanol)


@_optimizador.post("/optimize", response_model=PlanOptimo, dependencies=[Depends(require_api_key)])
def post_optimize(
    payload: OptimizeRequest,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> PlanOptimo:
    """Deterministic tax optimiser — no LLM: the best combination of the household's levers.

    Returns the current and optimal results with their traces, every lever of the plan with its
    marginal and standalone saving, what could not be used and why, citations and a Spanish
    explanation. Same ownership rules as /simulate; 422 with a readable message on bad data.
    """
    case = _load_agent_case(payload.case_id, store, access)
    facts = {fact.name: fact.value for fact in case.facts} if case is not None else {}
    year = payload.ejercicio
    if year is None:
        year = (parse_fiscal_year(case.tax_period) if case is not None else None) or max(ejercicios_disponibles())
    try:
        # With a case, the user's rejections and restrictions in force are constraints (asistente/nucleo/memoria).
        from asistente.nucleo.memoria.optimizador import optimizar_con_memoria

        return optimizar_con_memoria(
            case_id=case.case_id if case is not None else None,
            db_path=store.db_path,
            ejercicio=year,
            fecha=payload.fecha or hoy_en_espana(),
            facts=facts,
            datos=payload.datos,
            conyuge=payload.conyuge,
            restricciones=payload.restricciones,
            sin_conyuge="conyuge" in payload.model_fields_set and payload.conyuge is None,
        )
    except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


app.include_router(_optimizador)


class CalendarResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    hoy: date
    vencimientos: list[Vencimiento]
    aviso: str


@app.get(
    "/cases/{case_id}/calendar",
    response_model=CalendarResponse,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def get_case_calendar(
    case_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    dias: int = 365,
) -> CalendarResponse:
    """Upcoming filing deadlines that apply to this case's profile."""
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    today = datetime.now(UTC).date()
    facts = {fact.name: fact.value for fact in case.facts}
    return CalendarResponse(
        case_id=case_id,
        hoy=today,
        vencimientos=proximos_vencimientos(facts, hoy=today, dias=max(1, min(730, dias))),
        aviso=aviso_festivos(),
    )


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=200)
    # Base64 of the file. JSON instead of multipart keeps the API dependency-free.
    content_base64: str = Field(min_length=1)


@_thread_safe_singleton
def _build_document_provider() -> LLMProvider:
    from asistente.nucleo.agent.providers import create_provider
    from asistente.plataforma import settings

    # Locating labelled figures in a document is a constrained task: low effort.
    return medir(create_provider(effort=settings.DECISION_EXTRACTOR_EFFORT), "documentos")


def get_document_provider() -> LLMProvider:
    """FastAPI dependency (overridable in tests); a failed build is a 503."""
    try:
        return _build_document_provider()
    except Exception as exc:
        raise _no_disponible("El lector de documentos no está disponible.", exc) from exc


@app.post(
    "/cases/{case_id}/documents",
    response_model=AnalisisDocumento,
    dependencies=[Depends(require_api_key), Depends(require_document_consent)],
)
def post_case_document(
    case_id: str,
    payload: DocumentRequest,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    provider: Annotated[LLMProvider, Depends(get_document_provider)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> AnalisisDocumento:
    """Read one of the user's tax documents and PROPOSE profile facts from it.

    Nothing is written to the case: the client shows the proposals and applies
    the ones the user accepts through ``PUT /cases/{id}/facts/{name}``. The
    document is processed in memory and not stored; its text is sent to the
    configured LLM provider (the file itself when it is a scan or a photo,
    which is then read twice). Only the file name and the proposed fact names are
    audited.
    """
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        data = decode_upload(payload.content_base64)
        # Attributed to the user: without it the per-user daily budget never applied to documents.
        with contexto_consumo(user_id=_consumidor(access, case), case_id=case_id):
            analysis = analizar_documento(provider, filename=payload.filename, data=data)
    except DocumentoNoLegibleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PresupuestoAgotadoError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ProviderError as exc:
        raise _no_disponible("No se pudo analizar el documento. Inténtalo de nuevo en unos minutos.", exc) from exc
    store.append_audit_event(
        case_id,
        {
            "event_type": "document_analyzed",
            "filename": payload.filename,
            "document_type": analysis.tipo_documento,
            "proposed_facts": [fact.name for fact in analysis.hechos_propuestos],
        },
    )
    return analysis


class NotificationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    unread: int
    notifications: list[Notification]


class ProactiveRunResponse(NotificationsResponse):
    # Notifications created by this run, per kind.
    created: dict[str, int]


@app.get(
    "/cases/{case_id}/notifications",
    response_model=NotificationsResponse,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def get_case_notifications(
    case_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    notifications: Annotated[NotificationStore, Depends(get_notification_store)],
    unread_only: bool = False,
) -> NotificationsResponse:
    """Notifications the proactive jobs wrote for this case, newest first."""
    if store.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    return NotificationsResponse(
        case_id=case_id,
        unread=notifications.count_unread(case_id),
        notifications=notifications.list(case_id, unread_only=unread_only),
    )


@app.post(
    "/cases/{case_id}/notifications/{notification_id}/read",
    response_model=NotificationsResponse,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def post_notification_read(
    case_id: str,
    notification_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    notifications: Annotated[NotificationStore, Depends(get_notification_store)],
) -> NotificationsResponse:
    if store.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    if not notifications.mark_read(case_id, notification_id):
        raise HTTPException(status_code=404, detail="notification not found")
    return NotificationsResponse(
        case_id=case_id, unread=notifications.count_unread(case_id), notifications=notifications.list(case_id)
    )


@app.post(
    "/cases/{case_id}/proactive/run",
    response_model=ProactiveRunResponse,
    dependencies=[Depends(require_api_key), Depends(require_case_access)],
)
def post_case_proactive_run(
    case_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
    notifications: Annotated[NotificationStore, Depends(get_notification_store)],
    access: Annotated[CaseAccess, Depends(get_case_access)],
) -> ProactiveRunResponse:
    """Run the deterministic proactive jobs (deadlines + year-end plan) for this case now. No LLM."""
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    hoy = hoy_en_espana()
    # The modules that write their own notifications (the tax jobs), through the module contract.
    created = ejecutar_bandejas_propias(case, hoy=hoy, store=notifications)
    from asistente.nucleo.modulos.ganchos import escribe_su_bandeja

    otros = [m for m in modulos_activos_para_caso(case_id, access.store) if not escribe_su_bandeja(m)]
    if otros:
        # The household modules the owner switched on: their notices land in the same inbox.
        por_modulo, _fallidos = avisos_de_modulos(case, hoy=hoy, store=notifications, modulos=otros)
        created.update({kind_de(clave): n for clave, n in por_modulo.items()})
    return ProactiveRunResponse(
        case_id=case_id,
        unread=notifications.count_unread(case_id),
        notifications=notifications.list(case_id),
        created=created,
    )


@app.get("/cases/{case_id}/audit/export", dependencies=[Depends(require_api_key), Depends(require_case_access)])
def export_case_audit(
    case_id: str,
    store: Annotated[SQLiteCaseStateStore, Depends(get_case_store)],
) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    events = store.list_audit_events(case_id)
    return {"case_id": case_id, "exported_at": datetime.now(UTC).isoformat(), "events": events}


# --- routers ---
# Routers live in asistente/plataforma/api/routers/. Include them here, after every
# store provider above is defined, and pass those providers in (instead of
# importing them from this module in the router) so there is no circular import
# and ``app.dependency_overrides`` keeps working inside the router.
app.include_router(
    auth_router.build_router(
        get_case_store=get_case_store,
        get_notification_store=get_notification_store,
        get_accion_store=get_accion_store,
    )
)
app.include_router(
    modulos_router.build_router(
        get_case_store=get_case_store,
        get_notification_store=get_notification_store,
        get_accion_store=get_accion_store,
        get_document_provider=get_document_provider,
    )
)
app.include_router(autonomos_router.router)
app.include_router(consumo_router.router)
app.include_router(
    memoria_router.build_router(
        get_case_store=get_case_store,
        get_notification_store=get_notification_store,
        get_accion_store=get_accion_store,
    )
)
app.include_router(eventos_router.build_router(get_case_store=get_case_store))
app.include_router(navegador_router.build_router(get_case_store=get_case_store, get_accion_store=get_accion_store))
app.include_router(evaluacion_router.build_router(get_case_store=get_case_store))
app.include_router(planificador_router.build_router(get_case_store=get_case_store))
app.include_router(correo_router.build_router(get_case_store=get_case_store))
app.include_router(push_router.build_router(get_case_store=get_case_store))
app.include_router(plataforma_router.build_router(get_case_store=get_case_store))
app.include_router(tareas_router.build_router(get_case_store=get_case_store))
app.include_router(recuerdos_router.build_router(get_case_store=get_case_store))
app.include_router(repaso_router.build_router(get_case_store=get_case_store))
app.include_router(consejo_router.build_router(get_case_store=get_case_store))
app.include_router(capacidades_router.build_router())
app.include_router(investigacion_router.router)
# --- /routers ---

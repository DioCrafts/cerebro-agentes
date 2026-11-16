"""Accounts, sessions and the user's privacy controls (export, erasure, consents).

Built by :func:`build_router` instead of importing the stores from ``api.py``:
the main module passes its own (test-overridable) store providers in, which
avoids a circular import and keeps ``app.dependency_overrides`` working here.

With ``AUTH_MODE=off`` only ``GET /auth/config`` answers; the rest return 404 so
nobody creates an account that would protect nothing.

The module deliberately has no ``from __future__ import annotations``: the
endpoints are closures whose dependency aliases are local names, and FastAPI
could not resolve them from strings.
"""

from collections.abc import Callable
from datetime import UTC, datetime
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from asistente.capacidades.correo import almacen as correo_almacen
from asistente.capacidades.correo import oauth as correo_oauth
from asistente.capacidades.investigacion import capacidad as investigacion_capacidad
from asistente.capacidades.navegador import tareas as tareas_navegador
from asistente.capacidades.navegador import vigilancia
from asistente.capacidades.navegador.persistencia import exportar as exportar_navegador
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.memoria.almacen import exportar_decisiones
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.nucleo.tareas.almacen import exportar_tareas
from asistente.plataforma import settings
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import auth_required, bearer_token, get_auth_store, require_user
from asistente.plataforma.auth.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, PasswordPolicyError
from asistente.plataforma.auth.ratelimit import LoginRateLimiter
from asistente.plataforma.auth.store import (
    CONSENT_TEXT_VERSION,
    CONSENT_TYPES,
    AuthStore,
    ConsentRecord,
    ConsentType,
    EmailAlreadyRegisteredError,
    InvalidEmailError,
    User,
)
from asistente.plataforma.ops.consumo import anonimizar_consumo
from asistente.plataforma.push.almacen import PushStore

_limiter_lock = threading.Lock()
_limiters: dict[tuple[int, int], LoginRateLimiter] = {}


def get_login_limiter() -> LoginRateLimiter:
    """Process-wide limiter for the configured budget (overridable in tests)."""
    key = (settings.AUTH_LOGIN_MAX_ATTEMPTS, settings.AUTH_LOGIN_WINDOW_SECONDS)
    with _limiter_lock:
        limiter = _limiters.get(key)
        if limiter is None:
            limiter = _limiters[key] = LoginRateLimiter(*key)
        return limiter


def require_auth_enabled() -> None:
    if not auth_required():
        raise HTTPException(status_code=404, detail="Las cuentas de usuario no están activadas en este servicio.")


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_mode: str
    password_min_length: int
    consent_text_version: str
    # Empty when the operator has not published a privacy notice yet.
    privacy_notice_url: str


class ConsentChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tratamiento_datos: bool = False
    envio_documentos_a_proveedor_llm: bool = False
    avisos_por_correo: bool = False


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    consents: ConsentChoices


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Shown once: the server keeps only its SHA-256.
    token: str
    expires_at: datetime
    user: User


class ConsentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ConsentType
    granted: bool
    # None when the user has never decided on this consent.
    timestamp: datetime | None
    text_version: str | None


class ConsentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_version: str
    consents: list[ConsentState]


class ConsentsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envio_documentos_a_proveedor_llm: bool | None = None
    avisos_por_correo: bool | None = None
    tratamiento_datos: bool | None = None


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


def _consents_response(store: AuthStore, user_id: str) -> ConsentsResponse:
    current = store.current_consents(user_id)
    states: list[ConsentState] = []
    for consent_type in CONSENT_TYPES:
        record: ConsentRecord | None = current.get(consent_type)
        states.append(
            ConsentState(
                type=consent_type,
                granted=record.granted if record else False,
                timestamp=record.timestamp if record else None,
                text_version=record.text_version if record else None,
            )
        )
    return ConsentsResponse(text_version=CONSENT_TEXT_VERSION, consents=states)


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Budget per client address alone (registrations, failed logins across e-mails): wider than per e-mail, so a
# household or an office behind one address is not locked out. Behind a reverse proxy start uvicorn with
# ``--proxy-headers`` (docs/seguridad.md), or every client shares the proxy's address and this budget.
_ADDRESS_FACTOR = 4


def _per_address(limiter: LoginRateLimiter) -> int:
    return limiter.max_attempts * _ADDRESS_FACTOR


def _too_many_attempts(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Demasiados intentos fallidos. Espera unos minutos antes de volver a intentarlo.",
        headers={"Retry-After": str(retry_after)},
    )


def build_router(
    *,
    get_case_store: Callable[[], SQLiteCaseStateStore],
    get_notification_store: Callable[[], NotificationStore],
    get_accion_store: Callable[..., AccionStore] | None = None,
) -> APIRouter:
    router = APIRouter()

    def _sin_acciones() -> AccionStore | None:
        return None

    accion_store_dep = Annotated[AccionStore | None, Depends(get_accion_store or _sin_acciones)]
    guarded = [Depends(require_api_key), Depends(require_auth_enabled)]
    auth_store_dep = Annotated[AuthStore, Depends(get_auth_store)]
    user_dep = Annotated[User, Depends(require_user)]
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    notification_store_dep = Annotated[NotificationStore, Depends(get_notification_store)]

    @router.get("/auth/config", response_model=AuthConfig)
    def get_auth_config() -> AuthConfig:
        """What the web client needs to know before showing anything. Public on purpose."""
        return AuthConfig(
            auth_mode=settings.AUTH_MODE,
            password_min_length=PASSWORD_MIN_LENGTH,
            consent_text_version=CONSENT_TEXT_VERSION,
            privacy_notice_url=settings.PRIVACY_NOTICE_URL,
        )

    @router.post("/auth/register", response_model=SessionResponse, status_code=201, dependencies=guarded)
    def post_register(
        payload: RegisterRequest,
        request: Request,
        store: auth_store_dep,
        limiter: Annotated[LoginRateLimiter, Depends(get_login_limiter)],
    ) -> SessionResponse:
        if not payload.consents.tratamiento_datos:
            raise HTTPException(
                status_code=422,
                detail="Para crear la cuenta tienes que aceptar el tratamiento de tus datos.",
            )
        # Registering is also a way to test which addresses exist, and every new account brings its own
        # per-user LLM budget: every registration from an address counts, not only the refused ones.
        key = f"register|{_client_address(request)}"
        if (retry_after := limiter.retry_after(key, max_attempts=_per_address(limiter))) > 0:
            raise _too_many_attempts(retry_after)
        consents: dict[ConsentType, bool] = {
            consent_type: getattr(payload.consents, consent_type) for consent_type in CONSENT_TYPES
        }
        try:
            user = store.create_user(payload.email, payload.password, consents=consents)
        except (InvalidEmailError, PasswordPolicyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EmailAlreadyRegisteredError as exc:
            limiter.record_failure(key)
            raise HTTPException(
                status_code=409,
                detail="No se ha podido crear la cuenta con ese correo. Si ya tienes una, inicia sesión.",
            ) from exc
        limiter.record_failure(key)
        token, session = store.create_session(user.id)
        return SessionResponse(token=token, expires_at=session.expires_at, user=user)

    @router.post("/auth/login", response_model=SessionResponse, dependencies=guarded)
    def post_login(
        payload: LoginRequest,
        request: Request,
        store: auth_store_dep,
        limiter: Annotated[LoginRateLimiter, Depends(get_login_limiter)],
    ) -> SessionResponse:
        key = f"login|{payload.email.strip().casefold()}|{_client_address(request)}"
        # Also per address alone: one client trying one password on many e-mails (password spraying).
        address_key = f"login-address|{_client_address(request)}"
        retry_after = max(
            limiter.retry_after(key), limiter.retry_after(address_key, max_attempts=_per_address(limiter))
        )
        if retry_after > 0:
            raise _too_many_attempts(retry_after)
        user = store.authenticate(payload.email, payload.password)
        if user is None:
            limiter.record_failure(key)
            limiter.record_failure(address_key)
            # One message for "no such account" and "wrong password".
            raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos.")
        limiter.reset(key)
        token, session = store.create_session(user.id)
        return SessionResponse(token=token, expires_at=session.expires_at, user=user)

    @router.post("/auth/logout", status_code=204, dependencies=guarded)
    def post_logout(
        user: user_dep,
        store: auth_store_dep,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        token = bearer_token(authorization)
        if token:
            store.revoke_session(token)
        return Response(status_code=204)

    @router.get("/auth/me", response_model=User, dependencies=guarded)
    def get_me(user: user_dep) -> User:
        return user

    @router.get("/me/consents", response_model=ConsentsResponse, dependencies=guarded)
    def get_consents(user: user_dep, store: auth_store_dep) -> ConsentsResponse:
        return _consents_response(store, user.id)

    @router.put("/me/consents", response_model=ConsentsResponse, dependencies=guarded)
    def put_consents(payload: ConsentsUpdate, user: user_dep, store: auth_store_dep) -> ConsentsResponse:
        """Grant or withdraw consents. Only the ones present in the body change."""
        if payload.tratamiento_datos is False:
            raise HTTPException(
                status_code=409,
                detail=(
                    "El tratamiento de tus datos es imprescindible para tener cuenta. "
                    "Para retirarlo, elimina tu cuenta: se borrarán todos tus datos."
                ),
            )
        current = store.current_consents(user.id)
        for consent_type in CONSENT_TYPES:
            wanted = getattr(payload, consent_type)
            if wanted is None:
                continue
            record = current.get(consent_type)
            # Record only real changes (or a renewal under a new wording).
            if record is None or record.granted != wanted or record.text_version != CONSENT_TEXT_VERSION:
                store.record_consent(user.id, consent_type, wanted)
        return _consents_response(store, user.id)

    @router.get("/me/export", dependencies=guarded)
    def get_export(
        user: user_dep,
        store: auth_store_dep,
        cases: case_store_dep,
        notifications: notification_store_dep,
        acciones: accion_store_dep,
    ) -> dict[str, Any]:
        """Everything stored about the user, as one JSON document (access / portability).

        The household modules' facts are facts of the case, so they are in ``case.facts``; their notices are in
        ``notifications``, and the actions they prepared in ``pending_actions``.
        """
        exported_cases: list[dict[str, Any]] = []
        for case_id in store.case_ids_of(user.id):
            case = cases.get_case(case_id)
            if case is None:
                continue
            exported_cases.append(
                {
                    "case": case.model_dump(mode="json"),
                    "audit_events": cases.list_audit_events(case_id),
                    "notifications": [n.model_dump(mode="json") for n in notifications.list(case_id)],
                    "notification_deliveries": notifications.deliveries_for_case(case_id),
                    "pending_actions": acciones.exportar(case_id) if acciones is not None else [],
                    # What the user decided or prefers (asistente/nucleo/memoria), with its redacted history.
                    "decisiones": exportar_decisiones(cases.db_path, case_id),
                    # The Gmail module: connection (without the token), derived items, proposals, pinned contacts.
                    "correo": correo_almacen.exportar(case_id, cases.db_path),
                    # Long-running tasks (asistente/nucleo/tareas), each with its history.
                    "tareas": exportar_tareas(cases.db_path, case_id),
                    # Browser tasks (capacidades/navegador): state, steps and the parameters of a prepared one.
                    "navegador": exportar_navegador(cases.db_path, case_id),
                }
            )
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "format_version": 1,
            "profile": user.model_dump(mode="json"),
            # Token hashes are credentials, not data about the user: they are left out.
            "sessions": [s.model_dump(mode="json", exclude={"user_id"}) for s in store.list_sessions(user.id)],
            "consents": [c.model_dump(mode="json") for c in store.consent_history(user.id)],
            "module_choices": [c.model_dump(mode="json") for c in store.module_choice_history(user.id)],
            "cases": exported_cases,
            # Devices registered for push notices and the consent history (asistente/plataforma/push).
            "push": PushStore(cases.db_path).exportar(user.id),
            # Research with sources (capacidades/investigacion): web consent history and cached reports.
            "investigacion": investigacion_capacidad.exportar_usuario(user.id),
            "not_included": (
                "El historial de conversaciones se guarda solo en tu navegador, no en el servidor. "
                "Los documentos que subes no se almacenan."
            ),
        }

    @router.delete("/me", status_code=204, dependencies=guarded)
    def delete_me(
        payload: DeleteAccountRequest,
        request: Request,
        user: user_dep,
        store: auth_store_dep,
        cases: case_store_dep,
        notifications: notification_store_dep,
        acciones: accion_store_dep,
        limiter: Annotated[LoginRateLimiter, Depends(get_login_limiter)],
    ) -> Response:
        """Erase the account and everything attached to it. Irreversible."""
        key = f"delete|{user.id}|{_client_address(request)}"
        if (retry_after := limiter.retry_after(key)) > 0:
            raise _too_many_attempts(retry_after)
        if not store.verify_user_password(user.id, payload.password):
            limiter.record_failure(key)
            raise HTTPException(status_code=403, detail="La contraseña no es correcta. No se ha borrado nada.")
        # Data first, account last: if anything fails half-way the account still
        # exists and the user can simply ask again.
        # The usage log keeps the operator's totals but no longer says whose they were.
        anonimizar_consumo(cases.db_path, user_id=user.id, case_ids=store.case_ids_of(user.id))
        # Revoke the Gmail grants at Google now (the rows go with each case through the store's erasure hook).
        correo_oauth.revocar_de_usuario(store.case_ids_of(user.id), db_path=cases.db_path)
        for case_id in store.case_ids_of(user.id):
            notifications.delete_for_case(case_id)
            if acciones is not None:
                acciones.delete_for_case(case_id)
            # The browser agent's redacted diagnoses (files) and this process's tasks of the case, before the
            # case itself: if the erasure fails, the case is still listed and a new request retries it.
            vigilancia.borrar_de_caso(case_id)
            for tarea in tareas_navegador.gestor().de_caso(case_id):
                tareas_navegador.gestor().cerrar(tarea)
            cases.delete_case(case_id)
        # Research with sources: the user's web consent and cached reports (its own store).
        investigacion_capacidad.borrar_usuario(user.id)
        store.delete_user(user.id)
        limiter.reset(key)
        return Response(status_code=204)

    return router

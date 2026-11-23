"""Security review (docs/seguridad.md): authorisation of every route, isolation on the query-parameter and
body ``case_id`` endpoints, request-body cap, response headers, operator-only figures, per-user LLM budget
attribution, registration and password-spraying limits, and erasure of the usage log."""

from __future__ import annotations

import base64
import sqlite3
from typing import Any

from fastapi.routing import iter_route_contexts
import pytest

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.modulos.acciones import AccionPendiente, AccionStore
from asistente.plataforma import settings
from asistente.plataforma.api.api import app, get_agent, get_document_provider, get_fact_extractor
from asistente.plataforma.ops import consumo
from asistente.plataforma.ops.consumo import ConsumoStore, Usage
from .conftest import CONSENTS, Env

# Routes that answer without a session on purpose. Everything else needs one when AUTH_MODE=required.
PUBLIC = {
    "/health",
    "/metrics",
    "/auth/config",
    "/auth/register",
    "/auth/login",
    "/evaluacion/consentimiento",
    # Deterministic calculators of the self-employed quarterly forms: no case, no model, no stored data.
    "/autonomos/ejercicios",
    "/autonomos/actividad",
    "/autonomos/modelo-130",
    "/autonomos/modelo-303",
    # Google's OAuth redirect: a redirect carries no session header, so the single-use ``state`` issued to the
    # case owner is its credential (without it, 401; a wrong or used one changes nothing): see its own tests.
    "/correo/oauth/callback",
}
# Service-wide figures: the operator's, never a user's (404 without the operator key).
OPERATOR = {"/usage", "/eventos/salud", "/plataforma/salud"}
# Gone with accounts on (404 to everyone): the API's documentation (S-18, unless API_DOCS_PUBLICAS=true).
DISABLED = {"/openapi.json", "/docs", "/redoc"}


def _routes() -> list[tuple[str, str]]:
    out = []
    for route in iter_route_contexts(app.routes):  # FastAPI ≥0.16x agrupa los routers incluidos
        methods = getattr(route, "methods", None) or set()
        for method in sorted(methods - {"HEAD"}):
            out.append((method, route.path))
    return out


def _fill(path: str) -> str:
    return path.replace("{", "x_").replace("}", "")


class _MustNotRun:
    name, model = "fake", "fake-1"

    def __getattr__(self, name: str):
        raise AssertionError(f"reached the model ({name}) for a request that had to be refused")


@pytest.fixture
def no_model() -> Any:
    app.dependency_overrides[get_agent] = _MustNotRun
    app.dependency_overrides[get_document_provider] = _MustNotRun
    app.dependency_overrides[get_fact_extractor] = _MustNotRun
    yield
    for dependency in (get_agent, get_document_provider, get_fact_extractor):
        app.dependency_overrides.pop(dependency, None)


# --- every route: public by design, operator-only, or behind a session ----------------------------------


def test_every_route_is_public_by_design_operator_only_or_needs_a_session(
    env: Env, no_model, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", True)  # otherwise its routes answer 503 before anything
    monkeypatch.delenv("HACIENDA_OPERATOR_KEY", raising=False)
    declared = {path for _, path in _routes()}
    assert PUBLIC | OPERATOR | DISABLED <= declared, (PUBLIC | OPERATOR | DISABLED) - declared  # not stale
    for method, path in _routes():
        if path in PUBLIC:
            continue
        response = env.client.request(method, _fill(path), json={})
        expected = 404 if path in OPERATOR | DISABLED else 401
        assert response.status_code == expected, (method, path, response.status_code, response.text[:200])


def test_operator_figures_need_the_operator_key_once_accounts_are_on(env: Env, monkeypatch) -> None:
    ana = env.register("ana@example.com")
    # Not even a logged-in user, and not while the operator has not configured a key.
    assert env.client.get("/usage", headers=ana).status_code == 404
    monkeypatch.setenv("HACIENDA_OPERATOR_KEY", "clave-del-operador")
    assert env.client.get("/usage", headers=ana).status_code == 404
    assert env.client.get("/usage", headers={**ana, "X-Operator-Key": "otra"}).status_code == 404
    assert env.client.get("/usage", headers={"X-Operator-Key": "clave-del-operador"}).status_code == 200
    # Without accounts nothing changes: the API key (when set) is what guards them.
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    assert env.client.get("/usage").status_code == 200


# --- isolation on endpoints that take the case in the query or the body ----------------------------------


def test_another_users_case_is_404_on_the_query_and_body_endpoints(env: Env, no_model) -> None:
    ana = env.register("ana@example.com", consents={**CONSENTS, "envio_documentos_a_proveedor_llm": True})
    luis = env.register("luis@example.com", consents={**CONSENTS, "envio_documentos_a_proveedor_llm": True})
    for headers in (ana, luis):
        assert env.client.put("/me/modulos/suscripciones", json={"activo": True}, headers=headers).status_code == 200
    case_id = env.new_case(ana)
    acciones = AccionStore(env.db_path)
    accion = acciones.add(
        AccionPendiente(case_id=case_id, modulo="suscripciones", herramienta="x", argumentos={}, resumen="r")
    )
    env.cases.append_audit_event(case_id, {"event_type": "agent_answer", "input": "¿Plazo?"})
    feedback = env.client.post(
        "/evaluacion/feedback",
        json={"case_id": case_id, "pregunta": "¿Plazo?", "respuesta": "El 30 de junio.", "valoracion": "positiva"},
        headers=ana,
    )
    assert feedback.status_code == 201, feedback.text
    before = env.cases.get_case(case_id)
    audit_before = env.cases.list_audit_events(case_id)
    statement = base64.b64encode(b"Fecha;Concepto;Importe\n01/01/2026;NETFLIX;-12,99\n").decode()
    requests: list[tuple[str, str, dict | None]] = [
        ("get", f"/acciones?case_id={case_id}", None),
        ("get", f"/propuestas?case_id={case_id}", None),
        ("post", f"/acciones/{accion.id}/confirmar", {}),
        ("post", f"/acciones/{accion.id}/rechazar", {}),
        (
            "post",
            "/modulos/suscripciones/herramientas/suscripciones_analizar_extracto",
            {"case_id": case_id, "argumentos": {"contenido_base64": statement}},
        ),
        (
            "post",
            "/modulos/suscripciones/documento",
            {"case_id": case_id, "filename": "a.txt", "content_base64": "eA=="},
        ),
        ("get", f"/evaluacion/feedback?case_id={case_id}", None),
        ("post", "/planificar", {"case_id": case_id, "importe_disponible": 1000}),
        ("delete", f"/evaluacion/feedback/{feedback.json()['id']}", None),
    ]
    for method, path, body in requests:
        response = env.client.request(method, path, json=body, headers=luis)
        assert response.status_code == 404, (method, path, response.text)
    # Nothing of ana's changed through those attempts.
    assert env.cases.get_case(case_id) == before
    assert env.cases.list_audit_events(case_id) == audit_before
    assert acciones.get(accion.id).estado == "pendiente"


# --- request body cap and response headers ----------------------------------------------------------------


def test_an_oversized_body_is_refused_before_it_is_read(env: Env, monkeypatch) -> None:
    monkeypatch.setenv("API_MAX_BODY_BYTES", "1000")
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    big = {"value": "x" * 5000}
    declared = env.client.put(f"/cases/{case_id}/facts/edad", json=big, headers=ana)
    assert declared.status_code == 413
    assert declared.headers["x-content-type-options"] == "nosniff"

    def chunks():  # no Content-Length: the cap has to count what is streamed
        yield b'{"value": "'
        for _ in range(10):
            yield b"x" * 500
        yield b'"}'

    streamed = env.client.put(
        f"/cases/{case_id}/facts/edad", content=chunks(), headers={**ana, "Content-Type": "application/json"}
    )
    assert streamed.status_code == 413
    # Under the cap everything works as before.
    assert env.client.put(f"/cases/{case_id}/facts/edad", json={"value": 40}, headers=ana).status_code == 200


def test_responses_carry_security_headers_and_are_not_cached(env: Env) -> None:
    ana = env.register("ana@example.com")
    for response in (env.client.get("/health"), env.client.get("/auth/me", headers=ana), env.client.get("/nope")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"


# --- per-user LLM budget ------------------------------------------------------------------------------------


class _RecordsWhoPays:
    name, model = "fake", "fake-1"

    def __init__(self) -> None:
        self.users: list[str | None] = []

    def _who(self) -> None:
        contexto = consumo._CONTEXTO.get()
        self.users.append(contexto.user_id if contexto is not None else None)

    def complete_json(self, **kwargs):
        self._who()
        raise ProviderError("sin red")

    def extract(self, *args, **kwargs):
        self._who()
        raise ProviderError("sin red")


def test_document_and_turn_model_calls_are_charged_to_the_user(env: Env) -> None:
    ana = env.register("ana@example.com", consents={**CONSENTS, "envio_documentos_a_proveedor_llm": True})
    case_id = env.new_case(ana)
    fake = _RecordsWhoPays()
    app.dependency_overrides[get_document_provider] = lambda: fake
    app.dependency_overrides[get_fact_extractor] = lambda: fake
    try:
        document = {"filename": "c.txt", "content_base64": base64.b64encode(b"Retenciones: 100,00").decode()}
        env.client.post(f"/cases/{case_id}/documents", json=document, headers=ana)
        with pytest.raises(ProviderError):
            env.client.post(f"/cases/{case_id}/turn", json={"user_input": "Soy residente"}, headers=ana)
    finally:
        app.dependency_overrides.pop(get_document_provider, None)
        app.dependency_overrides.pop(get_fact_extractor, None)
    assert fake.users == [ana["_user_id"], ana["_user_id"]]


def test_an_exhausted_budget_on_a_turn_is_a_429(env: Env, monkeypatch) -> None:
    from asistente.nucleo.decision.fact_extractor import FactExtractionError
    from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)

    class _Agotado:
        def extract(self, *args, **kwargs):
            try:
                raise PresupuestoAgotadoError("Has alcanzado tu límite diario.", limite="diario_por_usuario")
            except PresupuestoAgotadoError as exc:
                raise FactExtractionError(f"Fact extraction failed: {exc}") from exc

    app.dependency_overrides[get_fact_extractor] = _Agotado
    try:
        response = env.client.post(f"/cases/{case_id}/turn", json={"user_input": "hola"}, headers=ana)
    finally:
        app.dependency_overrides.pop(get_fact_extractor, None)
    assert response.status_code == 429
    assert response.json()["detail"] == "Has alcanzado tu límite diario."


# --- error details ---------------------------------------------------------------------------------------------


def test_an_unexpected_failure_in_the_stream_does_not_leak_its_text(env: Env) -> None:
    ana = env.register("ana@example.com")

    class _Rompe:
        name, model = "fake", "fake-1"

        def run(self, *args, **kwargs):
            raise RuntimeError("/srv/secreto/state.sqlite3: no such table users")

    app.dependency_overrides[get_agent] = _Rompe
    try:
        response = env.client.post("/agent/stream", json={"query": "hola"}, headers=ana)
    finally:
        app.dependency_overrides.pop(get_agent, None)
    assert '"status": 500' in response.text
    assert "secreto" not in response.text and "sqlite3" not in response.text


# --- account creation and login limits -------------------------------------------------------------------------


def test_registrations_from_one_address_are_limited(env: Env) -> None:
    allowed = env.limiter.max_attempts * 4
    for n in range(allowed):
        env.register(f"cuenta{n}@example.com")
    response = env.client.post(
        "/auth/register",
        json={"email": "una-mas@example.com", "password": "una-clave-larga-1", "consents": CONSENTS},
    )
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


def test_one_password_against_many_emails_is_limited_per_address(env: Env) -> None:
    allowed = env.limiter.max_attempts * 4
    for n in range(allowed):
        failed = env.client.post("/auth/login", json={"email": f"v{n}@example.com", "password": "Primavera2026"})
        assert failed.status_code == 401
    blocked = env.client.post("/auth/login", json={"email": "otra@example.com", "password": "Primavera2026"})
    assert blocked.status_code == 429


# --- erasure -----------------------------------------------------------------------------------------------------


def test_deleting_the_account_detaches_the_usage_log(env: Env) -> None:
    ana = env.register("ana@example.com")
    luis = env.register("luis@example.com")
    case_ana, case_luis = env.new_case(ana), env.new_case(luis)
    log = ConsumoStore(env.db_path)
    usage = Usage(provider="fake", model="m", purpose="agente", input_tokens=10, output_tokens=5)
    log.registrar(usage, user_id=ana["_user_id"], case_id=case_ana)
    log.registrar(usage, user_id=None, case_id=case_ana)
    log.registrar(usage, user_id=luis["_user_id"], case_id=case_luis)
    log.close()
    response = env.client.request("DELETE", "/me", json={"password": "una-clave-larga-1"}, headers=ana)
    assert response.status_code == 204
    with sqlite3.connect(env.db_path) as conn:
        rows = conn.execute("SELECT user_id, case_id, input_tokens FROM llm_usage ORDER BY id").fetchall()
    # The operator keeps the totals; nothing says whose they were. Luis's rows are untouched.
    assert rows == [(None, None, 10), (None, None, 10), (luis["_user_id"], case_luis, 10)]


def test_made_up_api_keys_do_not_get_a_fresh_rate_limit_each(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from asistente.plataforma.api import api

    monkeypatch.setattr(api._rate_limiter, "limit", 2)
    monkeypatch.setattr(api._rate_limiter, "_windows", {})
    client = TestClient(app)
    codes = [client.get("/auth/config", headers={"X-API-Key": f"inventada-{n}"}).status_code for n in range(3)]
    assert codes == [200, 200, 429]


def test_password_derivations_run_a_few_at_a_time(monkeypatch) -> None:
    """Each scrypt derivation holds 128 MB at the production work factor and logins need no session."""
    import threading
    import time

    from asistente.plataforma.auth import passwords

    activos, maximo, cerrojo = 0, 0, threading.Lock()

    def scrypt_lento(*args, **kwargs):
        nonlocal activos, maximo
        with cerrojo:
            activos += 1
            maximo = max(maximo, activos)
        time.sleep(0.05)
        with cerrojo:
            activos -= 1
        return b"\x00" * 32

    monkeypatch.setattr(passwords.hashlib, "scrypt", scrypt_lento)
    hilos = [threading.Thread(target=passwords.burn_password_check, args=("una-clave-larga-1",)) for _ in range(12)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert 1 <= maximo <= passwords._KDF_CONCURRENCY

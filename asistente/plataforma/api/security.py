"""API-edge security: optional API-key auth, a per-client rate limiter, a request-body cap, response
security headers and the operator-only gate (see docs/seguridad.md).

Both are **opt-in via environment** and disabled by default, so local dev and
the test suite keep working unauthenticated. Turn them on in any deployment that
exposes the API beyond localhost — ``/qa`` spends real OpenAI tokens and loads
multi-GB models per process, so an open endpoint is a cost/abuse footgun.

The limiter is a simple in-process fixed-window counter. That is correct for a
single-process uvicorn deployment (the project's target); behind multiple
workers each process keeps its own window, so set the per-process limit
accordingly or front the service with a shared limiter (nginx, API gateway).
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from typing import Any

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

_AUTH_DISABLED_WARNED = False


def _iguales(recibido: str, esperado: str) -> bool:
    """Constant-time comparison of two secrets as UTF-8 bytes. ``hmac.compare_digest`` on ``str`` raises
    TypeError for non-ASCII text, which turned a key with an «ñ» (sent or configured) into a 500."""
    return hmac.compare_digest(recibido.encode("utf-8", "surrogateescape"), esperado.encode("utf-8", "surrogateescape"))


def _verdadero(nombre: str) -> bool:
    return os.environ.get(nombre, "").strip().lower() in ("1", "true", "yes", "si", "sí", "on")


# --- start-up guard -----------------------------------------------------------------


class ArranqueInseguroError(RuntimeError):
    """The API would start with nothing protecting it."""


def comprobar_arranque() -> None:
    """Refuse to start with ``AUTH_MODE=off`` and no ``HACIENDA_API_KEY``: every case would be readable and
    writable by anyone who reaches the port. ``PERMITIR_SIN_AUTENTICACION=1`` is the explicit, logged opt-out
    for a single-user machine."""
    from asistente.plataforma import settings

    if settings.AUTH_MODE == "required" or os.environ.get("HACIENDA_API_KEY"):
        return
    if _verdadero("PERMITIR_SIN_AUTENTICACION"):
        logger.warning("API started WITHOUT authentication (PERMITIR_SIN_AUTENTICACION=1).")
        return
    raise ArranqueInseguroError(
        "La API no arranca sin autenticación: configura AUTH_MODE=required o HACIENDA_API_KEY, "
        "o declara PERMITIR_SIN_AUTENTICACION=1 si es una máquina de un solo usuario."
    )


# --- Host, Origin and Content-Type of the requests ------------------------------------

DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1")


def hosts_permitidos() -> list[str]:
    """``API_ALLOWED_HOSTS`` (comma separated, ``*.example.com`` allowed); by default only the loopback names,
    so a DNS-rebinding page cannot reach a local API through its own domain."""
    valor = os.environ.get("API_ALLOWED_HOSTS", "")
    hosts = [h.strip() for h in valor.split(",") if h.strip()]
    return hosts or list(DEFAULT_ALLOWED_HOSTS)


def origenes_permitidos(base: list[str]) -> set[str]:
    """``base`` plus ``API_ALLOWED_ORIGINS`` (comma separated, exact ``scheme://host[:port]``)."""
    extra = os.environ.get("API_ALLOWED_ORIGINS", "")
    return {o.rstrip("/") for o in [*base, *extra.split(",")] if o.strip()}


_METODOS_ESCRITURA = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def make_write_guard_middleware(base_origins: list[str]):
    """Writes (POST/PUT/PATCH/DELETE) are refused with 403 when they carry an ``Origin`` that is neither the API's
    own nor an allowed one, and with 415 when they carry a body that is not JSON. A cross-site form or a
    ``text/plain`` fetch (the "simple requests" a browser sends without CORS preflight) cannot write."""
    from starlette.responses import JSONResponse

    async def write_guard_middleware(request: Request, call_next: Any) -> Any:
        if request.method not in _METODOS_ESCRITURA:
            return await call_next(request)
        origen = request.headers.get("origin")
        if origen is not None:
            propio = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origen.rstrip("/") != propio and origen.rstrip("/") not in origenes_permitidos(base_origins):
                return JSONResponse(status_code=403, content={"detail": "Origen no permitido."})
        longitud = request.headers.get("content-length")
        con_cuerpo = (longitud is not None and longitud.strip() not in ("", "0")) or (
            "transfer-encoding" in request.headers
        )
        if con_cuerpo:
            tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if tipo != "application/json" and not (tipo.startswith("application/") and tipo.endswith("+json")):
                return JSONResponse(status_code=415, content={"detail": "El cuerpo debe ser JSON."})
        return await call_next(request)

    return write_guard_middleware


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency enforcing the ``X-API-Key`` header when configured.

    When ``HACIENDA_API_KEY`` is unset, auth is disabled (a one-time warning is
    logged). When set, requests must present a matching key or get a 401. The
    comparison is constant-time to avoid leaking the key via timing.
    """
    expected = os.environ.get("HACIENDA_API_KEY")
    if not expected:
        global _AUTH_DISABLED_WARNED
        if not _AUTH_DISABLED_WARNED:
            logger.warning(
                "HACIENDA_API_KEY is not set — the API is UNAUTHENTICATED. "
                "Set it before exposing the service beyond localhost."
            )
            _AUTH_DISABLED_WARNED = True
        return
    if not x_api_key or not _iguales(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class RateLimiter:
    """Thread-safe fixed-window limiter keyed by an opaque client id.

    ``limit_per_min <= 0`` disables it entirely. Stale windows are pruned
    opportunistically so the backing dict can't grow without bound under a
    churn of distinct clients.
    """

    def __init__(self, limit_per_min: int) -> None:
        self.limit = limit_per_min
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    def allow(self, client: str) -> bool:
        if self.limit <= 0:
            return True
        now_min = int(time.time() // 60)
        with self._lock:
            if len(self._windows) > 4096:
                self._windows = {k: v for k, v in self._windows.items() if v[0] == now_min}
            start, count = self._windows.get(client, (now_min, 0))
            if start != now_min:
                start, count = now_min, 0
            count += 1
            self._windows[client] = (start, count)
            return count <= self.limit


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting: the API key when it is the configured one, else peer IP.

    Only a VALID key may name the bucket: the limiter runs before authentication, and keying on whatever
    ``X-API-Key`` the client sends would give every made-up value a fresh budget (an unlimited bypass).
    """
    api_key = request.headers.get("X-API-Key")
    expected = os.environ.get("HACIENDA_API_KEY")
    if api_key and expected and _iguales(api_key, expected):
        return "key:configured"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def make_rate_limit_middleware(limiter: RateLimiter):
    """Build a Starlette HTTP middleware that enforces ``limiter`` per client.

    Health and metrics endpoints are exempt so liveness probes and scrapers are
    never throttled. Returns 429 with a ``Retry-After`` hint when over budget.
    """
    from starlette.responses import JSONResponse

    exempt = {"/health", "/metrics"}

    async def rate_limit_middleware(request: Request, call_next):
        if request.url.path in exempt or limiter.allow(client_key(request)):
            return await call_next(request)
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded; retry shortly."},
            headers={"Retry-After": "60"},
        )

    return rate_limit_middleware


# --- request body cap ----------------------------------------------------------

# The largest legitimate body is a bank statement for a module tool: 20 MB of file, ~27 MB once in base64
# inside JSON. Anything bigger is refused before it is read into memory.
DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024


def max_body_bytes() -> int:
    """``API_MAX_BODY_BYTES`` (bytes); a value that is not a positive integer keeps the default."""
    try:
        valor = int(os.environ.get("API_MAX_BODY_BYTES", ""))
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES
    return valor if valor > 0 else DEFAULT_MAX_BODY_BYTES


_TOO_LARGE = b'{"detail":"La petici\\u00f3n es demasiado grande."}'


class BodySizeLimitMiddleware:
    """Pure ASGI middleware: 413 for a body over the limit, declared (Content-Length) or streamed (chunked).

    uvicorn and Starlette impose no limit of their own: without this a single request could make the
    process buffer gigabytes while FastAPI parses the JSON. A declared length is checked before anything is
    read (the server enforces that framing); a chunked body is read here, up to the limit, and replayed to
    the application only when it fits.
    """

    def __init__(self, app: Any, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limite = self.max_bytes or max_body_bytes()
        cabeceras = dict(scope.get("headers", []))
        if b"content-length" in cabeceras:
            try:
                declarado = int(cabeceras[b"content-length"])
            except ValueError:
                declarado = limite + 1
            if declarado > limite:
                await _responder_413(send)
                return
            await self.app(scope, receive, send)
            return
        if cabeceras.get(b"transfer-encoding", b"").lower() != b"chunked":
            await self.app(scope, receive, send)
            return
        partes: list[bytes] = []
        leidos = 0
        while True:
            mensaje = await receive()
            if mensaje["type"] != "http.request":  # the client went away
                return
            cuerpo = mensaje.get("body", b"")
            leidos += len(cuerpo)
            if leidos > limite:
                await _responder_413(send)
                return
            partes.append(cuerpo)
            if not mensaje.get("more_body", False):
                break
        entregado = False

        async def repetir() -> Any:
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": b"".join(partes), "more_body": False}
            return await receive()

        await self.app(scope, repetir, send)


async def _responder_413(send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE)).encode()),
                (b"connection", b"close"),
                *((nombre.lower().encode(), valor.encode()) for nombre, valor in SECURITY_HEADERS),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE})


# --- response security headers ----------------------------------------------------

# A JSON API: nothing it returns is meant to be rendered as a page, framed, sniffed or cached by a shared
# cache (answers carry personal and tax data). The Swagger UI pages (/docs, /redoc) load their assets from a
# CDN, so they keep their own policy; the reverse proxy adds HSTS (it terminates TLS).
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_PAGINAS_DOCUMENTACION = ("/docs", "/redoc")


async def security_headers_middleware(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    for nombre, valor in SECURITY_HEADERS:
        response.headers.setdefault(nombre, valor)
    if not request.url.path.startswith(_PAGINAS_DOCUMENTACION):
        response.headers.setdefault("Content-Security-Policy", _API_CSP)
    # Personal data must not stay in shared or browser caches; endpoints that set their own policy keep it.
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# --- operator-only endpoints -------------------------------------------------------


def require_operator(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_operator_key: str | None = Header(default=None, alias="X-Operator-Key"),
) -> None:
    """Service-wide figures (``/usage``, ``/eventos/salud``) are for the operator, not for the users.

    Without accounts (``AUTH_MODE=off``) nothing changes: the API key, when configured, is enough. With
    accounts every user holds a session (and, behind a proxy that injects it, the API key too), so these
    endpoints need ``HACIENDA_OPERATOR_KEY`` in ``X-Operator-Key``; while it is not configured they answer 404.
    """
    require_api_key(x_api_key)
    from asistente.plataforma import settings

    if settings.AUTH_MODE != "required":
        return
    esperado = os.environ.get("HACIENDA_OPERATOR_KEY", "")
    if not esperado or not x_operator_key or not _iguales(x_operator_key, esperado):
        raise HTTPException(status_code=404, detail="Not Found")


# --- size of what reaches the model (S-15) -----------------------------------------------------------------

# The question itself: far above any real question (a long one is ~2 000 characters), far below a context window.
DEFAULT_MAX_QUERY_CHARS = 20_000
# The prior conversation sent with each question: the newest messages are kept, the oldest dropped first. The
# whole history is re-sent on every step of the agent, so it is what multiplies the cost of one question.
DEFAULT_MAX_HISTORY_CHARS = 60_000
DEFAULT_MAX_HISTORY_MESSAGES = 40


def _entero_positivo(nombre: str, defecto: int) -> int:
    """An environment variable read per call; a value that is not a positive integer keeps the default."""
    try:
        valor = int(os.environ.get(nombre, ""))
    except ValueError:
        return defecto
    return valor if valor > 0 else defecto


def max_query_chars() -> int:
    return _entero_positivo("API_MAX_QUERY_CHARS", DEFAULT_MAX_QUERY_CHARS)


def max_history_chars() -> int:
    return _entero_positivo("API_MAX_HISTORY_CHARS", DEFAULT_MAX_HISTORY_CHARS)


def max_history_messages() -> int:
    return _entero_positivo("API_MAX_HISTORY_MESSAGES", DEFAULT_MAX_HISTORY_MESSAGES)


def comprobar_pregunta(texto: str) -> None:
    """422 with a message for the user when the question alone is longer than ``API_MAX_QUERY_CHARS``."""
    limite = max_query_chars()
    if len(texto) > limite:
        tiene, maximo = (f"{n:,}".replace(",", ".") for n in (len(texto), limite))
        raise HTTPException(
            status_code=422,
            detail=(
                f"La pregunta es demasiado larga: tiene {tiene} caracteres y el máximo es {maximo}. "
                "Resúmela o divídela en varias preguntas."
            ),
        )


def _caracteres(mensaje: Any) -> int:
    contenido = mensaje.get("content", "") if isinstance(mensaje, dict) else mensaje
    return len(contenido) if isinstance(contenido, str) else len(str(contenido))


def recortar_historial(historial: list[Any]) -> list[Any]:
    """The newest messages of ``historial`` within ``API_MAX_HISTORY_MESSAGES`` and ``API_MAX_HISTORY_CHARS``.

    Oldest first out; a message that does not fit whole is dropped, never cut. The result starts with a user
    message (an answer whose question was dropped would be out of context)."""
    max_mensajes, max_caracteres = max_history_messages(), max_history_chars()
    conservados: list[Any] = []
    total = 0
    for mensaje in reversed(historial):
        total += _caracteres(mensaje)
        if len(conservados) >= max_mensajes or total > max_caracteres:
            break
        conservados.append(mensaje)
    conservados.reverse()
    while conservados and not (isinstance(conservados[0], dict) and conservados[0].get("role") == "user"):
        conservados.pop(0)
    return conservados


# --- the API's own documentation (S-18) ----------------------------------------------------------------------


def docs_publicas() -> bool:
    """Whether ``/docs``, ``/redoc`` and ``/openapi.json`` answer: ``API_DOCS_PUBLICAS`` (true/false) when set;
    otherwise only without accounts (with ``AUTH_MODE=required`` they describe every route to anyone)."""
    from asistente.plataforma import settings

    valor = os.environ.get("API_DOCS_PUBLICAS", "").strip().lower()
    if valor in ("1", "true", "yes", "si", "sí", "on"):
        return True
    if valor in ("0", "false", "no", "off"):
        return False
    return settings.AUTH_MODE != "required"


def require_docs_publicas() -> None:
    if not docs_publicas():
        raise HTTPException(status_code=404, detail="Not Found")


# --- the legacy /qa chain (S-14) -----------------------------------------------------------------------------


def require_qa_legado() -> None:
    """``/qa`` (the LangChain chain, unmetered and outside the daily budgets) only answers without accounts:
    with ``AUTH_MODE=required`` it is a 404, before the API key, the session or the chain are looked at."""
    from asistente.plataforma import settings

    if settings.AUTH_MODE == "required":
        raise HTTPException(status_code=404, detail="Not Found")

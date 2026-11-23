"""HTTP-edge observability: request IDs, structured access logs, and a
Prometheus-format ``/metrics`` registry.

The repo already had ``asistente.plataforma.observability.metrics.MetricsCollector`` but
nothing in the running app ever instantiated it, so latency/error series were
empty and the dashboard plotted nothing. This module wires real per-request
signals into the FastAPI app:

* every request gets an ``X-Request-ID`` (echoed back) and a one-line JSON
  access log via :func:`emit_structured_log`;
* request counts, error counts and latency sums are aggregated per
  *route template* (``/cases/{case_id}/turn``, not the expanded id, to bound
  cardinality) and exposed at ``/metrics`` in Prometheus text format — no extra
  dependency required.
"""

from __future__ import annotations

from collections import defaultdict
import threading
import time
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import Response

from asistente.plataforma.observability.metrics import emit_structured_log


class ApiMetrics:
    """Thread-safe, bounded HTTP metrics registry with Prometheus output."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency_sum_ms: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self._errors_total = 0

    def record(self, method: str, route: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self._requests[(method, route, status)] += 1
            self._latency_sum_ms[(method, route)] += latency_ms
            self._latency_count[(method, route)] += 1
            if status >= 500:
                self._errors_total += 1

    def prometheus_text(self) -> str:
        lines: list[str] = [
            "# HELP hacienda_http_requests_total Total HTTP requests by method, route and status.",
            "# TYPE hacienda_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(
                    f'hacienda_http_requests_total{{method="{_escapar(method)}",route="{_escapar(route)}",status="{status}"}} {count}'
                )
            lines.append("# HELP hacienda_http_request_errors_total HTTP responses with status >= 500.")
            lines.append("# TYPE hacienda_http_request_errors_total counter")
            lines.append(f"hacienda_http_request_errors_total {self._errors_total}")
            lines.append("# HELP hacienda_http_request_latency_ms_sum Cumulative latency per method+route (ms).")
            lines.append("# TYPE hacienda_http_request_latency_ms_sum counter")
            for (method, route), total in sorted(self._latency_sum_ms.items()):
                lines.append(
                    f'hacienda_http_request_latency_ms_sum{{method="{_escapar(method)}",route="{_escapar(route)}"}} {total:.3f}'
                )
            lines.append("# HELP hacienda_http_request_latency_count Number of requests per method+route.")
            lines.append("# TYPE hacienda_http_request_latency_count counter")
            for (method, route), count in sorted(self._latency_count.items()):
                lines.append(
                    f'hacienda_http_request_latency_count{{method="{_escapar(method)}",route="{_escapar(route)}"}} {count}'
                )
        return "\n".join(lines) + "\n"


# Label of every request that matched no route (404, refused by a middleware): the raw path is never a
# label, or anyone could grow the registry without bound (and inject text into the exposition).
RUTA_SIN_PLANTILLA = "sin_ruta"
_METODOS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def _route_template(request: Request) -> str:
    """Matched route path (``/cases/{case_id}``) to keep metric cardinality bounded; a fixed label when no
    route matched."""
    route = request.scope.get("route")
    plantilla = getattr(route, "path", None)
    return plantilla if isinstance(plantilla, str) and plantilla else RUTA_SIN_PLANTILLA


def _metodo(request: Request) -> str:
    return request.method if request.method in _METODOS else "OTRO"


def _escapar(valor: object) -> str:
    """A Prometheus label value: backslash, double quote and newline escaped."""
    return str(valor).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


async def observability_middleware(request: Request, call_next) -> Response:
    """Stamp a request id, time the request, log it, and feed :class:`ApiMetrics`."""
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    start = time.perf_counter()
    status = 500
    try:
        response: Response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        route = _route_template(request)
        metrics: ApiMetrics | None = getattr(request.app.state, "api_metrics", None)
        if metrics is not None:
            metrics.record(_metodo(request), route, status, elapsed_ms)
        emit_structured_log(
            "http_request",
            {
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status": status,
                "latency_ms": round(elapsed_ms, 2),
            },
        )

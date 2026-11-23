"""The HTTP transport the OAuth and Gmail clients use, injectable (tests and the local fake server).

The default transport never follows redirects, has a timeout, and only talks to the URLs the operator
configured (Google's documented endpoints by default). Nothing here logs URLs with tokens, request bodies
or response bodies: only the status code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any, Protocol

TIMEOUT_SEGUNDOS = 20
MAX_RESPUESTA = 10_000_000  # bytes


class CorreoError(Exception):
    """A failure the user can be told about (Spanish message, no secrets)."""


@dataclass(frozen=True)
class RespuestaHttp:
    estado: int
    cuerpo: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.cuerpo.decode("utf-8") or "null")
        except (ValueError, UnicodeError) as exc:
            raise CorreoError("La respuesta del servicio de correo no es JSON válido.") from exc


class Transporte(Protocol):
    def __call__(
        self,
        metodo: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        datos: Mapping[str, str] | None = None,
        cabeceras: Mapping[str, str] | None = None,
    ) -> RespuestaHttp: ...


_cliente: list[Any] = []


def _cliente_google() -> Any:
    """A client of its own: no redirects (a token must never follow one) and this module's size limit."""
    from asistente.plataforma import http

    if not _cliente:
        _cliente.append(http.Cliente(timeout=TIMEOUT_SEGUNDOS, follow_redirects=False, max_bytes=MAX_RESPUESTA))
    return _cliente[0]


def transporte_http(
    metodo: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    datos: Mapping[str, str] | None = None,
    cabeceras: Mapping[str, str] | None = None,
) -> RespuestaHttp:
    import httpx

    from asistente.plataforma import http

    try:
        respuesta = _cliente_google().peticion(
            metodo,
            url,
            params=dict(params) if params else None,
            data=dict(datos) if datos else None,
            cabeceras=cabeceras,
        )
    except http.RespuestaDemasiadoGrande as exc:
        raise CorreoError("La respuesta de Google es demasiado grande.") from exc
    except httpx.HTTPError as exc:
        raise CorreoError("No se pudo contactar con Google. Inténtalo más tarde.") from exc
    return RespuestaHttp(estado=respuesta.status_code, cuerpo=respuesta.content)


_transporte: list[Transporte] = []


def transporte() -> Transporte:
    return _transporte[0] if _transporte else transporte_http


def configurar_transporte(nuevo: Transporte | Callable[..., RespuestaHttp] | None) -> None:
    """Replace the transport (tests, the local fake server); None restores the default."""
    _transporte.clear()
    if nuevo is not None:
        _transporte.append(nuevo)  # type: ignore[arg-type]


__all__ = ["CorreoError", "RespuestaHttp", "Transporte", "configurar_transporte", "transporte", "transporte_http"]

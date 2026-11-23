"""The project's one HTTP client, on top of ``httpx``.

Every outgoing request of the assistant (the BOE, the BDNS, ESIOS, the AEAT and
DGT portals, healthchecks, webhooks, Google's mail API) goes through here, so the
rules are written once:

* explicit timeouts (connect and read);
* retries with growing waits on network errors, 429 and 5xx, honouring
  ``Retry-After`` (seconds or an HTTP date). Only idempotent methods retry unless
  the caller asks: a POST to a webhook must not be delivered twice;
* a minimum pause between two requests to the same host, shared by every caller
  of the same :class:`Cliente`;
* one fixed, identifiable User-Agent;
* a maximum response size, read in chunks so an endless answer never fills memory;
* optionally, the body is handed to the format watch
  (:func:`asistente.conocimiento.formatos.observar`) under a key the caller names.

It lives in ``plataforma`` (not ``conocimiento``) because the callers span every
layer: ``nucleo`` (webhooks), ``ambitos`` (BDNS, ESIOS), ``capacidades`` (mail,
research), ``conocimiento`` (the BOE) and ``plataforma`` (CLIs, ops), and all of
them already depend on ``plataforma`` for their settings.

:class:`ClienteAsincrono` applies the same rules with ``httpx.AsyncClient``.

Tests never reach the network: build a client with
``Cliente(transport=httpx.MockTransport(handler), dormir=lambda _s: None)`` and
install it with :func:`usar`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import logging
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "cerebro-agentes/0.1 (asistente personal; https://github.com/DioCrafts/cerebro-agentes)"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_BYTES = 64 * 1024 * 1024
REINTENTOS = 2
ESPERA_BASE = 1.0
ESPERA_MAX = 30.0
# A Retry-After longer than this is not waited for: the last answer is returned as it is.
RETRY_AFTER_MAX = 120.0
_IDEMPOTENTES = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_REINTENTABLES = frozenset({429, 500, 502, 503, 504})
# Headers that describe the wire body; the body kept here is already decoded.
_CABECERAS_DEL_CUERPO = ("content-encoding", "content-length", "transfer-encoding")

__all__ = [
    "MAX_BYTES",
    "TIMEOUT",
    "USER_AGENT",
    "Cliente",
    "ClienteAsincrono",
    "RespuestaDemasiadoGrande",
    "cliente",
    "usar",
]


class RespuestaDemasiadoGrande(httpx.HTTPError):
    """The answer exceeded the client's maximum size."""


def espera_retry_after(valor: str | None, *, ahora: datetime | None = None) -> float | None:
    """Seconds asked by a ``Retry-After`` header (delta-seconds or HTTP date); None when absent or unreadable."""
    if not valor:
        return None
    valor = valor.strip()
    if valor.isdigit():
        return float(valor)
    try:
        cuando = parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None
    if cuando.tzinfo is None:
        cuando = cuando.replace(tzinfo=UTC)
    return max(0.0, (cuando - (ahora or datetime.now(UTC))).total_seconds())


class _Politica:
    """What the sync and async clients share: headers, waits, per-host pauses and the size limit."""

    def __init__(
        self,
        *,
        reintentos: int,
        espera_base: float,
        espera_max: float,
        pausa_host: float,
        max_bytes: int,
        reloj: Callable[[], float],
    ) -> None:
        self.reintentos = reintentos
        self.espera_base = espera_base
        self.espera_max = espera_max
        self.pausa_host = pausa_host
        self.max_bytes = max_bytes
        self._reloj = reloj
        self._ultima: dict[str, float] = {}
        self._cerrojo = threading.Lock()

    @staticmethod
    def cabeceras(accept: str | None, cabeceras: Mapping[str, str] | None) -> dict[str, str]:
        salida = {"User-Agent": USER_AGENT}
        if accept:
            salida["Accept"] = accept
        salida.update(cabeceras or {})
        return salida

    def reintenta(self, metodo: str, reintentar: bool | None) -> int:
        if reintentar is None:
            reintentar = metodo.upper() in _IDEMPOTENTES
        return self.reintentos if reintentar else 0

    def espera_del_turno(self, url: str, pausa: float | None) -> float:
        """Seconds to wait before hitting ``url``'s host, and book the slot."""
        pausa = self.pausa_host if pausa is None else pausa
        host = httpx.URL(url).host
        with self._cerrojo:
            ahora = self._reloj()
            turno = max(ahora, self._ultima.get(host, float("-inf")) + pausa)
            self._ultima[host] = turno
        return turno - ahora

    def espera_tras(self, intento: int, respuesta: httpx.Response | None) -> float | None:
        """Wait before attempt ``intento + 1``; None means "do not retry, give the answer back"."""
        if respuesta is not None:
            pedida = espera_retry_after(respuesta.headers.get("Retry-After"))
            if pedida is not None:
                return pedida if pedida <= RETRY_AFTER_MAX else None
        return min(self.espera_max, self.espera_base * 2**intento)

    def excede(self, recibidos: int, limite: int | None) -> bool:
        return recibidos > (self.max_bytes if limite is None else limite)


def _con_cuerpo(respuesta: httpx.Response, cuerpo: bytes) -> httpx.Response:
    cabeceras = [(k, v) for k, v in respuesta.headers.multi_items() if k.lower() not in _CABECERAS_DEL_CUERPO]
    nueva = httpx.Response(
        respuesta.status_code,
        headers=cabeceras,
        content=cuerpo,
        request=respuesta.request,
        history=respuesta.history,
        extensions=respuesta.extensions,
    )
    if respuesta.charset_encoding:
        nueva.encoding = respuesta.charset_encoding
    return nueva


def _observar(clave: str | None, respuesta: httpx.Response, contexto: str) -> None:
    if not clave or not respuesta.is_success:
        return
    from asistente.conocimiento import formatos  # lazy: the format watch is a knowledge-layer concern

    formatos.observar(clave, respuesta.content, contexto=contexto)


def _reintentable(respuesta: httpx.Response) -> bool:
    return respuesta.status_code in _REINTENTABLES


class Cliente:
    """Synchronous client. One instance keeps a connection pool, its cookies and its per-host pauses."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | float = TIMEOUT,
        reintentos: int = REINTENTOS,
        espera_base: float = ESPERA_BASE,
        espera_max: float = ESPERA_MAX,
        pausa_host: float = 0.0,
        max_bytes: int = MAX_BYTES,
        follow_redirects: bool = True,
        verify: bool | str = True,
        cabeceras: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._politica = _Politica(
            reintentos=reintentos,
            espera_base=espera_base,
            espera_max=espera_max,
            pausa_host=pausa_host,
            max_bytes=max_bytes,
            reloj=reloj,
        )
        self._dormir = dormir
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            headers=dict(cabeceras or {}),
            transport=transport,
        )

    @property
    def cookies(self) -> httpx.Cookies:
        return self._http.cookies

    def peticion(
        self,
        metodo: str,
        url: str,
        *,
        accept: str | None = None,
        cabeceras: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        content: bytes | str | None = None,
        json: Any = None,
        timeout: httpx.Timeout | float | None = None,
        reintentar: bool | None = None,
        pausa_host: float | None = None,
        max_bytes: int | None = None,
        observar: str | None = None,
        contexto: str = "",
    ) -> httpx.Response:
        """Send one request with the retry policy and return the last answer (any status; read in full).

        Network errors that survive the retries propagate (``httpx.TransportError``);
        a body over the limit raises :class:`RespuestaDemasiadoGrande`.
        """
        intentos = self._politica.reintenta(metodo, reintentar) + 1
        cabeceras_finales = self._politica.cabeceras(accept, cabeceras)
        for intento in range(intentos):
            espera = self._politica.espera_del_turno(url, pausa_host)
            if espera > 0:
                self._dormir(espera)
            ultimo = intento == intentos - 1
            try:
                respuesta = self._enviar(
                    metodo,
                    url,
                    cabeceras=cabeceras_finales,
                    params=params,
                    data=data,
                    content=content,
                    json=json,
                    timeout=timeout,
                    max_bytes=max_bytes,
                )
            except httpx.TransportError as exc:
                if ultimo:
                    raise
                logger.warning("HTTP %s %s failed (%d/%d): %s", metodo, url, intento + 1, intentos, exc)
                self._dormir(self._politica.espera_tras(intento, None) or 0.0)
                continue
            if ultimo or not _reintentable(respuesta):
                _observar(observar, respuesta, contexto)
                return respuesta
            espera_tras = self._politica.espera_tras(intento, respuesta)
            if espera_tras is None:
                return respuesta
            logger.warning("HTTP %s %s answered %d (%d/%d)", metodo, url, respuesta.status_code, intento + 1, intentos)
            self._dormir(espera_tras)
        raise AssertionError("unreachable")  # pragma: no cover

    def _enviar(
        self, metodo: str, url: str, *, cabeceras: Mapping[str, str], max_bytes: int | None, **kw: Any
    ) -> httpx.Response:
        extra = {k: v for k, v in kw.items() if v is not None}
        with self._http.stream(metodo, url, headers=cabeceras, **extra) as respuesta:
            partes: list[bytes] = []
            recibidos = 0
            for trozo in respuesta.iter_bytes():
                recibidos += len(trozo)
                if self._politica.excede(recibidos, max_bytes):
                    raise RespuestaDemasiadoGrande(f"La respuesta de {url} supera el tamaño máximo.")
                partes.append(trozo)
        return _con_cuerpo(respuesta, b"".join(partes))

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.peticion("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> httpx.Response:
        return self.peticion("POST", url, **kw)

    def obtener(self, url: str, **kw: Any) -> bytes | None:
        """GET ``url``'s body; None on 404; any other non-2xx raises ``httpx.HTTPStatusError``."""
        respuesta = self.get(url, **kw)
        if respuesta.status_code == 404:
            return None
        respuesta.raise_for_status()
        return respuesta.content

    def cerrar(self) -> None:
        self._http.close()

    def __enter__(self) -> Cliente:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cerrar()


class ClienteAsincrono:
    """The same rules as :class:`Cliente` over ``httpx.AsyncClient``."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | float = TIMEOUT,
        reintentos: int = REINTENTOS,
        espera_base: float = ESPERA_BASE,
        espera_max: float = ESPERA_MAX,
        pausa_host: float = 0.0,
        max_bytes: int = MAX_BYTES,
        follow_redirects: bool = True,
        verify: bool | str = True,
        cabeceras: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        dormir: Callable[[float], Any] = asyncio.sleep,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._politica = _Politica(
            reintentos=reintentos,
            espera_base=espera_base,
            espera_max=espera_max,
            pausa_host=pausa_host,
            max_bytes=max_bytes,
            reloj=reloj,
        )
        self._dormir = dormir
        self._http = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            headers=dict(cabeceras or {}),
            transport=transport,
        )

    async def peticion(
        self,
        metodo: str,
        url: str,
        *,
        accept: str | None = None,
        cabeceras: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        content: bytes | str | None = None,
        json: Any = None,
        timeout: httpx.Timeout | float | None = None,
        reintentar: bool | None = None,
        pausa_host: float | None = None,
        max_bytes: int | None = None,
        observar: str | None = None,
        contexto: str = "",
    ) -> httpx.Response:
        intentos = self._politica.reintenta(metodo, reintentar) + 1
        cabeceras_finales = self._politica.cabeceras(accept, cabeceras)
        extra = {
            k: v
            for k, v in {"params": params, "data": data, "content": content, "json": json, "timeout": timeout}.items()
            if v is not None
        }
        for intento in range(intentos):
            espera = self._politica.espera_del_turno(url, pausa_host)
            if espera > 0:
                await self._dormir(espera)
            ultimo = intento == intentos - 1
            try:
                async with self._http.stream(metodo, url, headers=cabeceras_finales, **extra) as respuesta:
                    partes: list[bytes] = []
                    recibidos = 0
                    async for trozo in respuesta.aiter_bytes():
                        recibidos += len(trozo)
                        if self._politica.excede(recibidos, max_bytes):
                            raise RespuestaDemasiadoGrande(f"La respuesta de {url} supera el tamaño máximo.")
                        partes.append(trozo)
                respuesta = _con_cuerpo(respuesta, b"".join(partes))
            except httpx.TransportError as exc:
                if ultimo:
                    raise
                logger.warning("HTTP %s %s failed (%d/%d): %s", metodo, url, intento + 1, intentos, exc)
                await self._dormir(self._politica.espera_tras(intento, None) or 0.0)
                continue
            if ultimo or not _reintentable(respuesta):
                _observar(observar, respuesta, contexto)
                return respuesta
            espera_tras = self._politica.espera_tras(intento, respuesta)
            if espera_tras is None:
                return respuesta
            await self._dormir(espera_tras)
        raise AssertionError("unreachable")  # pragma: no cover

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.peticion("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.peticion("POST", url, **kw)

    async def cerrar(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> ClienteAsincrono:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.cerrar()


_por_defecto: list[Cliente] = []
_cerrojo_defecto = threading.Lock()


def cliente() -> Cliente:
    """The process-wide client (pooled connections, shared per-host pauses)."""
    with _cerrojo_defecto:
        if not _por_defecto:
            _por_defecto.append(Cliente())
        return _por_defecto[0]


@contextmanager
def usar(nuevo: Cliente) -> Iterator[Cliente]:
    """Install ``nuevo`` as the process-wide client for the duration of the block (tests, fakes)."""
    with _cerrojo_defecto:
        anterior = list(_por_defecto)
        _por_defecto[:] = [nuevo]
    try:
        yield nuevo
    finally:
        with _cerrojo_defecto:
            _por_defecto[:] = anterior

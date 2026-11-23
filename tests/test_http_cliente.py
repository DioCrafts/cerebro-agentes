"""The shared HTTP client and the BOE operations on top of it. No network: ``httpx.MockTransport``."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
import json
from typing import Any

import httpx
import pytest

from asistente.conocimiento.boe import cliente as boe
from asistente.plataforma import http


def _cliente(handler: Any, esperas: list[float] | None = None, **kw: Any) -> http.Cliente:
    dormir = esperas.append if esperas is not None else (lambda _s: None)
    return http.Cliente(transport=httpx.MockTransport(handler), dormir=dormir, **kw)


def test_sends_the_project_user_agent_and_accept() -> None:
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, content=b"ok")

    respuesta = _cliente(handler).get("https://x.test/a", accept="application/json")
    assert respuesta.content == b"ok"
    assert vistas[0].headers["User-Agent"] == http.USER_AGENT
    assert vistas[0].headers["Accept"] == "application/json"


def test_retries_5xx_and_network_errors_with_growing_waits() -> None:
    respuestas: list[Any] = [httpx.ConnectError("caída"), httpx.Response(503), httpx.Response(200, content=b"bien")]
    esperas: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        siguiente = respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    respuesta = _cliente(handler, esperas, reintentos=2, espera_base=1.0).get("https://x.test/")
    assert respuesta.content == b"bien"
    assert esperas == [1.0, 2.0]


def test_honours_retry_after_on_429() -> None:
    respuestas = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    esperas: list[float] = []
    respuesta = _cliente(lambda r: respuestas.pop(0), esperas).get("https://x.test/")
    assert respuesta.status_code == 200
    assert esperas == [7.0]


def test_a_retry_after_too_long_returns_the_answer_without_waiting() -> None:
    esperas: list[float] = []
    respuesta = _cliente(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}), esperas).get("https://x.test/")
    assert respuesta.status_code == 429
    assert esperas == []


def test_retry_after_as_an_http_date() -> None:
    ahora = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert http.espera_retry_after("Thu, 01 Jan 2026 12:00:30 GMT", ahora=ahora) == 30.0
    assert http.espera_retry_after("basura") is None


def test_a_post_is_not_retried_unless_asked() -> None:
    llamadas: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(1)
        return httpx.Response(503)

    cliente = _cliente(handler)
    assert cliente.post("https://x.test/", content=b"x").status_code == 503
    assert len(llamadas) == 1
    cliente.post("https://x.test/", content=b"x", reintentar=True)
    assert len(llamadas) == 1 + 1 + http.REINTENTOS


def test_network_error_after_the_last_retry_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento")

    with pytest.raises(httpx.ReadTimeout):
        _cliente(handler).get("https://x.test/")


def test_minimum_pause_per_host() -> None:
    reloj = [100.0]
    esperas: list[float] = []
    cliente = http.Cliente(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        dormir=esperas.append,
        reloj=lambda: reloj[0],
        pausa_host=0.5,
    )
    cliente.get("https://a.test/1")
    cliente.get("https://a.test/2")
    cliente.get("https://b.test/1")
    assert esperas == [0.5]


def test_maximum_response_size() -> None:
    cliente = _cliente(lambda r: httpx.Response(200, content=b"x" * 100), max_bytes=10)
    with pytest.raises(http.RespuestaDemasiadoGrande):
        cliente.get("https://x.test/")


def test_obtener_is_none_on_404_and_raises_on_other_errors() -> None:
    cliente = _cliente(lambda r: httpx.Response(404 if r.url.path == "/no" else 403))
    assert cliente.obtener("https://x.test/no") is None
    with pytest.raises(httpx.HTTPStatusError):
        cliente.obtener("https://x.test/prohibido")


def test_gzip_bodies_arrive_decoded() -> None:
    import gzip

    cuerpo = gzip.compress("ñandú".encode())
    respuesta = _cliente(
        lambda r: httpx.Response(
            200, content=cuerpo, headers={"Content-Encoding": "gzip", "Content-Type": "text/plain; charset=utf-8"}
        )
    ).get("https://x.test/")
    assert respuesta.text == "ñandú"


def test_observar_hands_the_body_to_the_format_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.conocimiento import formatos

    vistas: list[tuple[str, Any, str]] = []
    monkeypatch.setattr(formatos, "observar", lambda clave, crudo, contexto="": vistas.append((clave, crudo, contexto)))
    _cliente(lambda r: httpx.Response(200, content=b"<html/>")).get(
        "https://x.test/", observar="boe_seed", contexto="id"
    )
    assert vistas == [("boe_seed", b"<html/>", "id")]


def test_async_client_applies_the_same_rules() -> None:
    respuestas = [httpx.Response(502), httpx.Response(200, content=b"ok")]
    esperas: list[float] = []

    async def dormir(segundos: float) -> None:
        esperas.append(segundos)

    async def principal() -> bytes:
        async with http.ClienteAsincrono(
            transport=httpx.MockTransport(lambda r: respuestas.pop(0)), dormir=dormir
        ) as cliente:
            respuesta = await cliente.get("https://x.test/")
            return respuesta.content

    assert asyncio.run(principal()) == b"ok"
    assert esperas == [1.0]


# --------------------------------------------------------------------------- #
# The BOE
# --------------------------------------------------------------------------- #


@pytest.fixture
def boe_falso(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    pedidas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidas.append(request)
        ruta = request.url.path
        if ruta.endswith("/metadatos"):
            return httpx.Response(
                200,
                json={
                    "status": {"code": "200"},
                    "data": [
                        {
                            "identificador": "BOE-A-2006-20764",
                            "fecha_actualizacion": "20250101T000000Z",
                            "titulo": "Ley 35/2006",
                        }
                    ],
                },
            )
        if ruta.endswith("/sumario/20260105"):
            return httpx.Response(404)
        if "/sumario/" in ruta:
            return httpx.Response(200, json={"data": {"sumario": {}}})
        if ruta.endswith("xml.php"):
            return httpx.Response(200, content=b"<documento/>")
        if ruta.endswith("act.php"):
            return httpx.Response(200, content=b"<html>ley</html>")
        return httpx.Response(404)

    monkeypatch.setattr(http, "_por_defecto", [_cliente(handler)])
    return pedidas


def test_boe_operations(boe_falso: list[httpx.Request]) -> None:
    assert boe.metadatos("BOE-A-2006-20764")["fecha_actualizacion"] == "20250101T000000Z"
    assert boe.texto_consolidado("BOE-A-2006-20764") == b"<html>ley</html>"
    assert boe.sumario(date(2026, 1, 5)) is None
    assert boe.sumario(date(2026, 1, 6)) == {"data": {"sumario": {}}}
    assert boe.xml("BOE-A-2026-1") == b"<documento/>"
    assert (
        str(boe_falso[0].url)
        == "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-2006-20764/metadatos"
    )
    assert all(r.url.host == "www.boe.es" for r in boe_falso)


def test_boe_downloads_only_from_the_boe(boe_falso: list[httpx.Request]) -> None:
    with pytest.raises(ValueError):
        boe.descargar("https://evil.example.com/", "text/html")
    assert boe_falso == []


def test_boe_summary_that_is_not_json_is_reported(
    boe_falso: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    from asistente.conocimiento import formatos

    monkeypatch.setattr(http, "_por_defecto", [_cliente(lambda r: httpx.Response(200, content=b"<html>error</html>"))])
    vistas: list[str] = []
    monkeypatch.setattr(formatos, "observar", lambda clave, *a, **k: vistas.append(clave))
    with pytest.raises(json.JSONDecodeError):
        boe.sumario(date(2026, 1, 6))
    assert vistas == ["boe_sumario"]

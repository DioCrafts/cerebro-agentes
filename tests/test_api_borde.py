"""The API's edge: Host allow-list, cross-site and non-JSON writes, the start-up guard, /metrics labels and
non-ASCII keys (docs/seguridad.md)."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from asistente.plataforma import settings
from asistente.plataforma.api import observability, security
from asistente.plataforma.api.api import app

CASO = {"user_id": "u", "jurisdiction": "ES", "tax_period": "2025"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    return TestClient(app)


def test_host_ajeno_rechazado(client: TestClient) -> None:
    assert client.get("/health", headers={"Host": "atacante.example"}).status_code == 400
    assert client.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_hosts_por_defecto_solo_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_ALLOWED_HOSTS")
    assert security.hosts_permitidos() == ["localhost", "127.0.0.1"]
    monkeypatch.setenv("API_ALLOWED_HOSTS", "api.example.com, *.example.org")
    assert security.hosts_permitidos() == ["api.example.com", "*.example.org"]


def test_escritura_desde_otro_origen_rechazada(client: TestClient) -> None:
    r = client.post("/cases", json=CASO, headers={"Origin": "https://atacante.example"})
    assert r.status_code == 403
    assert client.post("/cases", json=CASO, headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert client.post("/cases", json=CASO, headers={"Origin": "http://testserver"}).status_code == 200
    # Reads are not writes: CORS (no Access-Control-Allow-Origin) already keeps their answers from the page.
    assert client.get("/health", headers={"Origin": "https://atacante.example"}).status_code == 200


def test_origen_adicional_por_variable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_ALLOWED_ORIGINS", "https://app.example.com")
    assert client.post("/cases", json=CASO, headers={"Origin": "https://app.example.com"}).status_code == 200


@pytest.mark.parametrize("tipo", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""])
def test_escritura_con_cuerpo_no_json_rechazada(client: TestClient, tipo: str) -> None:
    import json

    r = client.post("/cases", content=json.dumps(CASO).encode(), headers={"Content-Type": tipo})
    assert r.status_code == 415


def test_escritura_sin_cuerpo_y_json_con_charset_pasan(client: TestClient) -> None:
    import json

    r = client.post(
        "/cases", content=json.dumps(CASO).encode(), headers={"Content-Type": "application/json; charset=utf-8"}
    )
    assert r.status_code == 200


def test_no_arranca_sin_autenticacion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    monkeypatch.delenv("PERMITIR_SIN_AUTENTICACION", raising=False)
    with pytest.raises(security.ArranqueInseguroError):
        with TestClient(app):
            pass
    monkeypatch.setenv("PERMITIR_SIN_AUTENTICACION", "1")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
    monkeypatch.delenv("PERMITIR_SIN_AUTENTICACION")
    monkeypatch.setenv("HACIENDA_API_KEY", "k")
    security.comprobar_arranque()
    monkeypatch.delenv("HACIENDA_API_KEY")
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    security.comprobar_arranque()


def test_limite_de_peticiones_activo_por_defecto() -> None:
    import importlib
    import os

    anterior = os.environ.pop("API_RATE_LIMIT_PER_MIN", None)
    try:
        assert importlib.reload(settings).API_RATE_LIMIT_PER_MIN > 0
    finally:
        if anterior is not None:
            os.environ["API_RATE_LIMIT_PER_MIN"] = anterior
        importlib.reload(settings)


def test_clave_no_ascii_no_da_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HACIENDA_API_KEY", "contraseña")
    assert client.post("/cases", json=CASO, headers={"X-API-Key": "otra"}).status_code == 401
    # httpx sends header values as Latin-1/UTF-8 bytes; the server decodes them as Latin-1.
    assert security._iguales("contraseña", "contraseña")
    assert not security._iguales("contraseña", "contrasena")
    monkeypatch.setenv("HACIENDA_API_KEY", "s3cret")
    r = client.post("/cases", json=CASO, headers={"X-API-Key": "señal".encode()})
    assert r.status_code == 401
    monkeypatch.setenv("HACIENDA_OPERATOR_KEY", "op")
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    r = client.get("/metrics", headers={"X-API-Key": "s3cret", "X-Operator-Key": "señal".encode()})
    assert r.status_code == 404
    assert client.get("/metrics", headers={"X-API-Key": "s3cret", "X-Operator-Key": "op"}).status_code == 200


def test_metricas_sin_rutas_arbitrarias_y_escapadas(client: TestClient) -> None:
    client.get("/no-existe/%22%7D%0Afalsa_metrica%201")
    client.get("/otra-que-no-existe")
    texto = app.state.api_metrics.prometheus_text()
    assert "no-existe" not in texto and "falsa_metrica" not in texto
    assert f'route="{observability.RUTA_SIN_PLANTILLA}"' in texto
    metricas = observability.ApiMetrics()
    metricas.record("GET", 'a"b\\c\nd', 200, 1.0)
    assert 'route="a\\"b\\\\c\\nd"' in metricas.prometheus_text()

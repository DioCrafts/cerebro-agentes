"""The session cookie (``__Host-sesion``: HttpOnly, Secure, SameSite=Strict, Path=/) and the CSRF lock on writes.

The web client gets the cookie and never the token; a client without a browser (no ``Origin``) still gets the token
and uses ``Authorization: Bearer``. A write authenticated by the cookie needs an allowed ``Origin`` or the
``X-Requested-With`` header."""

from __future__ import annotations

import hashlib
import sqlite3

from fastapi.testclient import TestClient
import pytest

from asistente.plataforma.api.api import app
from asistente.plataforma.auth.deps import COOKIE_SESION
from .conftest import CONSENTS, PASSWORD, Env

ORIGEN = "https://testserver"


@pytest.fixture
def navegador(env: Env) -> TestClient:
    """A client that behaves like the browser: HTTPS (so the Secure cookie is kept and sent) and its own jar."""
    return TestClient(app, base_url=ORIGEN)


def _entrar(navegador: TestClient, env: Env) -> str:
    env.register("ana@example.com")
    respuesta = navegador.post(
        "/auth/login", json={"email": "ana@example.com", "password": PASSWORD}, headers={"Origin": ORIGEN}
    )
    assert respuesta.status_code == 200, respuesta.text
    token = navegador.cookies.get(COOKIE_SESION)
    assert token
    return token


def test_login_from_the_browser_sets_a_locked_cookie_and_never_shows_the_token(navegador: TestClient, env: Env) -> None:
    env.register("ana@example.com")
    respuesta = navegador.post(
        "/auth/login", json={"email": "ana@example.com", "password": PASSWORD}, headers={"Origin": ORIGEN}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["token"] is None and respuesta.json()["user"]["email"] == "ana@example.com"
    cabecera = respuesta.headers["set-cookie"]
    atributos = {a.strip().split("=")[0].lower() for a in cabecera.split(";")}
    assert cabecera.startswith(f"{COOKIE_SESION}=")
    assert {"httponly", "secure", "path", "max-age"} <= atributos and "domain" not in atributos
    assert "samesite=strict" in cabecera.lower() and "path=/" in cabecera.lower()
    # The server keeps only the SHA-256 of what the cookie carries.
    token = navegador.cookies.get(COOKIE_SESION)
    with sqlite3.connect(env.db_path) as conn:
        hashes = {fila[0] for fila in conn.execute("SELECT token_hash FROM auth_sessions")}
    assert hashlib.sha256(token.encode()).hexdigest() in hashes and token not in hashes


def test_registering_from_the_browser_also_signs_in_with_the_cookie(navegador: TestClient) -> None:
    respuesta = navegador.post(
        "/auth/register",
        json={"email": "eva@example.com", "password": PASSWORD, "consents": CONSENTS},
        headers={"Origin": ORIGEN},
    )
    assert respuesta.status_code == 201 and respuesta.json()["token"] is None
    assert navegador.get("/auth/me").json()["email"] == "eva@example.com"


def test_a_client_without_a_browser_still_gets_the_token_for_bearer(env: Env) -> None:
    cabeceras = env.register("ana@example.com")
    assert cabeceras["_token"]
    assert env.client.get("/auth/me", headers={"Authorization": cabeceras["Authorization"]}).status_code == 200


def test_the_cookie_authenticates_reads_and_same_origin_writes(navegador: TestClient, env: Env) -> None:
    _entrar(navegador, env)
    assert navegador.get("/auth/me").json()["email"] == "ana@example.com"
    creado = navegador.post("/cases", json={"user_id": "x", "tax_period": "2025"}, headers={"Origin": ORIGEN})
    assert creado.status_code == 200, creado.text
    assert navegador.get(f"/cases/{creado.json()['case_id']}").status_code == 200


def test_csrf_a_cookie_write_needs_our_origin_or_our_header(navegador: TestClient, env: Env) -> None:
    _entrar(navegador, env)
    cuerpo = {"user_id": "x", "tax_period": "2025"}
    # Another site's page (an old browser that still attaches the cookie, or a sibling subdomain).
    ajeno = navegador.post("/cases", json=cuerpo, headers={"Origin": "https://malo.example"})
    assert ajeno.status_code == 403
    # No Origin and no header of ours: refused while the cookie would authenticate it.
    assert navegador.post("/cases", json=cuerpo).status_code == 403
    assert navegador.post("/cases", json=cuerpo, headers={"X-Requested-With": "cerebro"}).status_code == 200
    # Reads are not writes, and a Bearer client without a browser is not affected.
    assert navegador.get("/auth/me").status_code == 200
    bearer = env.register("bea@example.com")
    sin_cookie = TestClient(app)
    assert sin_cookie.post("/cases", json=cuerpo, headers={"Authorization": bearer["Authorization"]}).status_code == 200


def test_logout_revokes_the_session_and_deletes_the_cookie(navegador: TestClient, env: Env) -> None:
    token = _entrar(navegador, env)
    salida = navegador.post("/auth/logout", headers={"Origin": ORIGEN})
    assert salida.status_code == 204
    cabecera = salida.headers["set-cookie"].lower()
    assert cabecera.startswith(f"{COOKIE_SESION.lower()}=") and "max-age=0" in cabecera
    assert navegador.cookies.get(COOKIE_SESION) is None
    # The old token is dead even if someone kept a copy of the cookie.
    viejo = TestClient(app, base_url=ORIGEN, cookies={COOKIE_SESION: token})
    assert viejo.get("/auth/me").status_code == 401


def test_deleting_the_account_deletes_the_cookie(navegador: TestClient, env: Env) -> None:
    _entrar(navegador, env)
    borrado = navegador.request("DELETE", "/me", json={"password": PASSWORD}, headers={"Origin": ORIGEN})
    assert borrado.status_code == 204, borrado.text
    assert "max-age=0" in borrado.headers["set-cookie"].lower()

"""API of the mail module with accounts on: connect, callback, sync, proposals the owner confirms, privacy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from asistente.capacidades.correo import almacen
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.modulos import registro
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.api.api import app, get_case_store, get_notification_store
from asistente.plataforma.api.routers.auth import get_login_limiter
from asistente.plataforma.auth.deps import get_auth_store
from asistente.plataforma.auth.ratelimit import LoginRateLimiter
from asistente.plataforma.auth.store import AuthStore
from tests.modulos.correo import corpus
from tests.modulos.correo.falso import REFRESCO, GoogleFalso

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}


@dataclass
class Env:
    client: TestClient
    cases: SQLiteCaseStateStore
    db_path: str

    def register(self, email: str) -> dict[str, str]:
        r = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def new_case(self, headers: dict[str, str]) -> str:
        r = self.client.post("/cases", json={"user_id": "x", "tax_period": "2026"}, headers=headers)
        assert r.status_code == 200, r.text
        return str(r.json()["case_id"])

    def activar(self, headers: dict[str, str], *claves: str) -> None:
        for clave in claves:
            assert self.client.put(f"/me/modulos/{clave}", json={"activo": True}, headers=headers).status_code == 200


@pytest.fixture
def env(db_path: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    registro.reiniciar()
    cases = SQLiteCaseStateStore(db_path)
    auth, avisos = AuthStore(db_path), NotificationStore(db_path)
    overrides = {
        get_auth_store: lambda: auth,
        get_case_store: lambda: cases,
        get_notification_store: lambda: avisos,
        get_login_limiter: lambda: LoginRateLimiter(50, 900),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield Env(client=TestClient(app), cases=cases, db_path=db_path)
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
        registro.reiniciar()


def _conectar(env: Env, google: GoogleFalso, headers: dict[str, str], case_id: str) -> None:
    r = env.client.post("/correo/conexion", json={"case_id": case_id}, headers=headers)
    assert r.status_code == 200, r.text
    estado, codigo = google.autorizar(r.json()["url"])
    callback = env.client.get(
        "/correo/oauth/callback", params={"state": estado, "code": codigo}, follow_redirects=False
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "http://127.0.0.1:5173/?correo=conectado"


def test_flujo_completo(env: Env, google: GoogleFalso) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    estado = env.client.get("/correo/estado", params={"case_id": case_id}, headers=ana).json()
    assert estado["configurado"] and not estado["activo"] and not estado["conectado"] and estado["ventana_dias"] == 30
    assert env.client.post("/correo/conexion", json={"case_id": case_id}, headers=ana).status_code == 409
    env.activar(ana, "correo")
    _conectar(env, google, ana, case_id)
    estado = env.client.get("/correo/estado", params={"case_id": case_id}, headers=ana).json()
    assert estado["conectado"] and estado["cuenta"] == "yo@gmail.com"
    assert estado["alcance"] == ["https://www.googleapis.com/auth/gmail.readonly"]

    google.mensajes = corpus.todos()
    r = env.client.post("/correo/sincronizar", json={"case_id": case_id}, headers=ana)
    assert r.status_code == 200 and r.json()["nuevos"] == 10 and r.json()["propuestas"] == 4
    resumen = env.client.get("/correo/resumen", params={"case_id": case_id, "periodo": "semana"}, headers=ana).json()
    secciones = {s["clave"]: [e["id"] for e in s["elementos"]] for s in resumen["secciones"]}
    assert secciones["acciones"] == ["m006accion", "m001aeat"] and "m008phish" in secciones["phishing"]

    propuestas = {
        p["elemento_id"]: p
        for p in env.client.get("/correo/propuestas", params={"case_id": case_id}, headers=ana).json()["propuestas"]
    }
    compra, luz = propuestas["m003compra"], propuestas["m002luz"]
    # The destination module must be on: nothing is written otherwise.
    r = env.client.post(f"/correo/propuestas/{compra['id']}/confirmar", headers=ana)
    assert r.status_code == 409 and "Caducidades" in r.json()["detail"]
    env.activar(ana, "caducidades", "luz")
    r = env.client.post(f"/correo/propuestas/{compra['id']}/confirmar", headers=ana)
    assert r.status_code == 200 and r.json()["estado"] == "confirmada", r.text
    assert env.client.post(f"/correo/propuestas/{compra['id']}/confirmar", headers=ana).status_code == 409
    assert env.client.post(f"/correo/propuestas/{luz['id']}/confirmar", headers=ana).status_code == 200
    case = env.cases.get_case(case_id)
    hechos = {f.name: f for f in case.facts}  # type: ignore[union-attr]
    compras = json.loads(str(hechos["caducidades_compras"].value))
    assert compras[0]["importe"] in ("129.99", 129.99) and compras[0]["modalidad"] == "distancia"
    assert hechos["caducidades_compras"].source == "correo:m003compra"
    assert hechos["luz_potencia_p1_kw"].value == 4.6
    cargo = propuestas["m004cargo"]
    assert env.client.post(f"/correo/propuestas/{cargo['id']}/rechazar", headers=ana).json()["estado"] == "rechazada"
    assert "luz_potencia_p1_kw" not in {f.name for f in case.facts if f.source.startswith("correo:m004")}  # type: ignore[union-attr]

    r = env.client.post(
        "/correo/silenciar", json={"case_id": case_id, "tipo": "categoria", "valor": "accion"}, headers=ana
    )
    assert r.status_code == 200 and r.json()["ambito"] == "correo:accion"
    assert (
        env.client.post(
            "/correo/silenciar", json={"case_id": case_id, "tipo": "categoria", "valor": "nada"}, headers=ana
        ).status_code
        == 422
    )

    exportado = env.client.get("/correo/exportar", params={"case_id": case_id}, headers=ana).json()
    assert len(exportado["elementos"]) == 10 and exportado["conexion"]["cuenta"] == "yo@gmail.com"
    assert "token_cifrado" not in json.dumps(exportado) and REFRESCO not in json.dumps(exportado)

    r = env.client.post("/correo/desconectar", json={"case_id": case_id, "borrar_datos": True}, headers=ana)
    assert r.json()["revocado_en_google"] is True and REFRESCO in google.revocados
    assert almacen.elementos(case_id) == [] and almacen.conexion_de(case_id) is None


def test_otro_usuario_no_ve_ni_decide(env: Env, google: GoogleFalso) -> None:
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    case_id = env.new_case(ana)
    env.activar(ana, "correo")
    env.activar(luis, "correo")
    _conectar(env, google, ana, case_id)
    google.mensajes = corpus.todos()
    env.client.post("/correo/sincronizar", json={"case_id": case_id}, headers=ana)
    propuesta = almacen.propuestas(case_id)[0]
    assert env.client.get("/correo/resumen", params={"case_id": case_id}, headers=luis).status_code == 404
    assert env.client.post(f"/correo/propuestas/{propuesta.id}/confirmar", headers=luis).status_code == 404
    assert env.client.post("/correo/conexion", json={"case_id": case_id}, headers=luis).status_code == 404
    assert env.client.post("/correo/desconectar", json={"case_id": case_id}, headers=luis).status_code == 404
    assert env.client.get("/correo/estado", params={"case_id": case_id}).status_code == 401


def test_callback_con_estado_falso_o_cancelado(env: Env, google: GoogleFalso) -> None:
    r = env.client.get("/correo/oauth/callback", params={"state": "inventado", "code": "x"}, follow_redirects=False)
    assert r.headers["location"] == "http://127.0.0.1:5173/?correo=error"
    r = env.client.get(
        "/correo/oauth/callback", params={"error": "access_denied", "state": "s"}, follow_redirects=False
    )
    assert r.headers["location"] == "http://127.0.0.1:5173/?correo=cancelado"
    assert env.client.get("/correo/oauth/callback", params={"code": "x"}, follow_redirects=False).status_code == 401


def test_borrar_la_cuenta_borra_todo_lo_del_correo(env: Env, google: GoogleFalso) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    env.activar(ana, "correo")
    _conectar(env, google, ana, case_id)
    google.mensajes = corpus.todos()
    env.client.post("/correo/sincronizar", json={"case_id": case_id}, headers=ana)
    env.client.post(
        "/correo/contactos", json={"case_id": case_id, "direccion": "secretaria@colegio-ejemplo.es"}, headers=ana
    )
    r = env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana)
    assert r.status_code == 204, r.text
    with sqlite3.connect(env.db_path) as conn:
        for tabla in (
            "correo_conexiones",
            "correo_elementos",
            "correo_propuestas",
            "correo_contactos",
            "correo_oauth_estados",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0, tabla  # noqa: S608


def test_contactos_fijados_guardan_la_direccion_completa(env: Env, google: GoogleFalso) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    env.activar(ana, "correo")
    _conectar(env, google, ana, case_id)
    r = env.client.post(
        "/correo/contactos", json={"case_id": case_id, "direccion": "Secretaria@Colegio-Ejemplo.es"}, headers=ana
    )
    assert r.json()["contactos"] == ["secretaria@colegio-ejemplo.es"]
    google.mensajes = [corpus.accion(), corpus.aeat()]
    env.client.post("/correo/sincronizar", json={"case_id": case_id}, headers=ana)
    por_id = {e.id: e for e in almacen.elementos(case_id)}
    assert por_id["m006accion"].remitente == "secretaria@colegio-ejemplo.es" and por_id["m001aeat"].remitente is None
    env.client.delete(
        "/correo/contactos", params={"case_id": case_id, "direccion": "secretaria@colegio-ejemplo.es"}, headers=ana
    )
    assert almacen.elemento(case_id, "m006accion").remitente is None  # type: ignore[union-attr]


def test_url_de_consentimiento_desde_la_api(env: Env, google: GoogleFalso) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    env.activar(ana, "correo")
    url = env.client.post("/correo/conexion", json={"case_id": case_id}, headers=ana).json()["url"]
    params = parse_qs(urlsplit(url).query)
    assert params["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"] and params["prompt"] == ["consent"]

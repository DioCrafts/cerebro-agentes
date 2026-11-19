"""The app's side: generate a pairing code, see the channel and the paired chats, pause, unpair — owner-only — and
the account's export and erasure."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
import sqlite3

from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.api.api import app, get_case_store, get_notification_store
from asistente.plataforma.api.routers.auth import get_login_limiter
from asistente.plataforma.auth.deps import get_auth_store
from asistente.plataforma.auth.ratelimit import LoginRateLimiter
from asistente.plataforma.auth.store import AuthStore
from asistente.plataforma.mensajeria.almacen import almacen
from asistente.plataforma.mensajeria.emparejamiento import TEXTO_BIENVENIDA, normalizar
from asistente.plataforma.mensajeria.formato import a_canal
from asistente.plataforma.mensajeria.pasarela import TEXTO_DESPEDIDA_APP, Pasarela
from asistente.plataforma.mensajeria.prueba import CanalPrueba

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}


@dataclass
class Env:
    client: TestClient
    db: str
    cases: SQLiteCaseStateStore
    canal: CanalPrueba
    pasarela: Pasarela

    def register(self, email: str) -> dict:
        response = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert response.status_code == 201, response.text
        body = response.json()
        return {"Authorization": f"Bearer {body['token']}", "_user_id": body["user"]["id"]}

    def new_case(self, headers: dict) -> str:
        response = self.client.post("/cases", json={"user_id": "x", "tax_period": "2026"}, headers=headers)
        assert response.status_code == 200, response.text
        return str(response.json()["case_id"])

    def emparejar(self, headers: dict, case_id: str, contacto: str = "c1") -> dict:
        respuesta = self.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers=headers)
        assert respuesta.status_code == 201, respuesta.text
        self.canal.recibir(contacto, f"Mi código: {respuesta.json()['codigo']}")
        assert self.pasarela.esperar(10)
        return dict(respuesta.json())


@pytest.fixture
def env(tmp_path, monkeypatch: pytest.MonkeyPatch, entorno_mensajeria: None) -> Iterator[Env]:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    db = str(tmp_path / "state.sqlite3")
    auth, cases, notifications = AuthStore(db), SQLiteCaseStateStore(db), NotificationStore(db)
    overrides = {
        get_auth_store: lambda: auth,
        get_case_store: lambda: cases,
        get_notification_store: lambda: notifications,
        get_login_limiter: lambda: LoginRateLimiter(50, 900),
    }
    app.dependency_overrides.update(overrides)
    canal = CanalPrueba()
    pasarela = Pasarela(db, canales=[canal], agente=lambda: None)
    canal.iniciar_en_hilo(pasarela.manejar)
    try:
        yield Env(client=TestClient(app), db=db, cases=cases, canal=canal, pasarela=pasarela)
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
        canal.parar()
        pasarela.cerrar(5)
        auth.close()
        cases.close()
        notifications.close()


def test_the_owner_generates_a_code_that_pairs_a_chat_to_their_case(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    estado = env.client.get("/mensajeria/estado", headers=ana).json()
    assert estado["activa"] and estado["dispositivos"] == 0 and estado["codigo_caduca"] is None
    (canal,) = estado["canales"]
    assert canal["canal"] == "prueba" and not canal["conectado"] and canal["direccion"] is None

    # The worker publishes the channel's state (the bot's address) in the database.
    almacen(env.db).publicar_estado(env.canal.estado(), datetime.now(UTC))
    respuesta = env.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers=ana)
    assert respuesta.status_code == 201, respuesta.text
    codigo = respuesta.json()
    assert normalizar(codigo["codigo"]) is not None and 590 <= codigo["segundos"] <= 600
    assert codigo["direccion"] == env.canal.direccion and codigo["conectado"] is True
    assert "Caduca en 10 minutos" in codigo["instrucciones"]
    assert env.client.get("/mensajeria/estado", headers=ana).json()["codigo_caduca"] is not None

    env.canal.recibir("c1", codigo["codigo"], nombre_mostrado="Ana")
    assert env.pasarela.esperar(10)
    assert env.canal.textos("c1") == [a_canal(TEXTO_BIENVENIDA, "simplex")]
    vinculo = almacen(env.db).vinculo_de("prueba", "c1")
    assert vinculo is not None and vinculo.user_id == ana["_user_id"] and vinculo.case_id == case_id
    (dispositivo,) = env.client.get("/mensajeria/dispositivos", headers=ana).json()["dispositivos"]
    assert dispositivo["id"] == vinculo.id and dispositivo["nombre_mostrado"] == "Ana"
    assert dispositivo["nombre_canal"] == "Canal de prueba" and dispositivo["avisos_en_pausa"] is False
    assert "contacto" not in dispositivo  # the channel's id of the contact is not shown in the app
    assert env.client.get("/mensajeria/estado", headers=ana).json()["codigo_caduca"] is None  # spent


def test_another_users_case_and_devices_read_as_missing(env: Env) -> None:
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    caso_ana = env.new_case(ana)
    env.emparejar(ana, caso_ana)
    (dispositivo,) = env.client.get("/mensajeria/dispositivos", headers=ana).json()["dispositivos"]

    theirs = env.client.post("/mensajeria/emparejar", json={"case_id": caso_ana}, headers=luis)
    missing = env.client.post("/mensajeria/emparejar", json={"case_id": "case_nope"}, headers=luis)
    assert theirs.status_code == missing.status_code == 404 and theirs.json() == missing.json()
    assert env.client.get("/mensajeria/dispositivos", headers=luis).json()["dispositivos"] == []
    assert env.client.get("/mensajeria/estado", headers=luis).json()["dispositivos"] == 0
    for metodo, ruta, cuerpo in (
        ("put", f"/mensajeria/dispositivos/{dispositivo['id']}/pausa", {"pausado": True}),
        ("delete", f"/mensajeria/dispositivos/{dispositivo['id']}", None),
    ):
        ajeno = env.client.request(metodo, ruta, json=cuerpo, headers=luis)
        inventado = env.client.request(metodo, ruta.replace(dispositivo["id"], "vin_nope"), json=cuerpo, headers=luis)
        assert ajeno.status_code == inventado.status_code == 404 and ajeno.json() == inventado.json()
        assert env.client.request(metodo, ruta, json=cuerpo).status_code == 401
    # Nothing of ana's changed.
    (sigue,) = env.client.get("/mensajeria/dispositivos", headers=ana).json()["dispositivos"]
    assert sigue == dispositivo


def test_pause_and_unpair_from_the_app(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    env.emparejar(ana, case_id)
    (dispositivo,) = env.client.get("/mensajeria/dispositivos", headers=ana).json()["dispositivos"]
    ruta = f"/mensajeria/dispositivos/{dispositivo['id']}"

    pausado = env.client.put(f"{ruta}/pausa", json={"pausado": True}, headers=ana)
    assert pausado.status_code == 200 and pausado.json()["avisos_en_pausa"] is True

    assert env.client.delete(ruta, headers=ana).status_code == 204
    assert env.client.get("/mensajeria/dispositivos", headers=ana).json()["dispositivos"] == []
    assert env.client.delete(ruta, headers=ana).status_code == 404
    eventos = [e for e in env.cases.list_audit_events(case_id) if e.get("accion") == "desemparejado"]
    assert eventos[0]["motivo"] == "app"
    # The channel runs in the worker: it says goodbye there and removes the contact.
    assert env.pasarela.procesar_desconexiones() == 1
    assert env.canal.textos("c1")[-1] == TEXTO_DESPEDIDA_APP and env.canal.desconectados == ["c1"]


def test_limits_on_codes_and_devices(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    assert (
        env.client.post(
            "/mensajeria/emparejar", json={"case_id": case_id, "canal": "telegram"}, headers=ana
        ).status_code
        == 422
    )
    env.emparejar(ana, case_id)
    lleno = env.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers=ana)
    assert lleno.status_code == 409 and "Desvincula uno" in lleno.json()["detail"]

    monkeypatch.setattr(settings, "MENSAJERIA_CONTACTOS_POR_USUARIO", 5)
    monkeypatch.setattr(settings, "MENSAJERIA_CODIGOS_POR_HORA", 2)
    assert env.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers=ana).status_code == 201
    demasiados = env.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers=ana)
    assert demasiados.status_code == 429 and demasiados.headers["retry-after"] == "600"

    monkeypatch.setattr(settings, "MENSAJERIA_ACTIVA", False)
    apagada = env.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers=ana)
    assert apagada.status_code == 503
    assert env.client.get("/mensajeria/estado", headers=ana).json()["activa"] is False


def test_export_has_the_chats_and_deliveries_never_a_code_and_erasure_removes_them(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    codigo = env.emparejar(ana, case_id)["codigo"]
    vinculo = almacen(env.db).vinculo_de("prueba", "c1")
    assert vinculo is not None
    almacen(env.db).terminar_entrega(vinculo.id, "notif-1", "entregada", datetime.now(UTC))
    pendiente = env.client.post("/mensajeria/emparejar", json={"case_id": case_id}, headers={**ana})
    assert pendiente.status_code == 409  # one chat per user: no extra code lying around

    exportado = env.client.get("/me/export", headers=ana).json()
    (chat,) = exportado["mensajeria"]["chats_vinculados"]
    assert chat["id"] == vinculo.id and chat["contacto"] == "c1" and chat["case_id"] == case_id
    assert chat["avisos_entregados"][0]["notification_id"] == "notif-1"
    texto = str(exportado)
    assert codigo not in texto and str(normalizar(codigo)) not in texto and "huella" not in texto

    assert env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    with sqlite3.connect(env.db) as conn:
        for tabla in ("mensajeria_vinculos", "mensajeria_codigos", "mensajeria_entregas", "mensajeria_confirmaciones"):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0, tabla
    (pendiente_borrado,) = almacen(env.db).desconexiones()
    assert pendiente_borrado.motivo == "borrado" and pendiente_borrado.contacto == "c1"
    assert env.pasarela.procesar_desconexiones() == 1 and env.canal.desconectados == ["c1"]
    assert env.canal.textos("c1")[-1] == a_canal(TEXTO_BIENVENIDA, "simplex")  # no goodbye after an erasure


def test_without_accounts_the_operator_pairs(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    case_id = env.client.post("/cases", json={"user_id": "yo", "tax_period": "2026"}).json()["case_id"]
    codigo = env.client.post("/mensajeria/emparejar", json={"case_id": case_id}).json()["codigo"]
    env.canal.recibir("c9", codigo)
    assert env.pasarela.esperar(10)
    (dispositivo,) = env.client.get("/mensajeria/dispositivos").json()["dispositivos"]
    assert dispositivo["case_id"] == case_id

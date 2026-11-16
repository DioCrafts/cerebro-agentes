"""Web Push: router, store, opt-in, generic payload, delivery channel and erasure with the account.

No real push service is contacted: ``transporte_http`` is replaced by a fake that records each request and
the test decrypts it with the browser's key, as a device would.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import sqlite3

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
import pytest

from hacienda_gpt import settings
from hacienda_gpt.api.api import app, get_case_store, get_notification_store
from hacienda_gpt.api.routers import push as push_router
from hacienda_gpt.api.routers.auth import get_login_limiter
from hacienda_gpt.auth.deps import get_auth_store
from hacienda_gpt.auth.ratelimit import LoginRateLimiter
from hacienda_gpt.auth.store import AuthStore
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.proactive import entrega
from hacienda_gpt.proactive.entrega import ConfiguracionCanalError, EntregaStore, entregar_pendientes
from hacienda_gpt.proactive.store import Notification, NotificationStore
from hacienda_gpt.push import envio
from hacienda_gpt.push.almacen import OPERADOR, PushStore
from hacienda_gpt.push.cifrado import b64url_encode, descifrar, publica_cruda
from hacienda_gpt.push.envio import (
    TEXTO_AVISO,
    TEXTO_OPERADOR,
    TEXTO_PRUEBA,
    TEXTO_URGENTE,
    TITULO,
    EndpointNoPermitidoError,
    PushCanal,
    avisar_al_escribir,
    validar_endpoint,
)
from hacienda_gpt.push.vapid import claves_desde_entorno, generar_claves, verificar_jwt

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}
FCM = "https://fcm.googleapis.com/fcm/send/"
# Personal data a notice may carry: none of it may reach a push service.
PERSONAL = ("1.234,56", "Juan", "12345678Z", "devolución", "IRPF 2025")


@dataclass
class Dispositivo:
    endpoint: str
    privada: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    auth: bytes = b"0123456789abcdef"

    def claves(self) -> dict[str, str]:
        return {"p256dh": b64url_encode(publica_cruda(self.privada.public_key())), "auth": b64url_encode(self.auth)}

    def suscripcion(self) -> dict:
        return {"endpoint": self.endpoint, "claves": self.claves(), "consentimiento": True}


@dataclass
class ServicioFalso:
    """The push service: records the requests; ``estado`` per endpoint (default 201)."""

    peticiones: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)
    estado: dict[str, int] = field(default_factory=dict)

    def __call__(self, url: str, cuerpo: bytes, cabeceras) -> int:
        self.peticiones.append((url, cuerpo, dict(cabeceras)))
        return self.estado.get(url, 201)

    def recibido(self, dispositivo: Dispositivo) -> list[dict[str, str]]:
        return [
            json.loads(descifrar(dispositivo.privada, dispositivo.auth, cuerpo))
            for url, cuerpo, _ in self.peticiones
            if url == dispositivo.endpoint
        ]


@pytest.fixture(autouse=True)
def sin_otros_canales(monkeypatch: pytest.MonkeyPatch) -> None:
    for nombre in ("NOTIFY_WEBHOOK_URL", "NOTIFY_SMTP_HOST", "NOTIFY_EMAIL_FROM", "NOTIFY_EMAIL_TO"):
        monkeypatch.setattr(settings, nombre, "")


@pytest.fixture
def vapid(monkeypatch: pytest.MonkeyPatch) -> str:
    privada, publica = generar_claves()
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", privada)
    monkeypatch.setenv("PUSH_VAPID_SUBJECT", "mailto:operador@example.org")
    monkeypatch.delenv("PUSH_VAPID_PUBLIC_KEY", raising=False)
    return publica


@pytest.fixture
def servicio(monkeypatch: pytest.MonkeyPatch) -> ServicioFalso:
    falso = ServicioFalso()
    monkeypatch.setattr(envio, "transporte_http", falso)
    return falso


@dataclass
class Env:
    client: TestClient
    db_path: str
    auth: AuthStore
    cases: SQLiteCaseStateStore
    notifications: NotificationStore

    def register(self, email: str) -> dict:
        response = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert response.status_code == 201, response.text
        body = response.json()
        return {"Authorization": f"Bearer {body['token']}", "_id": body["user"]["id"]}

    def h(self, headers: dict) -> dict:
        return {k: v for k, v in headers.items() if not k.startswith("_")}

    def new_case(self, headers: dict) -> str:
        response = self.client.post("/cases", json={"user_id": "x", "tax_period": "2025"}, headers=self.h(headers))
        assert response.status_code == 200, response.text
        return response.json()["case_id"]

    def filas(self, tabla: str) -> list[tuple]:
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(f"SELECT * FROM {tabla}").fetchall()  # noqa: S608
        finally:
            conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    monkeypatch.setattr(push_router, "_ultima_prueba", {})
    db_path = str(tmp_path / "state.sqlite3")
    environment = Env(
        client=TestClient(app),
        db_path=db_path,
        auth=AuthStore(db_path),
        cases=SQLiteCaseStateStore(db_path),
        notifications=NotificationStore(db_path),
    )
    overrides = {
        get_auth_store: lambda: environment.auth,
        get_case_store: lambda: environment.cases,
        get_notification_store: lambda: environment.notifications,
        get_login_limiter: lambda: LoginRateLimiter(3, 900),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield environment
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
        environment.auth.close()
        environment.cases.close()
        environment.notifications.close()


def _aviso(case_id: str, key: str, *, severity: str = "urgente", created_at: datetime | None = None) -> Notification:
    return Notification(
        case_id=case_id,
        kind="plazo",
        key=key,
        title="Juan: tu devolución del IRPF 2025 de 1.234,56 €",
        body="DNI 12345678Z. Te devuelven 1.234,56 € de la devolución del IRPF 2025.",
        severity=severity,  # type: ignore[arg-type]
        actions=["Presenta el modelo 100 antes del 30 de junio"],
        sources=["BOE-A-2006-20764"],
        created_at=created_at or datetime.now(UTC) + timedelta(seconds=1),
    )


def _sin_datos_personales(cargas: list[dict[str, str]]) -> None:
    for carga in cargas:
        assert set(carga) == {"titulo", "cuerpo", "url", "etiqueta"}
        texto = json.dumps(carga, ensure_ascii=False)
        for dato in PERSONAL:
            assert dato not in texto
        assert carga["titulo"] == TITULO
        assert carga["url"] == "/?panel=avisos"


# --- endpoints ---------------------------------------------------------------


def test_needs_a_session_with_accounts(env: Env, vapid: str) -> None:
    assert env.client.get("/push/config").status_code == 401
    assert env.client.post("/push/suscripciones", json=Dispositivo(FCM + "a").suscripcion()).status_code == 401


def test_not_configured_is_reported_and_refused(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUSH_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("PUSH_VAPID_SUBJECT", raising=False)
    user = env.register("a@example.org")
    config = env.client.get("/push/config", headers=env.h(user)).json()
    assert config["disponible"] is False and config["clave_publica"] is None
    response = env.client.post("/push/suscripciones", json=Dispositivo(FCM + "a").suscripcion(), headers=env.h(user))
    assert response.status_code == 503


def test_opt_in_is_explicit_and_validated(env: Env, vapid: str) -> None:
    user = env.register("a@example.org")
    config = env.client.get("/push/config", headers=env.h(user)).json()
    assert config == {
        "disponible": True,
        "clave_publica": vapid,
        "consentimiento": False,
        "dispositivos": 0,
        "version_texto": config["version_texto"],
    }
    dispositivo = Dispositivo(FCM + "a")
    for consentimiento in (False, None):
        body = {**dispositivo.suscripcion(), "consentimiento": consentimiento}
        assert env.client.post("/push/suscripciones", json=body, headers=env.h(user)).status_code == 422
    sin = {k: v for k, v in dispositivo.suscripcion().items() if k != "consentimiento"}
    assert env.client.post("/push/suscripciones", json=sin, headers=env.h(user)).status_code == 422
    # Only real push services: never the internal network (the server POSTs to this URL).
    for endpoint in (
        "http://fcm.googleapis.com/fcm/send/x",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "https://evil.example/fcm.googleapis.com",
        "https://fcm.googleapis.com.evil.example/x",
        "https://fcm.googleapis.com:8443/x",
        "https://user@fcm.googleapis.com/x",
    ):
        response = env.client.post(
            "/push/suscripciones", json={**dispositivo.suscripcion(), "endpoint": endpoint}, headers=env.h(user)
        )
        assert response.status_code == 422, endpoint
    malas = {**dispositivo.suscripcion(), "claves": {"p256dh": "A" * 87, "auth": dispositivo.claves()["auth"]}}
    assert env.client.post("/push/suscripciones", json=malas, headers=env.h(user)).status_code == 422
    assert env.filas("push_suscripciones") == []
    assert env.filas("push_consentimientos") == []

    response = env.client.post("/push/suscripciones", json=dispositivo.suscripcion(), headers=env.h(user))
    assert response.status_code == 201, response.text
    assert response.json() == {"consentimiento": True, "dispositivos": 1}
    listado = env.client.get("/push/suscripciones", headers=env.h(user)).json()
    assert [d["servicio"] for d in listado["dispositivos"]] == ["fcm.googleapis.com"]
    assert [c["concedido"] for c in listado["consentimientos"]] == [True]
    # Neither the endpoint (a capability) nor the keys come back.
    texto = json.dumps(listado)
    assert dispositivo.endpoint not in texto and dispositivo.claves()["p256dh"] not in texto


def test_test_message_is_generic_encrypted_and_signed(env: Env, vapid: str, servicio: ServicioFalso) -> None:
    user = env.register("a@example.org")
    assert env.client.post("/push/prueba", headers=env.h(user)).status_code == 409  # no opt-in yet
    movil, portatil = Dispositivo(FCM + "movil"), Dispositivo("https://updates.push.services.mozilla.com/wpush/v2/p")
    for d in (movil, portatil):
        assert env.client.post("/push/suscripciones", json=d.suscripcion(), headers=env.h(user)).status_code == 201
    response = env.client.post("/push/prueba", headers=env.h(user))
    assert response.json() == {"enviadas": 2, "errores": 0, "caducadas": 0}
    assert servicio.recibido(movil) == [
        {"titulo": TITULO, "cuerpo": TEXTO_PRUEBA, "url": "/?panel=avisos", "etiqueta": "haciendagpt-avisos"}
    ]
    url, _cuerpo, cabeceras = servicio.peticiones[-1]
    assert cabeceras["Content-Encoding"] == "aes128gcm"
    assert int(cabeceras["TTL"]) > 0
    token, k = cabeceras["Authorization"][len("vapid t=") :].split(", k=")
    assert k == vapid
    assert verificar_jwt(token, vapid)["aud"] == "https://updates.push.services.mozilla.com"
    # Not twice in a row.
    assert env.client.post("/push/prueba", headers=env.h(user)).status_code == 429


def test_one_device_off_then_consent_withdrawn(env: Env, vapid: str) -> None:
    user = env.register("a@example.org")
    a, b = Dispositivo(FCM + "a"), Dispositivo(FCM + "b")
    for d in (a, b):
        env.client.post("/push/suscripciones", json=d.suscripcion(), headers=env.h(user))
    response = env.client.post("/push/suscripciones/baja", json={"endpoint": a.endpoint}, headers=env.h(user))
    assert response.json() == {"consentimiento": True, "dispositivos": 1}
    response = env.client.delete("/push/consentimiento", headers=env.h(user))
    assert response.json() == {"consentimiento": False, "dispositivos": 0}
    historial = env.client.get("/push/suscripciones", headers=env.h(user)).json()["consentimientos"]
    assert [c["concedido"] for c in historial] == [True, False]


def test_devices_belong_to_one_account(env: Env, vapid: str) -> None:
    ana, luis = env.register("ana@example.org"), env.register("luis@example.org")
    compartido = Dispositivo(FCM + "compartido")
    env.client.post("/push/suscripciones", json=compartido.suscripcion(), headers=env.h(ana))
    # Luis cannot remove Ana's device by knowing its endpoint…
    env.client.post("/push/suscripciones/baja", json={"endpoint": compartido.endpoint}, headers=env.h(luis))
    assert env.client.get("/push/config", headers=env.h(ana)).json()["dispositivos"] == 1
    # …but when the same browser subscribes for Luis, it is Luis's device from then on.
    env.client.post("/push/suscripciones", json=compartido.suscripcion(), headers=env.h(luis))
    assert env.client.get("/push/config", headers=env.h(ana)).json()["dispositivos"] == 0
    assert env.client.get("/push/config", headers=env.h(luis)).json()["dispositivos"] == 1


def test_erased_with_the_account(env: Env, vapid: str) -> None:
    ana, luis = env.register("ana@example.org"), env.register("luis@example.org")
    env.client.post("/push/suscripciones", json=Dispositivo(FCM + "ana").suscripcion(), headers=env.h(ana))
    env.client.post("/push/suscripciones", json=Dispositivo(FCM + "luis").suscripcion(), headers=env.h(luis))
    response = env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=env.h(ana))
    assert response.status_code == 204, response.text
    duenos = {fila[1] for fila in env.filas("push_suscripciones")}
    assert duenos == {luis["_id"]}
    assert {fila[1] for fila in env.filas("push_consentimientos")} == {luis["_id"]}


# --- delivery ----------------------------------------------------------------


def _suscrito(env: Env, email: str, endpoint: str) -> tuple[dict, str, Dispositivo]:
    user = env.register(email)
    case_id = env.new_case(user)
    dispositivo = Dispositivo(endpoint)
    assert (
        env.client.post("/push/suscripciones", json=dispositivo.suscripcion(), headers=env.h(user)).status_code == 201
    )
    return user, case_id, dispositivo


def test_delivery_channel_sends_only_a_generic_sentence(env: Env, vapid: str, servicio: ServicioFalso) -> None:
    _ana, caso_ana, movil_ana = _suscrito(env, "ana@example.org", FCM + "ana")
    luis = env.register("luis@example.org")
    caso_luis = env.new_case(luis)  # no opt-in
    antes = _aviso(caso_ana, "antes", created_at=datetime.now(UTC) - timedelta(days=1))
    for n in (
        _aviso(caso_ana, "urgente"),
        _aviso(caso_ana, "aviso", severity="aviso"),
        _aviso(caso_ana, "info", severity="info"),
        antes,
        _aviso(caso_luis, "luis"),
    ):
        assert env.notifications.add(n)
    canales = entrega.canales_configurados()
    assert [c.nombre for c in canales] == ["push"]
    store = EntregaStore(env.db_path)
    try:
        resumen = entregar_pendientes(store, canales)
        assert resumen["canales"]["push"] == {"pendientes": 2, "enviadas": 2, "errores": 0}
        cargas = servicio.recibido(movil_ana)
        assert sorted(c["cuerpo"] for c in cargas) == sorted([TEXTO_URGENTE, TEXTO_AVISO])
        _sin_datos_personales(cargas)
        # Each request body is only ciphertext: nothing readable on the wire either.
        for _url, cuerpo, cabeceras in servicio.peticiones:
            assert b"1.234" not in cuerpo and "1.234" not in json.dumps(cabeceras)
        # Nothing twice; the info notice, the one before the opt-in and Luis's never.
        assert entregar_pendientes(store, canales)["canales"]["push"]["enviadas"] == 0
        assert len(servicio.peticiones) == 2
    finally:
        store.close()


def test_expired_device_is_forgotten_and_the_notice_retried(env: Env, vapid: str, servicio: ServicioFalso) -> None:
    _user, case_id, movil = _suscrito(env, "ana@example.org", FCM + "ana")
    env.notifications.add(_aviso(case_id, "k"))
    servicio.estado[movil.endpoint] = 410
    store = EntregaStore(env.db_path)
    try:
        resumen = entregar_pendientes(store, entrega.canales_configurados())
        assert resumen["canales"]["push"]["errores"] == 1
        assert env.filas("push_suscripciones") == []
    finally:
        store.close()


def test_urgent_notice_is_pushed_when_written_and_not_again(env: Env, vapid: str, servicio: ServicioFalso) -> None:
    _user, case_id, movil = _suscrito(env, "ana@example.org", FCM + "ana")
    urgente, normal = _aviso(case_id, "u"), _aviso(case_id, "n", severity="aviso")
    for n in (urgente, normal):
        env.notifications.add(n)
    assert avisar_al_escribir(env.db_path, normal, en_segundo_plano=False) is False
    assert avisar_al_escribir(env.db_path, urgente, en_segundo_plano=False) is True
    assert [c["cuerpo"] for c in servicio.recibido(movil)] == [TEXTO_URGENTE]
    store = EntregaStore(env.db_path)
    try:
        assert store.entregada(urgente.id, "push")
        entregar_pendientes(store, entrega.canales_configurados())
    finally:
        store.close()
    # The batch delivery only added the non-urgent one.
    assert sorted(c["cuerpo"] for c in servicio.recibido(movil)) == sorted([TEXTO_URGENTE, TEXTO_AVISO])


def test_write_hook_never_raises(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", "rota")
    monkeypatch.setenv("PUSH_VAPID_SUBJECT", "mailto:a@b.c")
    assert avisar_al_escribir(env.db_path, _aviso("c", "k"), en_segundo_plano=False) is False
    with pytest.raises(ConfiguracionCanalError):
        entrega.canales_configurados()


def test_without_accounts_the_operator_owns_the_devices(
    tmp_path, monkeypatch: pytest.MonkeyPatch, vapid: str, servicio: ServicioFalso
) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    db_path = str(tmp_path / "state.sqlite3")
    store = PushStore(db_path)
    movil = Dispositivo(FCM + "op")
    store.registrar_consentimiento(OPERADOR, True)
    store.suscribir(OPERADOR, movil.endpoint, **movil.claves())
    claves = claves_desde_entorno()
    assert claves is not None
    canal = PushCanal(claves)
    canal.preparar(db_path)
    # ``ops doctor``: the subject and body never leave, only a fixed sentence.
    canal.enviar("HaciendaGPT: tarea programada con problemas", "- /srv/datos/caso-123 falló")
    assert [c["cuerpo"] for c in servicio.recibido(movil)] == [TEXTO_OPERADOR]
    notices = NotificationStore(db_path)
    try:
        n = _aviso("cualquier-caso", "k")
        notices.add(n)
        assert canal.permitido(n)
    finally:
        notices.close()
        canal.cerrar()
        store.close()


def test_endpoint_allowlist() -> None:
    for ok in (
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/QH",
        "https://wns2-db5p.notify.windows.com/w/?token=x",
    ):
        assert validar_endpoint(ok) == ok
    for mal in ("https://notify.windows.com.evil.example/x", "ftp://fcm.googleapis.com/x", "https://[::1]/x"):
        with pytest.raises(EndpointNoPermitidoError):
            validar_endpoint(mal)

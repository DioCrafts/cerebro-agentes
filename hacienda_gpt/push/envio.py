"""Sending notices to the users' devices with Web Push, and the ``push`` channel of the notice delivery.

What leaves the server — the privacy guarantee of this module:

* The payload is built ONLY from the notice's severity (:func:`carga_para`): a fixed title, a fixed generic
  sentence («Tienes un aviso urgente: abre la app») and a link into the app. Never the notice's title, body,
  amounts, dates, case id or anything typed by the user. The push service (Google, Mozilla, Apple…) and
  anybody looking at the lock screen learn only that there is a notice.
* It is encrypted end to end for the device (RFC 8291, :mod:`hacienda_gpt.push.cifrado`) and signed with the
  operator's VAPID key (RFC 8292, :mod:`hacienda_gpt.push.vapid`).
* It goes only to owners who opted in (``push_consentimientos``) and only to the devices they subscribed.
* Requests go only to the push services' hosts (:func:`validar_endpoint`: https, a known push service, no
  redirects), so a subscription cannot turn the server into a proxy towards the internal network.

Two ways out:

* The ``push`` channel of :mod:`hacienda_gpt.proactive.entrega` (``ops deliver``): notices of severity
  ``urgente`` and ``aviso`` (``info`` stays in the app), created after the owner's opt-in, once per notice.
* :func:`avisar_al_escribir` — for an URGENT notice right when it is written to the inbox, recorded as delivered
  on ``push`` so ``ops deliver`` does not send it again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import os
import threading
from typing import Any, Literal, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
import urllib.request

from hacienda_gpt import settings
from hacienda_gpt.proactive.store import Notification
from hacienda_gpt.push.almacen import OPERADOR, PushStore, Suscripcion
from hacienda_gpt.push.cifrado import b64url_decode, cifrar
from hacienda_gpt.push.vapid import ClavesVapid, ConfiguracionVapidError, cabecera_autorizacion, claves_desde_entorno

logger = logging.getLogger(__name__)

NOMBRE_CANAL = "push"
_TIMEOUT_SECONDS = 15
# How long the push service keeps a message for a device that is offline (seconds).
TTL_SEGUNDOS = 4 * 24 * 3600
# Collapses undelivered messages at the push service (RFC 8030 §5.4) and on screen (the notification tag).
TEMA = "avisos"
ETIQUETA = "haciendagpt-avisos"

TITULO = "HaciendaGPT"
TEXTO_URGENTE = "Tienes un aviso urgente: abre la app"
TEXTO_AVISO = "Tienes un aviso nuevo: abre la app"
TEXTO_PRUEBA = "Prueba: este dispositivo recibirá tus avisos"
TEXTO_OPERADOR = "Hay una tarea programada con problemas: revisa el servidor"
ENLACE_AVISOS = "/?panel=avisos"

# Severities that leave the app on this channel.
GRAVEDADES_PUSH = frozenset({"urgente", "aviso"})

# The browsers' push services. Extra hosts (exact names or ".suffix"): PUSH_HOSTS_PERMITIDOS, comma-separated.
HOSTS_PERMITIDOS: tuple[str, ...] = (
    "fcm.googleapis.com",  # Chrome, Edge on Android, Samsung Internet, Opera…
    "updates.push.services.mozilla.com",  # Firefox
    "web.push.apple.com",  # Safari (macOS 13+, iOS/iPadOS 16.4+ home-screen apps)
    ".push.apple.com",
    ".notify.windows.com",  # Edge on Windows
)


class EndpointNoPermitidoError(ValueError):
    """The subscription points somewhere that is not a known push service. Message for the user (Spanish)."""


def _hosts_permitidos() -> tuple[str, ...]:
    extra = tuple(h.strip().lower() for h in os.environ.get("PUSH_HOSTS_PERMITIDOS", "").split(",") if h.strip())
    return HOSTS_PERMITIDOS + extra


def validar_endpoint(endpoint: str) -> str:
    """The endpoint, if it is an https URL of a known push service on the default port; else raise."""
    if len(endpoint) > 2048:
        raise EndpointNoPermitidoError("La dirección del servicio de avisos es demasiado larga.")
    try:
        partes = urlsplit(endpoint)
        puerto = partes.port
    except ValueError as exc:
        raise EndpointNoPermitidoError("La dirección del servicio de avisos no es válida.") from exc
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or not host or partes.username or partes.password or puerto not in (None, 443):
        raise EndpointNoPermitidoError("La dirección del servicio de avisos no es válida.")
    for permitido in _hosts_permitidos():
        if (permitido.startswith(".") and host.endswith(permitido)) or host == permitido:
            return endpoint
    raise EndpointNoPermitidoError(
        "Este navegador usa un servicio de avisos que no está admitido. Prueba con Chrome, Firefox, Edge o Safari."
    )


def configuracion() -> ClavesVapid | None:
    """The VAPID keys when Web Push is configured, None when it is off. Raises when it is half-configured."""
    return claves_desde_entorno()


def configurado() -> bool:
    try:
        return configuracion() is not None
    except ConfiguracionVapidError:
        return False


def carga_para(
    notification: Notification | None, *, tipo: Literal["aviso", "prueba", "operador"] = "aviso"
) -> dict[str, str]:
    """The ONLY content a push message carries. Built from the severity alone: no figure, no personal data."""
    if tipo == "prueba":
        cuerpo = TEXTO_PRUEBA
    elif tipo == "operador":
        cuerpo = TEXTO_OPERADOR
    else:
        cuerpo = TEXTO_URGENTE if notification is not None and notification.severity == "urgente" else TEXTO_AVISO
    return {"titulo": TITULO, "cuerpo": cuerpo, "url": ENLACE_AVISOS, "etiqueta": ETIQUETA}


# (url, body, headers) -> HTTP status. Injected by the tests; the default is urllib without redirects.
Transporte = Callable[[str, bytes, Mapping[str, str]], int]


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_opener = urllib.request.build_opener(_SinRedirecciones)


def transporte_http(url: str, cuerpo: bytes, cabeceras: Mapping[str, str]) -> int:
    request = urllib.request.Request(url, data=cuerpo, headers=dict(cabeceras), method="POST")
    try:
        with _opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            response.read(1024)
            return int(response.status)
    except HTTPError as exc:
        return int(exc.code)


@dataclass
class ResultadoEnvio:
    enviadas: int = 0
    errores: int = 0
    caducadas: int = 0


def enviar_a_suscripcion(
    claves: ClavesVapid,
    suscripcion: Suscripcion,
    carga: Mapping[str, str],
    *,
    urgente: bool = False,
    transporte: Transporte | None = None,
) -> int:
    """Encrypt and POST one message to one device. Returns the push service's HTTP status."""
    validar_endpoint(suscripcion.endpoint)
    cuerpo = cifrar(
        b64url_decode(suscripcion.p256dh),
        b64url_decode(suscripcion.auth),
        json.dumps(dict(carga), ensure_ascii=False).encode("utf-8"),
    )
    cabeceras = {
        "Authorization": cabecera_autorizacion(claves, suscripcion.endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SEGUNDOS),
        "Urgency": "high" if urgente else "normal",
        "Topic": TEMA,
    }
    return (transporte or transporte_http)(suscripcion.endpoint, cuerpo, cabeceras)


def enviar_a_propietario(
    store: PushStore,
    propietario: str,
    carga: Mapping[str, str],
    *,
    claves: ClavesVapid,
    urgente: bool = False,
    transporte: Transporte | None = None,
) -> ResultadoEnvio:
    """Send ``carga`` to every device of ``propietario``. Devices the push service says are gone are forgotten."""
    resultado = ResultadoEnvio()
    for suscripcion in store.de(propietario):
        try:
            estado = enviar_a_suscripcion(claves, suscripcion, carga, urgente=urgente, transporte=transporte)
        except Exception as exc:  # one broken device must not stop the others
            # The endpoint is a capability: only the service host goes to the log.
            logger.warning("Push to a device on %s failed: %s", suscripcion.servicio, type(exc).__name__)
            store.marcar_envio(suscripcion.id, ok=False)
            resultado.errores += 1
            continue
        if 200 <= estado < 300:
            store.marcar_envio(suscripcion.id, ok=True)
            resultado.enviadas += 1
        elif estado in (404, 410):
            # RFC 8030 §7.3: the subscription expired or was revoked in the browser.
            store.eliminar(suscripcion.id)
            resultado.caducadas += 1
        else:
            logger.warning("Push service %s answered %s", suscripcion.servicio, estado)
            store.marcar_envio(suscripcion.id, ok=False)
            resultado.errores += 1
    return resultado


def propietario_de(case_id: str, db_path: str, *, cuentas: Any | None = None) -> str | None:
    """Who owns a case's notices: the account (accounts on), the operator (accounts off).

    ``cuentas`` is an open ``AuthStore`` to reuse (one connection per delivery run, not per notice).
    """
    if settings.AUTH_MODE != "required":
        return OPERADOR
    if cuentas is not None:
        return cast(str | None, cuentas.owner_of(case_id))
    from hacienda_gpt.auth.store import AuthStore

    propias = AuthStore(db_path)
    try:
        return propias.owner_of(case_id)
    finally:
        propias.close()


class SinDestinatarioError(RuntimeError):
    """Nobody to send to: the owner has no device subscribed or did not opt in."""


class PushCanal:
    """The ``push`` channel of :func:`hacienda_gpt.proactive.entrega.entregar_pendientes`.

    Unlike the webhook and e-mail channels (one operator destination), it delivers each notice to the devices
    of the owner of its case, and applies its own consent check (:meth:`permitido`) instead of the e-mail one.
    """

    nombre = NOMBRE_CANAL

    def __init__(self, claves: ClavesVapid, *, transporte: Transporte | None = None) -> None:
        self._claves = claves
        self._transporte = transporte
        self._db_path: str | None = None
        self._store: PushStore | None = None
        self._cuentas: Any | None = None

    def preparar(self, db_path: str) -> Callable[[Notification], bool]:
        """Bind to the database being delivered and return the filter of what may go out on this channel."""
        if self._db_path != db_path:
            self.cerrar()
            self._db_path = db_path
            self._store = PushStore(db_path)
            if settings.AUTH_MODE == "required":
                from hacienda_gpt.auth.store import AuthStore

                self._cuentas = AuthStore(db_path)
        return self.permitido

    def cerrar(self) -> None:
        if self._store is not None:
            self._store.close()
        if self._cuentas is not None:
            self._cuentas.close()
        self._store = None
        self._cuentas = None
        self._db_path = None

    def _propietario(self, case_id: str) -> str | None:
        store = self._almacen()
        return propietario_de(case_id, store.db_path, cuentas=self._cuentas)

    def _almacen(self) -> PushStore:
        if self._store is None:
            self.preparar(settings.DECISION_STATE_DB_PATH)
        assert self._store is not None
        return self._store

    def permitido(self, notification: Notification) -> bool:
        """Only urgent/aviso notices, of an owner who opted in, created after the opt-in, with a device."""
        if notification.severity not in GRAVEDADES_PUSH:
            return False
        store = self._almacen()
        propietario = self._propietario(notification.case_id)
        if propietario is None:
            return False
        registro = store.consentimiento(propietario)
        if registro is None or not registro.concedido or notification.created_at < registro.momento:
            return False
        return bool(store.de(propietario))

    def enviar_aviso(self, notification: Notification) -> None:
        """Deliver one notice to its owner's devices; raise when none accepted it (retried on the next run)."""
        store = self._almacen()
        propietario = self._propietario(notification.case_id)
        if propietario is None or not store.tiene_consentimiento(propietario):
            raise SinDestinatarioError("el propietario del aviso no ha aceptado los avisos en el dispositivo")
        resultado = enviar_a_propietario(
            store,
            propietario,
            carga_para(notification),
            claves=self._claves,
            urgente=notification.severity == "urgente",
            transporte=self._transporte,
        )
        if resultado.enviadas == 0:
            raise SinDestinatarioError(
                f"ningún dispositivo aceptó el aviso ({resultado.errores} errores, {resultado.caducadas} caducados)"
            )

    def enviar(self, asunto: str, cuerpo: str) -> None:
        """An operator message (``ops doctor``): only without accounts, to the operator's own devices.

        The subject and body are NOT sent (they may name cases or paths): only a fixed sentence.
        """
        if settings.AUTH_MODE == "required":
            raise SinDestinatarioError("con cuentas de usuario no hay dispositivo del operador para push")
        store = self._almacen()
        if not store.tiene_consentimiento(OPERADOR):
            raise SinDestinatarioError("el operador no ha activado los avisos en ningún dispositivo")
        resultado = enviar_a_propietario(
            store, OPERADOR, carga_para(None, tipo="operador"), claves=self._claves, transporte=self._transporte
        )
        if resultado.enviadas == 0:
            raise SinDestinatarioError("ningún dispositivo del operador aceptó el mensaje")


def canal_push_configurado() -> PushCanal | None:
    """The channel when PUSH_VAPID_* is configured (None otherwise). Raises when half-configured."""
    claves = configuracion()
    return PushCanal(claves) if claves is not None else None


# Whether the inbox write hook pushes in a daemon thread (the default) or inline; tests set it to False so
# what reaches the push service is deterministic.
EN_SEGUNDO_PLANO = True


def avisar_al_escribir(
    db_path: str,
    notification: Notification,
    *,
    en_segundo_plano: bool | None = None,
    transporte: Transporte | None = None,
) -> bool:
    """Push an URGENT notice right after it was written to the inbox. Never raises; True when it was started.

    Meant to be called from the inbox write path (``NotificationStore.add`` after a successful insert). It does
    nothing unless Web Push is configured, the notice is urgent and its owner opted in with a device. The
    delivery is recorded on the ``push`` channel of ``notification_deliveries`` so ``ops deliver`` does not
    send it twice. By default it runs in a daemon thread: the write never waits for a push service.
    """
    try:
        if notification.severity != "urgente":
            return False
        claves = configuracion()
        if claves is None:
            return False
    except Exception as exc:  # a misconfiguration must never fail the write
        logger.warning("Web Push is misconfigured: %s", exc)
        return False

    def trabajo() -> None:
        try:
            from hacienda_gpt.proactive.entrega import EntregaStore

            canal = PushCanal(claves, transporte=transporte)
            if not canal.preparar(db_path)(notification):
                return
            entregas = EntregaStore(db_path)
            try:
                if entregas.entregada(notification.id, NOMBRE_CANAL):
                    return
                canal.enviar_aviso(notification)
                entregas.registrar(notification.id, NOMBRE_CANAL, ahora=datetime.now(UTC))
            finally:
                entregas.close()
                canal.cerrar()
        except SinDestinatarioError:
            return
        except Exception as exc:  # never propagate into the writer
            logger.warning("Immediate push of an urgent notice failed: %s", type(exc).__name__)

    if EN_SEGUNDO_PLANO if en_segundo_plano is None else en_segundo_plano:
        threading.Thread(target=trabajo, name="push-urgente", daemon=True).start()
    else:
        trabajo()
    return True

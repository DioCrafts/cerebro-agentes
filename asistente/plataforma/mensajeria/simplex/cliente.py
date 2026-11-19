"""WebSocket JSON client of the simplex-chat CLI: correlation ids, request/response with timeout, an event stream.

One connection, two kinds of traffic on it (see ``protocolo``): responses (carry the ``corrId`` we sent) and events
(no ``corrId``). A dedicated reader thread owns the socket's receive side and never blocks on anyone:

- a response wakes the thread waiting in :meth:`ClienteSimplex.comando` (a late response, after its timeout, is
  dropped and counted);
- an event goes into a BOUNDED queue read by the channel; when the queue is full the event is dropped and counted
  instead of blocking the reader — a blocked reader would also stop the responses the slow consumer itself may be
  waiting for (deadlock).

When the connection drops, every pending request fails at once with :class:`ErrorDesconectado` (no caller waits for
its full timeout on a dead socket). Keepalive pings are the websockets library's (``ping_interval``/``ping_timeout``):
a CLI that stops answering closes the connection, and the channel reconnects. The client never logs frame contents,
only event tags and counts. It does not reconnect by itself: the channel creates a new client per connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import json
import logging
import queue
import secrets
import threading
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from asistente.plataforma.mensajeria.simplex.protocolo import (
    R_ERROR_COMANDO,
    ContadorProtocolo,
    codigo_error,
    verbo,
)

logger = logging.getLogger(__name__)
# The websockets library logs every frame (with a preview of its content) at DEBUG on the connection's logger: this
# one never goes below INFO, so enabling DEBUG logging elsewhere cannot write message text to the logs.
_LOGGER_WS = logging.getLogger(__name__ + ".websocket")
_LOGGER_WS.setLevel(logging.INFO)

MAX_TRAMA = 16 * 1024 * 1024  # events with image previews or long contact lists stay well below this


class ErrorClienteSimplex(Exception):  # noqa: N818 - Spanish names across the project
    """Base of the client's errors. Messages are Spanish and never include message content."""


class ErrorDesconectado(ErrorClienteSimplex):
    """No connection, or it dropped while the request was pending."""


class ErrorTiempoAgotado(ErrorClienteSimplex):
    """The CLI did not answer in time."""


class ErrorComando(ErrorClienteSimplex):
    """The CLI answered ``chatCmdError``. ``codigo`` is content-free (``errorStore:userContactLinkNotFound``)."""

    def __init__(self, codigo: str, respuesta: dict[str, Any]) -> None:
        super().__init__(f"simplex-chat rechazó la orden ({codigo})")
        self.codigo = codigo
        self.respuesta = respuesta


@dataclass
class _Pendiente:
    listo: threading.Event = field(default_factory=threading.Event)
    respuesta: dict[str, Any] | None = None
    error: ErrorClienteSimplex | None = None


class ClienteSimplex:
    """A single connection to the CLI. Thread-safe: any thread may call :meth:`comando`."""

    def __init__(
        self,
        url: str,
        *,
        timeout_conexion: float = 10.0,
        ping_s: float = 20.0,
        max_eventos: int = 1000,
        contador: ContadorProtocolo | None = None,
        max_trama: int = MAX_TRAMA,
    ) -> None:
        self._url = url
        self._timeout_conexion = timeout_conexion
        self._ping_s = ping_s or None
        self._max_trama = max_trama
        self.contador = contador or ContadorProtocolo()
        self._ws: ClientConnection | None = None
        self._lock = threading.Lock()
        self._envio = threading.Lock()
        self._pendientes: dict[str, _Pendiente] = {}
        self._eventos: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_eventos)
        self._abierto = threading.Event()
        self._cerrado = threading.Event()  # cerrar() was called: this client never (re)opens
        self._lector: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._prefijo = secrets.token_hex(4)

    # --- lifecycle ---

    def conectar(self) -> None:
        """Open the WebSocket and start the reader. Raises :class:`ErrorDesconectado` on failure."""
        if self._cerrado.is_set():
            raise ErrorDesconectado("cliente cerrado")
        try:
            ws = connect(
                self._url,
                open_timeout=self._timeout_conexion,
                ping_interval=self._ping_s,
                ping_timeout=self._ping_s,
                close_timeout=2,
                max_size=self._max_trama,
                compression=None,
                # Never through an HTTP(S) proxy from the environment: the CLI is local (or behind the operator's own
                # TLS proxy, given explicitly in the URL).
                proxy=None,
                user_agent_header=None,
                logger=_LOGGER_WS,
            )
        except Exception as e:  # OSError, InvalidHandshake, TimeoutError…: the class name says enough
            raise ErrorDesconectado(f"no se pudo conectar con simplex-chat ({type(e).__name__})") from None
        self._ws = ws
        self._abierto.set()
        if self._cerrado.is_set():  # closed while connecting
            self.cerrar()
            raise ErrorDesconectado("cliente cerrado")
        self._lector = threading.Thread(target=self._leer, args=(ws,), name="simplex-lector", daemon=True)
        self._lector.start()

    @property
    def conectado(self) -> bool:
        return self._abierto.is_set()

    def esperar_desconexion(self, timeout: float | None = None) -> bool:
        """Block until the connection is closed (True) or the timeout passes (False)."""
        lector = self._lector
        if lector is None:
            return True
        lector.join(timeout)
        return not lector.is_alive()

    def cerrar(self) -> None:
        """Close the connection (idempotent). Pending requests fail with :class:`ErrorDesconectado`."""
        self._cerrado.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # already closed or broken: nothing else to do
                pass
        lector = self._lector
        if lector is not None and lector is not threading.current_thread():
            lector.join(5)
        self._abierto.clear()
        self._fallar_pendientes()

    # --- requests ---

    def comando(self, cmd: str, *, timeout: float = 30.0) -> dict[str, Any]:
        """Send one command and return its ``resp`` record.

        Raises :class:`ErrorComando` for ``chatCmdError``, :class:`ErrorTiempoAgotado` after ``timeout`` seconds and
        :class:`ErrorDesconectado` when there is no connection or it drops meanwhile.
        """
        ws = self._ws
        corr = f"{self._prefijo}-{next(self._ids)}"
        pendiente = _Pendiente()
        with self._lock:
            # Checked under the same lock the reader takes to fail every pending request when it stops: a request
            # registered here is either failed by it or sees the connection already closed.
            if ws is None or not self._abierto.is_set():
                raise ErrorDesconectado("sin conexión con simplex-chat")
            self._pendientes[corr] = pendiente
        try:
            with self._envio:
                ws.send(json.dumps({"corrId": corr, "cmd": cmd}, ensure_ascii=False))
        except Exception:
            with self._lock:
                self._pendientes.pop(corr, None)
            raise ErrorDesconectado(f"no se pudo enviar la orden {verbo(cmd)}") from None
        if not pendiente.listo.wait(timeout):
            with self._lock:
                self._pendientes.pop(corr, None)
            raise ErrorTiempoAgotado(f"simplex-chat no respondió a {verbo(cmd)} en {timeout:g} s")
        if pendiente.error is not None:
            raise pendiente.error
        resp = pendiente.respuesta or {}
        if resp.get("type") == R_ERROR_COMANDO:
            raise ErrorComando(codigo_error(resp), resp)
        return resp

    def siguiente_evento(self, timeout: float | None = None) -> dict[str, Any] | None:
        """The next event's ``resp`` record, or None when none arrives within ``timeout`` (0: do not wait)."""
        try:
            if timeout == 0:
                return self._eventos.get_nowait()
            return self._eventos.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def eventos_en_cola(self) -> int:
        return self._eventos.qsize()

    # --- reader thread ---

    def _leer(self, ws: ClientConnection) -> None:
        try:
            for trama in ws:
                self._recibir(trama)
        except ConnectionClosed:
            pass
        except Exception as e:  # never let the reader die silently; the content of the frame is not logged
            logger.warning("simplex: el lector de la conexión terminó (%s)", type(e).__name__)
        finally:
            self._abierto.clear()
            self._fallar_pendientes()

    def _recibir(self, trama: str | bytes) -> None:
        if not isinstance(trama, str):
            self.contador.trama_invalida()
            return
        try:
            datos = json.loads(trama)
        except ValueError:
            self.contador.trama_invalida()
            return
        resp = datos.get("resp") if isinstance(datos, dict) else None
        if not isinstance(resp, dict) or not isinstance(resp.get("type"), str):
            self.contador.trama_invalida()
            return
        corr = datos.get("corrId")
        if isinstance(corr, str):
            with self._lock:
                pendiente = self._pendientes.pop(corr, None)
            if pendiente is None:
                self.contador.respuesta_huerfana()
                logger.debug("simplex: respuesta sin petición pendiente (%s)", resp["type"][:64])
                return
            pendiente.respuesta = resp
            pendiente.listo.set()
            return
        try:
            self._eventos.put_nowait(resp)
        except queue.Full:
            self.contador.evento_descartado()
            logger.warning("simplex: cola de eventos llena; evento descartado (%s)", resp["type"][:64])

    def _fallar_pendientes(self) -> None:
        with self._lock:
            pendientes = list(self._pendientes.values())
            self._pendientes.clear()
        for p in pendientes:
            p.error = ErrorDesconectado("se perdió la conexión con simplex-chat")
            p.listo.set()


__all__ = [
    "ClienteSimplex",
    "ErrorClienteSimplex",
    "ErrorComando",
    "ErrorDesconectado",
    "ErrorTiempoAgotado",
]

"""A fake simplex-chat CLI WebSocket server: a faithful subset of the documented bot API, for tests.

Reusable by any test that needs a SimpleX CLI (the gateway's end-to-end tests included)::

    with ServidorSimplexFalso() as cli:
        canal = CanalSimplex(configuracion_para(cli, tmp_path))
        ...
        cli.mensaje(contacto=7, texto="hola")

What it follows (bots/api/*.md at simplex-chat ``stable`` cb4b50db, 7.1.4, and the CLI's ``apps/simplex-chat/Server.hs``):

- frames: commands ``{"corrId", "cmd"}``; responses ``{"corrId", "resp"}``; events ``{"resp"}`` (no corrId);
  an undecodable request answers ``chatCmdError`` without corrId; commands are processed one at a time per client;
- events produced while no client is connected are kept and delivered to the next one (the CLI's output queue);
- the commands the channel uses, with their documented responses and errors: ``/version``, ``/user``, ``/users``,
  ``/_user``, ``/_create user``, ``/_show_address``, ``/_address``, ``/_address_settings``,
  ``/_set accept group invitations``, ``/_servers``, ``/_send``, ``/_update item``, ``/freceive``, ``/fcancel``,
  ``/_delete``; anything else answers ``chatCmdError`` ``commandError``;
- ``/freceive <id> ... <path>`` creates the (empty) file at the given path at once, like the CLI, refuses an existing
  path (``fileAlreadyExists``), then writes the content and emits ``rcvFileStart`` + ``rcvFileComplete``;
- records carry extra unknown fields (``futuro``) to exercise the tolerance the guide requires.

Approximations (not documented precisely): the error for a message too large to send (``errorStore/largeMsg``)
and the size check itself (the encoded content against 13 380 bytes, the post-quantum limit).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import itertools
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import Server, ServerConnection, serve

NOMBRE_BOT = "Asistente del hogar"
LIMITE_MENSAJE = 13_380
# The server's frames (message text included) must not reach the logs the privacy tests inspect.
_LOGGER = logging.getLogger("tests.mensajeria.simplex.falso")
_LOGGER.setLevel(logging.WARNING)


def _ahora() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _error(tipo: str, sub: str, **extra: Any) -> dict[str, Any]:
    clave = {"error": "errorType", "errorStore": "storeError", "errorAgent": "agentError"}[tipo]
    return {"type": "chatCmdError", "chatError": {"type": tipo, clave: {"type": sub, **extra}}}


@dataclass
class UsuarioFalso:
    user_id: int
    nombre: str
    activo: bool = False
    auto_grupos: bool = False
    archivos: bool = True
    oculto: bool = False
    bot: bool = True


@dataclass
class ArchivoFalso:
    file_id: int
    contacto: int
    nombre: str
    contenido: bytes
    tamano: int
    modo: str  # completo | error | nunca | enlace | crece
    item: dict[str, Any] = field(default_factory=dict)
    ruta: str | None = None


class ServidorSimplexFalso:
    def __init__(
        self,
        *,
        version: str = "7.1.4.0",
        perfiles: tuple[str, ...] = (NOMBRE_BOT,),
        activo: int | None = 0,
        con_direccion: bool = True,
        ajustes_direccion: dict[str, Any] | None = None,
        auto_grupos: bool = False,
        admite_archivos: bool = True,
        servidores_smp: tuple[str, ...] = ("smp://huella@smp.ejemplo.org",),
        servidores_xftp: tuple[str, ...] = ("xftp://huella@xftp.ejemplo.org",),
        retraso_archivo_s: float = 0.05,
    ) -> None:
        self.version = version
        self.usuarios: list[UsuarioFalso] = [
            UsuarioFalso(i + 1, n, activo=(activo == i), auto_grupos=auto_grupos, archivos=admite_archivos)
            for i, n in enumerate(perfiles)
        ]
        self.direcciones: dict[int, dict[str, Any]] = {}
        if con_direccion and activo is not None:
            uid = self.usuarios[activo].user_id
            self.direcciones[uid] = {
                "link": f"https://smp.ejemplo.org/a#direccion-{uid}",
                "ajustes": ajustes_direccion
                or {"businessAddress": False, "autoAccept": {"acceptIncognito": False}, "futuro": 1},
            }
        self.servidores_smp = servidores_smp
        self.servidores_xftp = servidores_xftp
        self.retraso_archivo_s = retraso_archivo_s
        self.ttl = 7 * 86400  # the bot's chat item TTL (seconds; 0 = forever)
        self.contactos: dict[int, str] = {}
        self.archivos: dict[int, ArchivoFalso] = {}
        self.enviados: list[dict[str, Any]] = []
        self.editados: list[dict[str, Any]] = []
        self.borrados: list[int] = []
        self.recibidos: dict[int, str] = {}  # texts of the messages emitted as received (they can be quoted)
        self.comandos: list[str] = []
        self.conexiones_aceptadas = 0
        self.caido = False
        self._items = itertools.count(1000)
        self._ficheros = itertools.count(500)
        self._lock = threading.RLock()
        self._conexiones: dict[ServerConnection, threading.Lock] = {}
        self._buffer: list[dict[str, Any]] = []
        self._retener: set[str] = set()
        self._retenidas: list[tuple[ServerConnection, str, str]] = []
        self._fallos: dict[str, tuple[dict[str, Any], int]] = {}
        self._servidor: Server | None = None
        self._hilo: threading.Thread | None = None

    # --- lifecycle ---

    def __enter__(self) -> ServidorSimplexFalso:
        return self.arrancar()

    def __exit__(self, *exc: object) -> None:
        self.detener()

    def arrancar(self, puerto: int = 0) -> ServidorSimplexFalso:
        self._servidor = serve(
            self._atender,
            "127.0.0.1",
            puerto,
            process_request=self._filtrar,
            compression=None,
            max_size=16 * 1024 * 1024,
            ping_interval=None,
            logger=_LOGGER,
        )
        self._hilo = threading.Thread(target=self._servidor.serve_forever, name="simplex-falso", daemon=True)
        self._hilo.start()
        return self

    @property
    def puerto(self) -> int:
        assert self._servidor is not None
        return int(self._servidor.socket.getsockname()[1])

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.puerto}"

    def detener(self) -> None:
        self.desconectar()
        if self._servidor is not None:
            self._servidor.shutdown()
        if self._hilo is not None:
            self._hilo.join(5)

    def desconectar(self) -> None:
        """Drop every client connection (the CLI restarting, or the network between them failing)."""
        with self._lock:
            # Forgotten at once: an event emitted from now on is kept for the next client (the CLI's output queue).
            conexiones = list(self._conexiones)
            self._conexiones.clear()
        for conexion in conexiones:
            try:
                conexion.close(code=1011)
            except Exception:
                pass

    @property
    def conectados(self) -> int:
        with self._lock:
            return len(self._conexiones)

    def _filtrar(self, conexion: ServerConnection, peticion: Any) -> Any:
        if self.caido:
            return conexion.respond(503, "simplex-chat no disponible\n")
        return None

    # --- test controls ---

    def retener(self, prefijo: str) -> None:
        """Do not answer commands starting with ``prefijo`` until :meth:`soltar` (a CLI stuck on the network)."""
        with self._lock:
            self._retener.add(prefijo)

    def soltar(self) -> None:
        with self._lock:
            self._retener.clear()
            retenidas, self._retenidas = self._retenidas, []
        for conexion, corr, cmd in retenidas:
            self._responder(conexion, corr, cmd)

    def fallar(self, prefijo: str, respuesta: dict[str, Any], veces: int = 1) -> None:
        """Answer the next ``veces`` commands starting with ``prefijo`` with ``respuesta`` (e.g. a chatCmdError)."""
        with self._lock:
            self._fallos[prefijo] = (respuesta, veces)

    def esperar(self, condicion: Callable[[], bool], timeout: float = 5.0) -> bool:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.01)
        return condicion()

    def comandos_que_empiezan(self, prefijo: str) -> list[str]:
        with self._lock:
            return [c for c in self.comandos if c.startswith(prefijo)]

    @property
    def usuario_activo(self) -> UsuarioFalso | None:
        return next((u for u in self.usuarios if u.activo), None)

    # --- events ---

    def emitir(self, resp: dict[str, Any]) -> None:
        """Send an event (``{"resp": ...}``) to the client; kept for the next client when none is connected."""
        with self._lock:
            conexiones = list(self._conexiones)
            if not conexiones:
                self._buffer.append(resp)
                return
        entregado = False
        for conexion in conexiones:
            entregado = self._enviar(conexion, {"resp": resp}) or entregado
        if not entregado:
            with self._lock:
                self._buffer.append(resp)

    def contacto_conectado(self, contacto: int, nombre: str = "Ana") -> None:
        self.contactos[contacto] = nombre
        self.emitir(
            {
                "type": "contactConnected",
                "user": self._usuario_json(self._activo()),
                "contact": self._contacto_json(contacto),
            }
        )

    def mensaje(
        self,
        contacto: int,
        texto: str = "",
        *,
        nombre: str = "Ana",
        tipo: str = "text",
        reenviado: bool = False,
        cita: str | None = None,
        archivo: dict[str, Any] | None = None,
        grupo: bool = False,
        usuario: int | None = None,
        item_id: int | None = None,
    ) -> int:
        """Emit ``newChatItems`` with one received message; returns its chat item id.

        ``archivo``: ``{"nombre", "contenido": bytes, "tamano"?: declared size, "modo"?: completo|error|nunca|enlace|crece}``.
        """
        self.contactos.setdefault(contacto, nombre)
        item = item_id if item_id is not None else next(self._items)
        self.recibidos[item] = texto
        contenido: dict[str, Any] = {"type": tipo, "text": texto}
        if tipo == "image":
            contenido["image"] = "data:image/jpg;base64,/9j/4AAQ"
        if tipo == "voice":
            contenido["duration"] = 3
        chat_item: dict[str, Any] = {
            "chatDir": {"type": "groupRcv", "groupMember": {"groupMemberId": 1}} if grupo else {"type": "directRcv"},
            "meta": self._meta(item, texto, reenviado),
            "content": {"type": "rcvMsgContent", "msgContent": contenido},
            "mentions": {},
            "reactions": [],
            "futuro": {"a": 1},
        }
        if cita is not None:
            chat_item["quotedItem"] = {
                "chatDir": {"type": "directSnd"},
                "itemId": 1,
                "sentAt": _ahora(),
                "content": {"type": "text", "text": cita},
            }
        if archivo is not None:
            fid = next(self._ficheros)
            datos = archivo.get("contenido", b"")
            tamano = int(archivo.get("tamano", len(datos)))
            self.archivos[fid] = ArchivoFalso(
                fid, contacto, archivo.get("nombre", "archivo"), datos, tamano, archivo.get("modo", "completo")
            )
            chat_item["file"] = {
                "fileId": fid,
                "fileName": archivo.get("nombre", "archivo"),
                "fileSize": tamano,
                "fileStatus": {"type": "rcvInvitation"},
                "fileProtocol": "xftp",
            }
        info = (
            {"type": "group", "groupInfo": {"groupId": 3, "localDisplayName": "familia"}}
            if grupo
            else {"type": "direct", "contact": self._contacto_json(contacto)}
        )
        elemento = {"chatInfo": info, "chatItem": chat_item}
        if archivo is not None:
            self.archivos[chat_item["file"]["fileId"]].item = elemento
        usuario_json = self._usuario_json(self._activo())
        if usuario is not None:
            usuario_json = {**usuario_json, "userId": usuario}
        self.emitir({"type": "newChatItems", "user": usuario_json, "chatItems": [elemento]})
        return item

    # --- JSON builders (shapes from TYPES.md) ---

    def _activo(self) -> UsuarioFalso:
        activo = self.usuario_activo
        return activo if activo is not None else UsuarioFalso(0, "?")

    @staticmethod
    def _usuario_json(u: UsuarioFalso) -> dict[str, Any]:
        perfil: dict[str, Any] = {"profileId": u.user_id, "displayName": u.nombre, "fullName": "", "localAlias": ""}
        if u.bot:
            perfil["peerType"] = "bot"
        datos: dict[str, Any] = {
            "userId": u.user_id,
            "agentUserId": u.user_id,
            "userContactId": u.user_id,
            "localDisplayName": u.nombre,
            "profile": perfil,
            "fullPreferences": {
                "files": {"allow": "always" if u.archivos else "no"},
                "voice": {"allow": "yes"},
                "calls": {"allow": "no"},
            },
            "activeUser": u.activo,
            "activeOrder": 1,
            "showNtfs": True,
            "sendRcptsContacts": True,
            "sendRcptsSmallGroups": True,
            "autoAcceptMemberContacts": False,
            "autoAcceptGroupInvitations": u.auto_grupos,
            "userChatRelay": False,
            "clientService": False,
            "futuro": "x",
        }
        if u.oculto:
            datos["viewPwdHash"] = {"hash": "h", "salt": "s"}
        return datos

    def _contacto_json(self, contacto: int) -> dict[str, Any]:
        nombre = self.contactos.get(contacto, "Ana")
        return {
            "contactId": contacto,
            "localDisplayName": nombre,
            "profile": {"profileId": 100 + contacto, "displayName": nombre, "fullName": "", "localAlias": ""},
            "contactUsed": True,
            "contactStatus": "active",
            "chatSettings": {"enableNtfs": "all", "favorite": False},
            "userPreferences": {},
            "mergedPreferences": {},
            "createdAt": _ahora(),
            "updatedAt": _ahora(),
            "chatTags": [],
            "contactGrpInvSent": False,
            "chatDeleted": False,
            "futuro": True,
        }

    @staticmethod
    def _meta(item: int, texto: str, reenviado: bool = False) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "itemId": item,
            "itemTs": _ahora(),
            "itemText": texto,
            "itemStatus": {"type": "rcvNew"},
            "itemEdited": False,
            "userMention": False,
            "hasLink": False,
            "deletable": True,
            "editable": False,
            "showGroupAsSender": False,
            "createdAt": _ahora(),
            "updatedAt": _ahora(),
        }
        if reenviado:
            meta["itemForwarded"] = {"type": "unknown"}
        return meta

    def _item_enviado(self, contacto: int, item: int, contenido: dict[str, Any], cita: str | None) -> dict[str, Any]:
        chat_item: dict[str, Any] = {
            "chatDir": {"type": "directSnd"},
            "meta": {**self._meta(item, contenido.get("text", "")), "itemStatus": {"type": "sndNew"}, "editable": True},
            "content": {"type": "sndMsgContent", "msgContent": contenido},
            "mentions": {},
            "reactions": [],
        }
        if cita is not None:
            chat_item["quotedItem"] = {"sentAt": _ahora(), "content": {"type": "text", "text": cita}}
        return {"chatInfo": {"type": "direct", "contact": self._contacto_json(contacto)}, "chatItem": chat_item}

    # --- connection handling ---

    def _atender(self, conexion: ServerConnection) -> None:
        with self._lock:
            self._conexiones[conexion] = threading.Lock()
            self.conexiones_aceptadas += 1
            pendientes, self._buffer = self._buffer, []
        for resp in pendientes:
            self._enviar(conexion, {"resp": resp})
        try:
            for trama in conexion:
                self._recibir(conexion, trama)
        except ConnectionClosed:
            pass
        finally:
            with self._lock:
                self._conexiones.pop(conexion, None)

    def _enviar(self, conexion: ServerConnection, datos: dict[str, Any]) -> bool:
        with self._lock:
            cerrojo = self._conexiones.get(conexion)
        if cerrojo is None:
            return False
        try:
            with cerrojo:
                conexion.send(json.dumps(datos))
            return True
        except ConnectionClosed:
            return False

    def _recibir(self, conexion: ServerConnection, trama: str | bytes) -> None:
        try:
            peticion = json.loads(trama)
            corr, cmd = peticion["corrId"], peticion["cmd"]
            if not isinstance(corr, str) or not isinstance(cmd, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self._enviar(conexion, {"resp": _error("error", "commandError", message="invalid request")})
            return
        with self._lock:
            self.comandos.append(cmd)
            if any(cmd.startswith(r) for r in self._retener):
                self._retenidas.append((conexion, corr, cmd))
                return
        self._responder(conexion, corr, cmd)

    def _responder(self, conexion: ServerConnection, corr: str, cmd: str) -> None:
        with self._lock:
            for prefijo, (respuesta, veces) in list(self._fallos.items()):
                if cmd.startswith(prefijo):
                    if veces <= 1:
                        del self._fallos[prefijo]
                    else:
                        self._fallos[prefijo] = (respuesta, veces - 1)
                    self._enviar(conexion, {"corrId": corr, "resp": respuesta})
                    return
        try:
            resp, despues = self._ejecutar(cmd)
        except Exception as e:  # a bug in the fake must show up in the test, not hang it
            resp, despues = _error("error", "commandError", message=f"fake: {type(e).__name__}"), None
        self._enviar(conexion, {"corrId": corr, "resp": resp})
        if despues is not None:
            despues()

    # --- commands ---

    def _ejecutar(self, cmd: str) -> tuple[dict[str, Any], Callable[[], None] | None]:
        activo = self.usuario_activo
        if cmd in ("/version", "/v"):
            return {
                "type": "versionInfo",
                "versionInfo": {"version": self.version, "simplexmqVersion": "6.5.0", "simplexmqCommit": ""},
                "chatMigrations": [{"upName": "m1"}],
                "agentMigrations": [{"upName": "m1"}],
            }, None
        if cmd in ("/user", "/u"):
            if activo is None:
                return _error("error", "noActiveUser"), None
            return {"type": "activeUser", "user": self._usuario_json(activo)}, None
        if cmd == "/users":
            return {
                "type": "usersList",
                "users": [{"user": self._usuario_json(u), "unreadCount": 0} for u in self.usuarios],
            }, None
        if m := re.fullmatch(r"/_user (\d+)", cmd):
            elegido = next((u for u in self.usuarios if u.user_id == int(m.group(1))), None)
            if elegido is None:
                return _error("errorStore", "userNotFound", userId=int(m.group(1))), None
            for u in self.usuarios:
                u.activo = u is elegido
            return {"type": "activeUser", "user": self._usuario_json(elegido)}, None
        if cmd.startswith("/_create user "):
            nuevo = json.loads(cmd[len("/_create user ") :])
            perfil = nuevo.get("profile") or {}
            nombre = perfil.get("displayName", "")
            if any(u.nombre == nombre for u in self.usuarios) or nombre in self.contactos.values():
                return _error("error", "userExists", contactName=nombre), None
            archivos = ((perfil.get("preferences") or {}).get("files") or {}).get("allow", "yes") != "no"
            u = UsuarioFalso(len(self.usuarios) + 1, nombre, archivos=archivos, bot=perfil.get("peerType") == "bot")
            for otro in self.usuarios:
                otro.activo = False
            u.activo = True
            self.usuarios.append(u)
            return {"type": "activeUser", "user": self._usuario_json(u)}, None
        if m := re.fullmatch(r"/_show_address (\d+)", cmd):
            uid = int(m.group(1))
            direccion = self.direcciones.get(uid)
            if direccion is None:
                return _error("errorStore", "userContactLinkNotFound"), None
            return {
                "type": "userContactLink",
                "user": self._usuario_json(self._por_id(uid)),
                "contactLink": {
                    "userContactLinkId": uid,
                    "connLinkContact": {
                        "connFullLink": direccion["link"] + "-completo",
                        "connShortLink": direccion["link"],
                    },
                    "shortLinkDataSet": True,
                    "shortLinkLargeDataSet": True,
                    "addressSettings": direccion["ajustes"],
                },
            }, None
        if m := re.fullmatch(r"/_address_settings (\d+)(?: pq_ratchet=(?:on|off))? (.*)", cmd, re.DOTALL):
            uid = int(m.group(1))
            if uid not in self.direcciones:
                return _error("errorStore", "userContactLinkNotFound"), None
            self.direcciones[uid]["ajustes"] = json.loads(m.group(2))
            return {
                "type": "userContactLinkUpdated",
                "user": self._usuario_json(self._por_id(uid)),
                "contactLink": {
                    "userContactLinkId": uid,
                    "connLinkContact": {"connFullLink": self.direcciones[uid]["link"]},
                    "addressSettings": self.direcciones[uid]["ajustes"],
                },
            }, None
        if m := re.fullmatch(r"/_address (\d+)(?: pq_ratchet=(?:on|off))?", cmd):
            uid = int(m.group(1))
            if uid in self.direcciones:
                return _error("errorStore", "duplicateContactLink"), None
            self.direcciones[uid] = {
                "link": f"https://smp.ejemplo.org/a#nueva-{uid}",
                "ajustes": {"businessAddress": False},
            }
            return {
                "type": "userContactLinkCreated",
                "user": self._usuario_json(self._por_id(uid)),
                "connLinkContact": {"connFullLink": self.direcciones[uid]["link"] + "-completo"},
            }, None
        if m := re.fullmatch(r"/_set accept group invitations (\d+) (on|off)", cmd):
            self._por_id(int(m.group(1))).auto_grupos = m.group(2) == "on"
            return {"type": "cmdOk", "user_": self._usuario_json(self._por_id(int(m.group(1))))}, None
        if m := re.fullmatch(r"/_ttl (\d+) (\d+)", cmd):
            self.ttl = int(m.group(2))
            return {"type": "cmdOk", "user_": self._usuario_json(self._por_id(int(m.group(1))))}, None
        if m := re.fullmatch(r"/_ttl (\d+)", cmd):
            return {
                "type": "chatItemTTL",
                "user": self._usuario_json(self._por_id(int(m.group(1)))),
                "chatItemTTL": self.ttl,
            }, None
        if m := re.fullmatch(r"/_servers (\d+)", cmd):
            return {
                "type": "userServers",
                "user": self._usuario_json(self._por_id(int(m.group(1)))),
                "userServers": [
                    {
                        "operator": None,
                        "smpServers": [
                            {"server": s, "preset": False, "enabled": True, "deleted": False}
                            for s in self.servidores_smp
                        ],
                        "xftpServers": [
                            {"server": s, "preset": False, "enabled": True, "deleted": False}
                            for s in self.servidores_xftp
                        ],
                        "chatRelays": [],
                    }
                ],
            }, None
        if m := re.fullmatch(r"/_send @(\d+)((?: (?:live|ttl|sign)=\S+)*) json (.*)", cmd, re.DOTALL):
            return self._enviar_mensajes(int(m.group(1)), json.loads(m.group(3))), None
        if m := re.fullmatch(r"/_update item @(\d+) (\d+)(?: live=on)? json (.*)", cmd, re.DOTALL):
            return self._editar(int(m.group(1)), int(m.group(2)), json.loads(m.group(3))), None
        if m := re.fullmatch(
            r"/freceive (\d+)((?: (?:approved_relays|encrypt|inline)=(?:on|off))*)(?: (.+))?", cmd, re.DOTALL
        ):
            return self._recibir_archivo(int(m.group(1)), m.group(3))
        if m := re.fullmatch(r"/fcancel (\d+)", cmd):
            archivo = self.archivos.get(int(m.group(1)))
            if archivo is None:
                return _error("errorStore", "fileNotFound", fileId=int(m.group(1))), None
            archivo.modo = "cancelado"
            return {
                "type": "rcvFileCancelled",
                "user": self._usuario_json(self._activo()),
                "chatItem_": archivo.item,
                "rcvFileTransfer": {"fileId": archivo.file_id},
            }, None
        if m := re.fullmatch(r"/_delete @(\d+)(?: (full|entity|messages))?(?: notify=off)?", cmd):
            cid = int(m.group(1))
            if cid not in self.contactos:
                return _error("errorStore", "contactNotFound", contactId=cid), None
            contacto = self._contacto_json(cid)
            del self.contactos[cid]
            self.borrados.append(cid)
            return {"type": "contactDeleted", "user": self._usuario_json(self._activo()), "contact": contacto}, None
        return _error("error", "commandError", message="Failed reading: empty"), None

    def _por_id(self, uid: int) -> UsuarioFalso:
        return next(u for u in self.usuarios if u.user_id == uid)

    def _enviar_mensajes(self, contacto: int, mensajes: list[dict[str, Any]]) -> dict[str, Any]:
        if contacto not in self.contactos:
            return _error("errorStore", "contactNotFound", contactId=contacto)
        elementos = []
        for mensaje in mensajes:
            contenido = mensaje["msgContent"]
            cita_id = mensaje.get("quotedItemId")
            cita = None
            if cita_id is not None:
                cita = self._texto_item(cita_id)
                if cita is None:
                    return _error("errorStore", "chatItemNotFound", itemId=cita_id)
            tamano = len(json.dumps({"content": contenido, "quote": cita}, ensure_ascii=False).encode())
            if tamano > LIMITE_MENSAJE:
                return _error("errorStore", "largeMsg")
            item = next(self._items)
            self.enviados.append(
                {"contacto": contacto, "texto": contenido.get("text", ""), "cita_id": cita_id, "item_id": item}
            )
            elementos.append(self._item_enviado(contacto, item, contenido, cita))
        return {"type": "newChatItems", "user": self._usuario_json(self._activo()), "chatItems": elementos}

    def _texto_item(self, item_id: int) -> str | None:
        """Text of a known item (sent by the bot or emitted as received); None when it does not exist."""
        for enviado in self.enviados:
            if enviado["item_id"] == item_id:
                return str(enviado["texto"])
        return self.recibidos.get(item_id)

    def _editar(self, contacto: int, item_id: int, actualizado: dict[str, Any]) -> dict[str, Any]:
        enviado = next((e for e in self.enviados if e["item_id"] == item_id and e["contacto"] == contacto), None)
        if enviado is None:
            return _error("error", "invalidChatItemUpdate")
        texto = actualizado["msgContent"].get("text", "")
        if texto == enviado["texto"]:
            return {
                "type": "chatItemNotChanged",
                "user": self._usuario_json(self._activo()),
                "chatItem": self._item_enviado(contacto, item_id, actualizado["msgContent"], None),
            }
        enviado["texto"] = texto
        self.editados.append({"contacto": contacto, "item_id": item_id, "texto": texto})
        return {
            "type": "chatItemUpdated",
            "user": self._usuario_json(self._activo()),
            "chatItem": self._item_enviado(contacto, item_id, actualizado["msgContent"], None),
        }

    def _recibir_archivo(self, file_id: int, ruta: str | None) -> tuple[dict[str, Any], Callable[[], None] | None]:
        archivo = self.archivos.get(file_id)
        if archivo is None:
            return _error("errorStore", "fileNotFound", fileId=file_id), None
        if ruta is None:
            return _error("error", "commandError", message="fake: file path required"), None
        if os.path.lexists(ruta):
            return _error("error", "fileAlreadyExists", filePath=ruta), None
        try:
            Path(ruta).write_bytes(b"")  # like the CLI: the empty file is created when the file is accepted
        except OSError as e:
            return _error("error", "fileWrite", filePath=ruta, message=str(e)), None
        archivo.ruta = ruta
        item = json.loads(json.dumps(archivo.item))
        item["chatItem"]["file"]["fileStatus"] = {"type": "rcvAccepted"}
        resp = {"type": "rcvFileAccepted", "user": self._usuario_json(self._activo()), "chatItem": item}
        return resp, lambda: self._programar_descarga(archivo)

    def _programar_descarga(self, archivo: ArchivoFalso) -> None:
        temporizador = threading.Timer(self.retraso_archivo_s, self._descargar, args=(archivo,))
        temporizador.daemon = True
        temporizador.start()

    def _descargar(self, archivo: ArchivoFalso) -> None:
        assert archivo.ruta is not None
        usuario = self._usuario_json(self._activo())
        if archivo.modo in ("nunca", "cancelado"):
            return
        item = json.loads(json.dumps(archivo.item))
        if archivo.modo == "error":
            Path(archivo.ruta).unlink(missing_ok=True)
            self.emitir(
                {
                    "type": "rcvFileError",
                    "user": usuario,
                    "chatItem_": item,
                    "agentError": {"type": "XFTP", "xftpErr": {"type": "AUTH"}},
                    "rcvFileTransfer": {"fileId": archivo.file_id},
                }
            )
            return
        self.emitir({"type": "rcvFileStart", "user": usuario, "chatItem": item})
        if archivo.modo == "enlace":
            Path(archivo.ruta).unlink()
            destino = Path(archivo.ruta).with_name("objetivo-del-enlace")
            destino.write_bytes(archivo.contenido)
            os.symlink(destino, archivo.ruta)
        elif archivo.modo == "crece":
            Path(archivo.ruta).write_bytes(archivo.contenido + b"\x00" * 1024)
        else:
            Path(archivo.ruta).write_bytes(archivo.contenido)
        item["chatItem"]["file"]["fileStatus"] = {"type": "rcvComplete"}
        item["chatItem"]["file"]["fileSource"] = {"filePath": archivo.ruta}
        self.emitir({"type": "rcvFileComplete", "user": usuario, "chatItem": item})


def configuracion_para(cli: ServidorSimplexFalso, carpeta: Path, **cambios: Any) -> Any:
    """A ``ConfiguracionSimplex`` pointing at the fake, with short timeouts and files under ``carpeta``."""
    from asistente.plataforma.mensajeria.simplex.configuracion import ConfiguracionSimplex

    base: dict[str, Any] = {
        "ws_url": cli.url,
        "bot_nombre": NOMBRE_BOT,
        "bienvenida": "",
        "archivos_dir": Path(carpeta) / "archivos",
        "timeout_conexion_s": 2.0,
        "timeout_comando_s": 2.0,
        "timeout_comando_red_s": 2.0,
        "timeout_archivo_s": 5.0,
        "ping_s": 0.0,
        "reconexion_base_s": 0.05,
        "reconexion_max_s": 0.2,
    }
    base.update(cambios)
    return ConfiguracionSimplex(**base)


__all__ = ["LIMITE_MENSAJE", "NOMBRE_BOT", "ServidorSimplexFalso", "configuracion_para"]

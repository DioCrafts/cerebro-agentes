"""The subset of the SimpleX Chat CLI WebSocket API this channel uses: command strings, tolerant models, error codes.

Source (see docs/mensajeria/simplex.md, «Protocolo»): the official bot API reference in the simplex-chat repository,
``bots/README.md`` and ``bots/api/{COMMANDS,EVENTS,TYPES}.md``, branch ``stable`` at commit
``cb4b50db169a2638c78e0b41730d5759c559b473`` (2026-11-16, simplex-chat 7.1.4), cross-checked against the same files
at tag ``v6.4.3`` (the first release with the bot API reference) and against the CLI's own WebSocket server
(``apps/simplex-chat/Server.hs``) and command parser (``src/Simplex/Chat/Library/Commands.hs``).

Wire format: a command is one WebSocket text message ``{"corrId": "<unique>", "cmd": "<command string>"}``; its
response is ``{"corrId": "<same>", "resp": {"type": "<tag>", ...}}``; an event is ``{"resp": {"type": "<tag>", ...}}``
(no corrId). A failed command answers ``{"type": "chatCmdError", "chatError": {...}}``.

Tolerance (why ``extra="ignore"`` here, unlike the project's ``extra="forbid"`` models): the official guide requires
bots to «allow additional properties in all types» and to «ignore records with unknown union tags and unknown enum
strings» — the CLI adds fields in every release. So every model ignores unknown fields; unknown event types and
undecodable records are ignored too, and counted (:class:`ContadorProtocolo`) so the operator can see drift.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import re
import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# Supported simplex-chat versions: from the first release whose bot API reference documents every command used here
# (v6.4.3) up to, not including, the next major version (unknown: its API may change). Checked with ``/version``.
VERSION_MINIMA: tuple[int, int, int] = (6, 4, 3)
VERSION_LIMITE: tuple[int, int, int] = (8, 0, 0)


def _version_texto(v: tuple[int, ...]) -> str:
    return ".".join(str(x) for x in v)


VERSION_MINIMA_TEXTO = _version_texto(VERSION_MINIMA)
VERSION_LIMITE_TEXTO = _version_texto(VERSION_LIMITE)

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")


def parsear_version(texto: str | None) -> tuple[int, int, int] | None:
    """``"7.1.4.0"`` → ``(7, 1, 4)``; None when it is not a version."""
    m = _VERSION_RE.match(texto or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def version_soportada(version: tuple[int, int, int] | None) -> bool:
    return version is not None and VERSION_MINIMA <= version < VERSION_LIMITE


# --- tolerant models (only the fields the channel reads) ---------------------------------------------------------


class _Tolerante(BaseModel):
    model_config = ConfigDict(extra="ignore", alias_generator=to_camel, populate_by_name=True, frozen=True)


class PerfilLocal(_Tolerante):
    display_name: str = ""
    peer_type: str | None = None


class Preferencia(_Tolerante):
    allow: str | None = None


class PreferenciasCompletas(_Tolerante):
    files: Preferencia | None = None
    voice: Preferencia | None = None


class Usuario(_Tolerante):
    """``User`` (TYPES.md)."""

    user_id: int
    local_display_name: str = ""
    profile: PerfilLocal = Field(default_factory=PerfilLocal)
    active_user: bool = False
    # Present from the release that added APISetUserAutoAcceptGroupInvitations; None on older ones.
    auto_accept_group_invitations: bool | None = None
    view_pwd_hash: dict[str, Any] | None = None  # set on hidden profiles (never selected by the channel)
    full_preferences: PreferenciasCompletas | None = None

    @property
    def nombre(self) -> str:
        return self.profile.display_name or self.local_display_name

    @property
    def admite_archivos(self) -> bool | None:
        prefs = self.full_preferences
        if prefs is None or prefs.files is None or prefs.files.allow is None:
            return None
        return prefs.files.allow != "no"


class InfoUsuario(_Tolerante):
    user: Usuario


class EnlaceCreado(_Tolerante):
    """``CreatedConnLink``: the short link (when the CLI made one) is what the apps show as a QR."""

    conn_full_link: str
    conn_short_link: str | None = None

    @property
    def enlace(self) -> str:
        return self.conn_short_link or self.conn_full_link


class DireccionUsuario(_Tolerante):
    """``UserContactLink``."""

    conn_link_contact: EnlaceCreado
    address_settings: dict[str, Any] = Field(default_factory=dict)


class Contacto(_Tolerante):
    contact_id: int
    local_display_name: str = ""
    profile: PerfilLocal = Field(default_factory=PerfilLocal)

    @property
    def nombre(self) -> str:
        return self.profile.display_name or self.local_display_name


class ContenidoMensaje(_Tolerante):
    """``MsgContent``: every variant carries ``text`` (the caption for images, files and voice notes)."""

    type: str
    text: str = ""


class TipoEtiquetado(_Tolerante):
    type: str


class ArchivoCI(_Tolerante):
    """``CIFile``. ``fileName`` comes from the sender: never used as a path."""

    file_id: int
    file_name: str = ""
    file_size: int = 0
    file_status: TipoEtiquetado | None = None


class CitaCI(_Tolerante):
    content: ContenidoMensaje | None = None


class MetaCI(_Tolerante):
    item_id: int
    item_ts: datetime | None = None
    created_at: datetime | None = None
    item_forwarded: dict[str, Any] | None = None
    item_deleted: dict[str, Any] | None = None


class ContenidoCI(_Tolerante):
    type: str
    msg_content: ContenidoMensaje | None = None


class ElementoChat(_Tolerante):
    chat_dir: TipoEtiquetado
    meta: MetaCI
    content: ContenidoCI
    quoted_item: CitaCI | None = None
    file: ArchivoCI | None = None


class InfoChat(_Tolerante):
    type: str
    contact: Contacto | None = None


class AElementoChat(_Tolerante):
    """``AChatItem``: the chat (direct contact, group…) and the item."""

    chat_info: InfoChat
    chat_item: ElementoChat


class ServidorUsuario(_Tolerante):
    server: str = ""
    enabled: bool = True
    deleted: bool = False


class ServidoresOperador(_Tolerante):
    smp_servers: list[ServidorUsuario] = Field(default_factory=list)
    xftp_servers: list[ServidorUsuario] = Field(default_factory=list)


# --- response / event tags -------------------------------------------------------------------------------------

R_VERSION = "versionInfo"
R_USUARIO_ACTIVO = "activeUser"
R_USUARIOS = "usersList"
R_DIRECCION = "userContactLink"
R_DIRECCION_CREADA = "userContactLinkCreated"
R_DIRECCION_ACTUALIZADA = "userContactLinkUpdated"
R_OK = "cmdOk"
R_NUEVOS = "newChatItems"
R_ACTUALIZADO = "chatItemUpdated"
R_SIN_CAMBIOS = "chatItemNotChanged"
R_ARCHIVO_ACEPTADO = "rcvFileAccepted"
R_ARCHIVO_ACEPTADO_CANCELADO = "rcvFileAcceptedSndCancelled"
R_CONTACTO_BORRADO = "contactDeleted"
R_SERVIDORES = "userServers"
R_TTL = "chatItemTTL"
R_ERROR_COMANDO = "chatCmdError"

E_NUEVOS = "newChatItems"
E_CONTACTO_CONECTADO = "contactConnected"
E_CONTACTO_BORRADO = "contactDeletedByContact"
E_ARCHIVO_COMPLETO = "rcvFileComplete"
E_ARCHIVO_ERROR = "rcvFileError"
E_ARCHIVO_AVISO = "rcvFileWarning"
E_ARCHIVO_CANCELADO_EMISOR = "rcvFileSndCancelled"
E_ARCHIVO_CANCELADO = "rcvFileCancelled"
E_INVITACION_GRUPO = "receivedGroupInvitation"
E_INVITACION_GRUPO_VIA = "receivedGroupInvitationViaGroup"

# Documented events (EVENTS.md) and common CLI output the channel deliberately ignores: not counted as unknown.
EVENTOS_IGNORADOS = frozenset(
    {
        "contactUpdated",
        "receivedContactRequest",
        "newMemberContactReceivedInv",
        "contactSndReady",
        "chatItemReaction",
        "chatItemsDeleted",
        "chatItemUpdated",
        "groupChatItemsDeleted",
        "chatItemsStatusesUpdated",
        "userJoinedGroup",
        "groupUpdated",
        "joinedGroupMember",
        "memberRole",
        "deletedMember",
        "leftMember",
        "deletedMemberUser",
        "groupDeleted",
        "connectedToGroupMember",
        "memberAcceptedByOther",
        "memberBlockedForAll",
        "groupMemberUpdated",
        "groupLinkDataUpdated",
        "groupRelayUpdated",
        "rcvFileDescrReady",
        "sndFileCompleteXFTP",
        "rcvFileStart",
        "rcvFileAccepted",
        "rcvFileProgressXFTP",
        "sndFileError",
        "sndFileWarning",
        "acceptingContactRequest",
        "acceptingBusinessRequest",
        "contactConnecting",
        "businessLinkConnecting",
        "joinedGroupMemberConnecting",
        "groupLinkConnecting",
        "hostConnected",
        "hostDisconnected",
        "subscriptionStatus",
        "serviceRequest",
        "serviceReplySent",
        "remoteCtrlSessionCode",
        "remoteCtrlStopped",
        "messageError",
        "chatError",
        "chatErrors",
        "chatSuspended",
        "contactsSubscribed",
        "contactSubSummary",
        "pendingSubSummary",
        "userContactSubSummary",
        "networkStatus",
        "networkStatuses",
        "chatItemsRead",
        "contactsMerged",
        "contactRatchetSync",
    }
)


# --- error codes ----------------------------------------------------------------------------------------------

_CODIGO_RE = re.compile(r"[^A-Za-z0-9_:]")


def codigo_error(resp: dict[str, Any]) -> str:
    """A short, content-free code for a ``chatCmdError``/``chatError`` record, e.g. ``errorStore:userContactLinkNotFound``.

    Only the union tags are read: the records' other fields (``message``, file paths, names) are never logged.
    """
    err = resp.get("chatError")
    if not isinstance(err, dict):
        return "desconocido"
    tipo = err.get("type")
    interno: Any = None
    if tipo == "error":
        interno = err.get("errorType")
    elif tipo == "errorStore":
        interno = err.get("storeError")
    elif tipo == "errorAgent":
        interno = err.get("agentError")
    sub = interno.get("type") if isinstance(interno, dict) else None
    texto = f"{tipo}:{sub}" if sub else str(tipo)
    return _CODIGO_RE.sub("_", texto)[:80]


ERROR_SIN_USUARIO = "error:noActiveUser"
ERROR_SIN_DIRECCION = "errorStore:userContactLinkNotFound"


# --- commands (exact syntax from COMMANDS.md) ------------------------------------------------------------------


def _json(obj: Any) -> str:
    # ASCII-only JSON: the command stays one plain line whatever the text holds (newlines, quotes, emoji…).
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def _entero(valor: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ValueError("id no válido")
    return valor


def cmd_version() -> str:
    """ShowVersion (CLI ``/version``; not in the bot reference, answered with ``versionInfo``)."""
    return "/version"


def cmd_usuario_activo() -> str:
    """ShowActiveUser → ``activeUser`` | chatCmdError ``noActiveUser``."""
    return "/user"


def cmd_usuarios() -> str:
    """ListUsers → ``usersList`` (``users: [UserInfo]``)."""
    return "/users"


def cmd_activar_usuario(user_id: int) -> str:
    """APISetActiveUser → ``activeUser``."""
    return f"/_user {_entero(user_id)}"


def perfil_bot(nombre: str, *, archivos: bool, voz: bool) -> dict[str, Any]:
    """``Profile`` of a bot (``peerType: "bot"``), files/voice allowed as configured, calls off."""
    return {
        "displayName": nombre,
        "fullName": "",
        "peerType": "bot",
        "preferences": {
            "files": {"allow": "yes" if archivos else "no"},
            "voice": {"allow": "yes" if voz else "no"},
            "calls": {"allow": "no"},
        },
    }


def cmd_crear_usuario(perfil: dict[str, Any]) -> str:
    """CreateActiveUser ``/_create user <json(newUser)>`` → ``activeUser``.

    ``userChatRelay`` and ``clientService`` are required by 7.x and ignored (unknown fields) by 6.4.x.
    """
    return "/_create user " + _json(
        {"profile": perfil, "pastTimestamp": False, "userChatRelay": False, "clientService": False}
    )


def cmd_mostrar_direccion(user_id: int) -> str:
    """APIShowMyAddress → ``userContactLink`` | chatCmdError ``errorStore/userContactLinkNotFound``."""
    return f"/_show_address {_entero(user_id)}"


def cmd_crear_direccion(user_id: int) -> str:
    """APICreateMyAddress → ``userContactLinkCreated`` (network: interactive)."""
    return f"/_address {_entero(user_id)}"


def cmd_ajustes_direccion(user_id: int, ajustes: dict[str, Any]) -> str:
    """APISetAddressSettings ``/_address_settings <userId> <json(settings)>`` → ``userContactLinkUpdated``."""
    return f"/_address_settings {_entero(user_id)} " + _json(ajustes)


def cmd_invitaciones_grupo(user_id: int, activar: bool) -> str:
    """APISetUserAutoAcceptGroupInvitations → ``cmdOk``."""
    return f"/_set accept group invitations {_entero(user_id)} {'on' if activar else 'off'}"


def cmd_servidores(user_id: int) -> str:
    """APIGetUserServers → ``userServers``."""
    return f"/_servers {_entero(user_id)}"


def cmd_ttl(user_id: int) -> str:
    """APIGetChatItemTTL ``/_ttl <userId>`` → ``chatItemTTL`` (``chatItemTTL``: seconds, 0 = keep forever).

    Not in the bot API reference: taken from the CLI's command parser (``Commands.hs``); handled tolerantly.
    """
    return f"/_ttl {_entero(user_id)}"


def cmd_fijar_ttl(user_id: int, segundos: int) -> str:
    """APISetChatItemTTL ``/_ttl <userId> <seconds>`` → ``cmdOk``: the CLI deletes older messages (and their files)."""
    if isinstance(segundos, bool) or not isinstance(segundos, int) or segundos < 0:
        raise ValueError("ttl no válido")
    return f"/_ttl {_entero(user_id)} {segundos}"


def cmd_enviar_texto(contacto_id: int, texto: str, cita_id: int | None = None) -> str:
    """APISendMessages ``/_send @<contactId> json <json([ComposedMessage])>`` → ``newChatItems``."""
    mensaje: dict[str, Any] = {"msgContent": {"type": "text", "text": texto}, "mentions": {}}
    if cita_id is not None:
        mensaje["quotedItemId"] = _entero(cita_id)
    return f"/_send @{_entero(contacto_id)} json " + _json([mensaje])


def cmd_editar_texto(contacto_id: int, item_id: int, texto: str) -> str:
    """APIUpdateChatItem ``/_update item @<contactId> <chatItemId> json <json(UpdatedMessage)>``."""
    return f"/_update item @{_entero(contacto_id)} {_entero(item_id)} json " + _json(
        {"msgContent": {"type": "text", "text": texto}, "mentions": {}}
    )


def cmd_recibir_archivo(file_id: int, ruta: str) -> str:
    """ReceiveFile ``/freceive <fileId> approved_relays=on encrypt=off <filePath>`` → ``rcvFileAccepted``.

    ``filePath`` is a NEW file path we choose (the CLI creates exactly it when it does not exist; a directory would
    make it use the sender's file name). ``encrypt=off``: the channel must read and verify the plain file.
    ``approved_relays=on`` as the official clients do: the file comes from the XFTP relays the sender's app uses.
    """
    if not ruta or any(c in ruta for c in "\r\n\x00"):
        raise ValueError("ruta no válida")
    return f"/freceive {_entero(file_id)} approved_relays=on encrypt=off {ruta}"


def cmd_cancelar_archivo(file_id: int) -> str:
    """CancelFile → ``rcvFileCancelled``."""
    return f"/fcancel {_entero(file_id)}"


def cmd_borrar_contacto(contacto_id: int) -> str:
    """APIDeleteChat ``/_delete @<contactId> full`` (contact and conversation; the contact is notified)."""
    return f"/_delete @{_entero(contacto_id)} full"


def verbo(cmd: str) -> str:
    """The command's first word (``/_send``), the only part of a command that may be logged."""
    return cmd.split(" ", 1)[0][:40]


# --- counters ------------------------------------------------------------------------------------------------


class ContadorProtocolo:
    """Thread-safe counts of what the channel could not use: unknown event tags, undecodable frames and records,
    and events dropped because the event queue was full. Only tags and numbers are kept, never content."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.eventos_desconocidos: Counter[str] = Counter()
        self.tramas_invalidas = 0
        self.registros_invalidos = 0
        self.eventos_descartados = 0
        self.respuestas_huerfanas = 0

    def evento_desconocido(self, tipo: str) -> None:
        with self._lock:
            self.eventos_desconocidos[_CODIGO_RE.sub("_", tipo)[:64]] += 1

    def trama_invalida(self) -> None:
        with self._lock:
            self.tramas_invalidas += 1

    def registro_invalido(self) -> None:
        with self._lock:
            self.registros_invalidos += 1

    def evento_descartado(self) -> None:
        with self._lock:
            self.eventos_descartados += 1

    def respuesta_huerfana(self) -> None:
        with self._lock:
            self.respuestas_huerfanas += 1

    def resumen(self) -> dict[str, Any]:
        with self._lock:
            return {
                "eventos_desconocidos": dict(self.eventos_desconocidos),
                "tramas_invalidas": self.tramas_invalidas,
                "registros_invalidos": self.registros_invalidos,
                "eventos_descartados": self.eventos_descartados,
                "respuestas_huerfanas": self.respuestas_huerfanas,
            }


__all__ = [
    "AElementoChat",
    "ArchivoCI",
    "ContadorProtocolo",
    "Contacto",
    "DireccionUsuario",
    "EnlaceCreado",
    "InfoUsuario",
    "ServidoresOperador",
    "Usuario",
    "VERSION_LIMITE",
    "VERSION_MINIMA",
    "codigo_error",
    "parsear_version",
    "version_soportada",
]

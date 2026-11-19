"""``CanalSimplex``: the SimpleX Chat adapter of the messaging gateway (implements ``contrato.Canal``).

It talks to a simplex-chat CLI running on this machine in WebSocket mode (``simplex-chat -p 5225``) with a dedicated
bot profile, so the household can reach the brain from the SimpleX app: end-to-end encrypted, no phone number. The
adapter only moves messages (contract): it never decides who may talk to the brain, never calls a model and never
interprets the text it carries.

Lifecycle (``iniciar`` runs until ``parar``): connect → check the CLI version is supported → make sure the active
profile is the bot's (created with the configured name if absent; other profiles are never modified) → group
invitations off → the bot's contact address exists (created if needed) with auto-accept and the welcome message as
configured → publish the address in ``estado().direccion`` (the app turns it into a QR) → process events until the
connection drops → reconnect with jittered exponential backoff, forever, until ``parar``.

Receiving: only direct messages from contacts of the bot profile (groups, other profiles, our own items, deleted
items are ignored), deduplicated by chat item id. Text is truncated at the contract's maximum (flagged); forwarded and
quoted content is flagged/passed as data. Files go through ``archivos`` (size and type gates, a path we choose,
verification after download) and the message waits for its file; a file refused or lost on the way is reported in
``adjuntos_rechazados`` (``demasiado_grande``, ``tipo_no_permitido``, ``error_descarga``, ``tiempo_agotado``) and its
message is delivered all the same, text or not; later messages from the same contact wait behind it
so the gateway sees them in the order they were sent. The handler runs on worker threads (``ejecutor``), in order per
contact; its exceptions never reach the event loop.

Sending: split at the channel's limit (characters, and bytes of the encoded message: SimpleX caps a message at about
13 KB with post-quantum encryption, the quoted message travelling inside it), reply-to on the first part, edits in
place. ``enviar``/``editar``/``desconectar_contacto`` never raise: they log and return None/False.

Privacy: logs carry contact ids, chat item ids, event types, sizes and error codes — never message text, quoted text,
file names or display names.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import random
import re
import threading
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from asistente.plataforma.mensajeria.contrato import (
    MAX_TEXTO_ENTRANTE,
    AdjuntoEntrante,
    CapacidadesCanal,
    EstadoCanal,
    Manejador,
    MensajeEntrante,
    MensajeSaliente,
    NombreCanal,
)
from asistente.plataforma.mensajeria.simplex import protocolo as p
from asistente.plataforma.mensajeria.simplex.archivos import (
    RECHAZO_DESCARGA,
    RECHAZO_TIEMPO,
    ArchivoRechazado,
    RecepcionArchivos,
)
from asistente.plataforma.mensajeria.simplex.cliente import (
    ClienteSimplex,
    ErrorClienteSimplex,
    ErrorComando,
    ErrorDesconectado,
)
from asistente.plataforma.mensajeria.simplex.configuracion import ConfiguracionSimplex, validar_url
from asistente.plataforma.mensajeria.simplex.ejecutor import EjecutorPorClave, describir_excepcion

logger = logging.getLogger(__name__)
_M = TypeVar("_M", bound=BaseModel)

# Characters per outgoing message (what the gateway formats to) and the byte budget of one encoded message's text
# (the CLI rejects a message whose encoding exceeds 13 380 bytes with post-quantum encryption, 15 602 without:
# ``maxEncodedMsgLengthPQ``/``maxEncodedMsgLength`` in the CLI's Protocol.hs; the rest is left for the envelope).
MAX_TEXTO_CANAL = 4000
PRESUPUESTO_BYTES = 12_000
MAX_CITA = 4000
MAX_ARCHIVOS_PENDIENTES = 20
MAX_EN_ESPERA_POR_CONTACTO = 100
_VISTOS_MAX = 20_000
_CITAS_MAX = 5_000
_LIMPIEZA_CADA_S = 3600.0
_TIPOS_CONTENIDO = frozenset({"text", "link", "image", "video", "voice", "file", "report", "chat", "unknown"})


class VersionNoSoportada(ErrorClienteSimplex):
    """The CLI's version is outside the supported range (``protocolo.VERSION_MINIMA``…``VERSION_LIMITE``)."""


# --- text splitting ------------------------------------------------------------------------------------------------


def bytes_json(texto: str) -> int:
    """Bytes the text takes inside the CLI's JSON message encoding (UTF-8, escaped)."""
    return len(json.dumps(texto, ensure_ascii=False).encode("utf-8")) - 2


_CORTES = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


def dividir_texto(texto: str, max_caracteres: int = MAX_TEXTO_CANAL, max_bytes: int = PRESUPUESTO_BYTES) -> list[str]:
    """Split ``texto`` into parts that each fit both limits, preferring paragraph, line, sentence and word breaks."""

    def cabe(s: str) -> bool:
        return len(s) <= max_caracteres and bytes_json(s) <= max_bytes

    partes: list[str] = []
    resto = texto
    while resto:
        if cabe(resto):
            partes.append(resto)
            break
        bajo, alto = 1, min(len(resto), max_caracteres)
        while bajo < alto:  # the longest prefix that fits (fitting is monotonic in the prefix length)
            medio = (bajo + alto + 1) // 2
            if cabe(resto[:medio]):
                bajo = medio
            else:
                alto = medio - 1
        corte = bajo
        prefijo = resto[:bajo]
        for separador in _CORTES:
            pos = prefijo.rfind(separador)
            if pos >= bajo // 2:  # a natural break, not too early (no tiny parts)
                corte = pos + len(separador)
                break
        parte = resto[:corte].rstrip()
        if not parte:
            parte, corte = resto[:bajo], bajo
        partes.append(parte)
        resto = resto[corte:].lstrip("\n ")
    return partes or [texto]


# --- small helpers -------------------------------------------------------------------------------------------------


class _Acotado:
    """A thread-safe LRU map with a size bound (dedupe ids, sizes of quotable messages)."""

    def __init__(self, maximo: int) -> None:
        self._maximo = maximo
        self._datos: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    def agregar(self, clave: str, valor: int = 0) -> bool:
        """Store ``clave``; False when it was already there (then only refreshed)."""
        with self._lock:
            if clave in self._datos:
                self._datos.move_to_end(clave)
                return False
            self._datos[clave] = valor
            while len(self._datos) > self._maximo:
                self._datos.popitem(last=False)
            return True

    def obtener(self, clave: str) -> int | None:
        with self._lock:
            return self._datos.get(clave)


def _id_numerico(valor: str | None) -> int | None:
    if valor is None or not re.fullmatch(r"[1-9][0-9]{0,18}", valor.strip()):
        return None
    return int(valor.strip())


def _hosts(servidor: str) -> set[str]:
    """Hosts of an SMP/XFTP server string (``smp://<fingerprint>[:<password>]@host1,host2[:port]``)."""
    resto = servidor.split("://", 1)[-1]
    resto = resto.rsplit("@", 1)[-1]
    return {h.split(":", 1)[0].strip().lower() for h in resto.split(",") if h.strip()}


@dataclass
class _ArchivoPendiente:
    """A received message waiting for its file (and blocking the later messages of its contact)."""

    file_id: int
    contacto: str
    base: dict[str, Any]
    ruta: Path
    nombre: str
    tamano: int
    clase: str
    caduca: float
    listo: MensajeEntrante | None = None


# --- the channel ---------------------------------------------------------------------------------------------------


class CanalSimplex:
    """See the module docstring. Thread-safe; one instance per CLI."""

    nombre: NombreCanal = "simplex"

    @classmethod
    def desde_settings(cls) -> CanalSimplex:
        """The channel as configured in ``settings`` (block ``# --- mensajeria.simplex ---``)."""
        return cls(ConfiguracionSimplex.desde_settings())

    def __init__(self, configuracion: ConfiguracionSimplex | None = None) -> None:
        # Raises ConfiguracionSimplexInvalida (clear Spanish message) on unusable settings, e.g. a remote ws:// URL;
        # checked again here so that a configuration built without validation cannot skip the gate.
        self._cfg = configuracion or ConfiguracionSimplex.desde_settings()
        validar_url(self._cfg.ws_url, self._cfg.permitir_remoto)
        self.capacidades = CapacidadesCanal(
            edicion=True,
            adjuntos_entrantes=True,
            adjuntos_salientes=False,
            max_texto=MAX_TEXTO_CANAL,
            cifrado_extremo_a_extremo=True,
            formato="simplex",
        )
        self.contador = p.ContadorProtocolo()
        self._recepcion = RecepcionArchivos(
            self._cfg.archivos_dir, self._cfg.recepcion, self._cfg.max_bytes_archivo, self._cfg.aceptar_voz
        )
        self._lock = threading.RLock()
        self._parar = threading.Event()
        self._terminado = threading.Event()
        self._terminado.set()
        self._en_marcha = False
        self._parada_pendiente = False
        self._manejador: Manejador | None = None
        self._ejecutor: EjecutorPorClave | None = None
        self._cliente: ClienteSimplex | None = None
        self._preparado = False
        self._usuario_id: int | None = None
        self._version: str | None = None
        self._direccion: str | None = None
        self._detalle = "Sin iniciar."
        self._ultimo_error: str | None = None
        self._actualizado = datetime.now(UTC)
        self._vistos = _Acotado(_VISTOS_MAX)
        self._tamanos_cita = _Acotado(_CITAS_MAX)
        # Only the event loop's thread touches these two.
        self._por_fichero: dict[int, _ArchivoPendiente] = {}
        self._en_espera: dict[str, deque[MensajeEntrante | _ArchivoPendiente]] = {}
        self._proxima_limpieza = 0.0

    # --- contract: lifecycle ---

    def iniciar(self, manejador: Manejador) -> None:
        with self._lock:
            if self._en_marcha:
                logger.warning("simplex: iniciar() ya estaba en marcha; se ignora la segunda llamada")
                return
            if self._parada_pendiente:
                # parar() arrived before this start (a stop racing a start): honour it instead of running forever.
                self._parada_pendiente = False
                logger.info("simplex: parada pedida antes de iniciar; no se arranca")
                return
            self._en_marcha = True
            self._parar.clear()
            self._terminado.clear()
        intento = 0
        try:
            self._manejador = manejador
            self._ejecutor = EjecutorPorClave(self._cfg.manejadores_max, self._cfg.pendientes_max)
            error_archivos = self._recepcion.preparar()
            if error_archivos:
                logger.error("simplex: recepción de archivos desactivada: %s", error_archivos)
            self._limpiar_archivos()
            while not self._parar.is_set():
                espera: float | None = None
                try:
                    self._conectar_y_preparar()
                    intento = 0
                    self._bucle_eventos()
                except VersionNoSoportada as e:
                    logger.error("simplex: %s", e)
                    self._marcar_error(str(e))
                    espera = self._cfg.reconexion_max_s  # the operator has to upgrade: no point in hurrying
                except ErrorClienteSimplex as e:
                    if not self._parar.is_set():
                        logger.warning("simplex: %s", e)
                        self._marcar_error(str(e))
                except Exception as e:  # a bug must not end the channel: log where, reconnect
                    logger.error("simplex: error inesperado: %s", describir_excepcion(e))
                    self._marcar_error(f"Error inesperado del canal ({type(e).__name__}).")
                finally:
                    self._soltar_cliente()
                if self._parar.is_set():
                    break
                intento += 1
                if espera is None:
                    espera = self._espera(intento)
                logger.info("simplex: nuevo intento de conexión en %.1f s", espera)
                self._parar.wait(espera)
        finally:
            self._cerrar_todo()

    def parar(self) -> None:
        with self._lock:
            self._parar.set()
            if not self._en_marcha:
                self._parada_pendiente = True
                return
            cliente = self._cliente
        if cliente is not None:
            cliente.cerrar()
        ejecutor = self._ejecutor
        if ejecutor is not None and ejecutor.es_hilo_propio():
            return  # called from the handler: iniciar() finishes on its own; waiting here would wait for ourselves
        self._terminado.wait(20)

    # --- contract: sending ---

    def enviar(self, mensaje: MensajeSaliente) -> str | None:
        try:
            return self._enviar(mensaje)
        except Exception as e:
            logger.error("simplex: fallo inesperado al enviar: %s", describir_excepcion(e))
            return None

    def editar(self, contacto: str, mensaje_id: str, texto: str) -> bool:
        try:
            return self._editar(contacto, mensaje_id, texto)
        except Exception as e:
            logger.error("simplex: fallo inesperado al editar: %s", describir_excepcion(e))
            return False

    def desconectar_contacto(self, contacto: str) -> bool:
        try:
            cid = _id_numerico(contacto)
            cliente = self._cliente_listo()
            if cid is None or cliente is None:
                logger.warning(
                    "simplex: no se puede borrar el contacto (%s)", "id no válido" if cid is None else "sin conexión"
                )
                return False
            try:
                resp = cliente.comando(p.cmd_borrar_contacto(cid), timeout=self._cfg.timeout_comando_s)
            except ErrorClienteSimplex as e:
                logger.warning("simplex: borrar contacto=%s falló (%s)", cid, self._codigo(e))
                return False
            if resp.get("type") != p.R_CONTACTO_BORRADO:
                logger.warning(
                    "simplex: borrar contacto=%s: respuesta inesperada (%s)", cid, str(resp.get("type"))[:64]
                )
                return False
            logger.info("simplex: contacto=%s borrado", cid)
            return True
        except Exception as e:
            logger.error("simplex: fallo inesperado al borrar un contacto: %s", describir_excepcion(e))
            return False

    def estado(self) -> EstadoCanal:
        with self._lock:
            cliente = self._cliente
            conectado = bool(self._preparado and cliente is not None and cliente.conectado)
            detalle = self._detalle
            descartados = self.contador.eventos_descartados
            if descartados:
                detalle += f" Eventos descartados por sobrecarga: {descartados}."
            return EstadoCanal(
                canal="simplex",
                conectado=conectado,
                detalle=detalle,
                version=self._version,
                direccion=self._direccion,
                ultimo_error=self._ultimo_error,
                actualizado=self._actualizado,
            )

    # --- connection and start-up ---

    def _crear_cliente(self) -> ClienteSimplex:
        return ClienteSimplex(
            self._cfg.ws_url,
            timeout_conexion=self._cfg.timeout_conexion_s,
            ping_s=self._cfg.ping_s,
            max_eventos=self._cfg.cola_eventos_max,
            contador=self.contador,
        )

    def _espera(self, intento: int) -> float:
        """Exponential backoff with jitter (half fixed, half random) so restarts do not hammer the CLI in step."""
        tope = min(self._cfg.reconexion_max_s, self._cfg.reconexion_base_s * float(2 ** min(intento - 1, 16)))
        return tope / 2 + random.uniform(0, tope / 2)

    def _conectar_y_preparar(self) -> None:
        self._poner(detalle=f"Conectando con simplex-chat en {self._cfg.destino}…")
        cliente = self._crear_cliente()
        with self._lock:
            if self._parar.is_set():
                return
            self._cliente = cliente
            self._preparado = False
        cliente.conectar()
        version = self._comprobar_version(cliente)
        usuario = self._asegurar_perfil(cliente)
        avisos: list[str] = []
        self._evitar_grupos(cliente, usuario, avisos)
        if usuario.admite_archivos is False:
            avisos.append(
                "El perfil del bot no admite archivos: créalo con --create-bot-allow-files o actívalos en su perfil."
            )
        direccion = self._asegurar_direccion(cliente, usuario)
        self._ajustar_historial(cliente, usuario, avisos)
        self._comprobar_servidores(cliente, usuario, avisos)
        if self._recepcion.error:
            avisos.append(self._recepcion.error)
        detalle = (
            f"Conectado a simplex-chat {version} en {self._cfg.destino} con el perfil «{usuario.nombre}»."
            + "".join(f" Aviso: {a}" for a in avisos)
        )
        with self._lock:
            self._usuario_id = usuario.user_id
            self._version = version
            self._direccion = direccion
            self._preparado = True
            self._ultimo_error = None
        self._poner(detalle=detalle)
        logger.info("simplex: conectado (versión %s, perfil %s, avisos %d)", version, usuario.user_id, len(avisos))

    def _comprobar_version(self, cliente: ClienteSimplex) -> str:
        resp = cliente.comando(p.cmd_version(), timeout=self._cfg.timeout_comando_s)
        self._exigir(resp, p.R_VERSION, "/version")
        info = resp.get("versionInfo")
        texto = info.get("version") if isinstance(info, dict) else None
        texto = re.sub(r"[^0-9A-Za-z.\-]", "", str(texto or ""))[:32] or None
        if not p.version_soportada(p.parsear_version(texto)):
            raise VersionNoSoportada(
                f"Versión de simplex-chat no compatible: {texto or 'desconocida'} (se admite desde la "
                f"{p.VERSION_MINIMA_TEXTO} y anteriores a la {p.VERSION_LIMITE_TEXTO}). Actualiza simplex-chat o "
                "este programa."
            )
        assert texto is not None
        return texto

    def _asegurar_perfil(self, cliente: ClienteSimplex) -> p.Usuario:
        """The bot's profile becomes the active one: found by display name, else created. Others are untouched."""
        nombre = self._cfg.bot_nombre
        timeout = self._cfg.timeout_comando_s
        try:
            resp = cliente.comando(p.cmd_usuario_activo(), timeout=timeout)
            self._exigir(resp, p.R_USUARIO_ACTIVO, "/user")
            activo: p.Usuario | None = self._modelo(p.Usuario, resp.get("user"), "/user")
        except ErrorComando as e:
            if e.codigo != p.ERROR_SIN_USUARIO:
                raise
            activo = None
        if activo is not None and activo.nombre == nombre:
            return activo
        resp = cliente.comando(p.cmd_usuarios(), timeout=timeout)
        self._exigir(resp, p.R_USUARIOS, "/users")
        candidatos: list[p.Usuario] = []
        for bruto in resp.get("users") or []:
            try:
                usuario = p.InfoUsuario.model_validate(bruto).user
            except ValidationError:
                self.contador.registro_invalido()
                continue
            if usuario.nombre == nombre and usuario.view_pwd_hash is None:
                candidatos.append(usuario)
        if candidatos:
            elegido = sorted(candidatos, key=lambda u: (u.profile.peer_type != "bot", u.user_id))[0]
            resp = cliente.comando(p.cmd_activar_usuario(elegido.user_id), timeout=timeout)
            self._exigir(resp, p.R_USUARIO_ACTIVO, "/_user")
            logger.info("simplex: perfil del bot %s activado", elegido.user_id)
            return self._modelo(p.Usuario, resp.get("user"), "/_user")
        perfil = p.perfil_bot(nombre, archivos=True, voz=self._cfg.aceptar_voz)
        resp = cliente.comando(p.cmd_crear_usuario(perfil), timeout=timeout)
        self._exigir(resp, p.R_USUARIO_ACTIVO, "/_create user")
        creado = self._modelo(p.Usuario, resp.get("user"), "/_create user")
        logger.info("simplex: perfil del bot creado (%s)", creado.user_id)
        return creado

    def _evitar_grupos(self, cliente: ClienteSimplex, usuario: p.Usuario, avisos: list[str]) -> None:
        """The bot never joins groups on its own (an invitation would put the brain in a room of strangers)."""
        if usuario.auto_accept_group_invitations is not True:
            return
        try:
            resp = cliente.comando(
                p.cmd_invitaciones_grupo(usuario.user_id, False), timeout=self._cfg.timeout_comando_s
            )
            self._exigir(resp, p.R_OK, "/_set accept group invitations")
            logger.info("simplex: aceptación automática de grupos desactivada")
        except ErrorDesconectado:
            raise
        except ErrorClienteSimplex as e:
            avisos.append(f"No se pudo desactivar la aceptación automática de grupos ({self._codigo(e)}).")

    def _asegurar_direccion(self, cliente: ClienteSimplex, usuario: p.Usuario) -> str:
        uid = usuario.user_id
        timeout = self._cfg.timeout_comando_s
        red = max(timeout, self._cfg.timeout_comando_red_s)
        try:
            resp = cliente.comando(p.cmd_mostrar_direccion(uid), timeout=timeout)
        except ErrorComando as e:
            if e.codigo != p.ERROR_SIN_DIRECCION:
                raise
            logger.info("simplex: el perfil del bot no tiene dirección; se crea")
            creada = cliente.comando(p.cmd_crear_direccion(uid), timeout=red)
            self._exigir(creada, p.R_DIRECCION_CREADA, "/_address")
            resp = cliente.comando(p.cmd_mostrar_direccion(uid), timeout=timeout)
        self._exigir(resp, p.R_DIRECCION, "/_show_address")
        direccion = self._modelo(p.DireccionUsuario, resp.get("contactLink"), "/_show_address")
        actuales = dict(direccion.address_settings)
        deseados = self._ajustes_deseados(actuales)
        if deseados != actuales:
            resp = cliente.comando(p.cmd_ajustes_direccion(uid, deseados), timeout=red)
            self._exigir(resp, p.R_DIRECCION_ACTUALIZADA, "/_address_settings")
            logger.info(
                "simplex: ajustes de la dirección actualizados (autoaceptar=%s, bienvenida=%s)",
                self._cfg.autoaceptar,
                bool(self._cfg.bienvenida),
            )
        return direccion.conn_link_contact.enlace

    def _ajustes_deseados(self, actuales: dict[str, Any]) -> dict[str, Any]:
        """``AddressSettings`` as configured; fields we do not manage (e.g. ``chatLayout``) are kept as they are."""
        deseados = dict(actuales)
        deseados["businessAddress"] = False
        if self._cfg.autoaceptar:
            auto = actuales.get("autoAccept")
            auto = dict(auto) if isinstance(auto, dict) else {}
            auto["acceptIncognito"] = False
            deseados["autoAccept"] = auto
        else:
            deseados.pop("autoAccept", None)
        if self._cfg.bienvenida:
            deseados["autoReply"] = {"type": "text", "text": self._cfg.bienvenida}
        else:
            deseados.pop("autoReply", None)
        return deseados

    def _ajustar_historial(self, cliente: ClienteSimplex, usuario: p.Usuario, avisos: list[str]) -> None:
        """Privacy: the CLI keeps every message in plain text in its database; it is told to delete the bot's
        messages older than SIMPLEX_HISTORIAL_DIAS (the gateway keeps what it needs on its own side)."""
        dias = self._cfg.historial_dias
        if dias <= 0:
            return
        segundos = dias * 86400
        timeout = self._cfg.timeout_comando_s
        try:
            resp = cliente.comando(p.cmd_ttl(usuario.user_id), timeout=timeout)
            self._exigir(resp, p.R_TTL, "/_ttl")
            if resp.get("chatItemTTL") == segundos:
                return
            resp = cliente.comando(p.cmd_fijar_ttl(usuario.user_id, segundos), timeout=timeout)
            self._exigir(resp, p.R_OK, "/_ttl")
            logger.info("simplex: la CLI borrará los mensajes de más de %d días", dias)
        except ErrorDesconectado:
            raise
        except ErrorClienteSimplex as e:
            avisos.append(f"No se pudo fijar cuánto guarda simplex-chat los mensajes ({self._codigo(e)}).")

    def _comprobar_servidores(self, cliente: ClienteSimplex, usuario: p.Usuario, avisos: list[str]) -> None:
        """With SIMPLEX_SERVIDORES_SMP/XFTP set, warn when the CLI has other servers enabled (privacy promise)."""
        if not (self._cfg.servidores_smp or self._cfg.servidores_xftp):
            return
        try:
            resp = cliente.comando(p.cmd_servidores(usuario.user_id), timeout=self._cfg.timeout_comando_s)
            self._exigir(resp, p.R_SERVIDORES, "/_servers")
            grupos = [p.ServidoresOperador.model_validate(g) for g in resp.get("userServers") or []]
        except ErrorDesconectado:
            raise
        except (ErrorClienteSimplex, ValidationError):
            avisos.append("No se pudo comprobar qué servidores SMP/XFTP usa simplex-chat.")
            return
        for etiqueta, configurados, activos in (
            ("SMP", self._cfg.servidores_smp, [s for g in grupos for s in g.smp_servers]),
            ("XFTP", self._cfg.servidores_xftp, [s for g in grupos for s in g.xftp_servers]),
        ):
            if not configurados:
                continue
            esperados = set().union(*(_hosts(s) for s in configurados))
            en_uso = set().union(*(_hosts(s.server) for s in activos if s.enabled and not s.deleted), set())
            if en_uso - esperados:
                avisos.append(
                    f"simplex-chat tiene activos servidores {etiqueta} que no están en SIMPLEX_SERVIDORES_{etiqueta}."
                )
            elif not en_uso & esperados:
                avisos.append(f"simplex-chat no usa ninguno de los servidores de SIMPLEX_SERVIDORES_{etiqueta}.")

    # --- event loop ---

    def _bucle_eventos(self) -> None:
        cliente = self._cliente
        if cliente is None:
            raise ErrorDesconectado("sin conexión con simplex-chat")
        while not self._parar.is_set():
            evento = cliente.siguiente_evento(timeout=0.25)
            if evento is not None:
                self._procesar_evento_seguro(evento)
            self._revisar_caducados()
            if time.monotonic() >= self._proxima_limpieza:
                self._limpiar_archivos()
            if evento is None and not cliente.conectado:
                break  # disconnected and every queued event processed
        if not self._parar.is_set():
            raise ErrorDesconectado("se perdió la conexión con simplex-chat")

    def _procesar_evento_seguro(self, resp: dict[str, Any]) -> None:
        try:
            self._procesar_evento(resp)
        except Exception as e:  # one bad event must not stop the others
            self.contador.registro_invalido()
            logger.error(
                "simplex: error procesando un evento %s: %s", str(resp.get("type"))[:64], describir_excepcion(e)
            )

    def _procesar_evento(self, resp: dict[str, Any]) -> None:
        tipo = resp.get("type")
        if not isinstance(tipo, str):
            self.contador.registro_invalido()
            return
        usuario = resp.get("user")
        if isinstance(usuario, dict) and usuario.get("userId") not in (None, self._usuario_id):
            logger.debug("simplex: evento %s de otro perfil; ignorado", tipo[:64])
            return
        if tipo == p.E_NUEVOS:
            self._nuevos_elementos(resp)
        elif tipo == p.E_ARCHIVO_COMPLETO:
            self._archivo_completo(resp)
        elif tipo in (p.E_ARCHIVO_ERROR, p.E_ARCHIVO_CANCELADO_EMISOR, p.E_ARCHIVO_CANCELADO):
            self._archivo_fallido(resp, tipo)
        elif tipo == p.E_ARCHIVO_AVISO:
            logger.info("simplex: aviso al recibir un archivo (archivo=%s)", self._id_archivo(resp))
        elif tipo == p.E_CONTACTO_CONECTADO:
            logger.info("simplex: contacto conectado contacto=%s", self._id_contacto(resp.get("contact")))
        elif tipo == p.E_CONTACTO_BORRADO:
            logger.info("simplex: el contacto=%s borró el chat", self._id_contacto(resp.get("contact")))
        elif tipo in (p.E_INVITACION_GRUPO, p.E_INVITACION_GRUPO_VIA):
            logger.info("simplex: invitación a un grupo ignorada (el bot no se une a grupos)")
        elif tipo in p.EVENTOS_IGNORADOS:
            logger.debug("simplex: evento %s ignorado", tipo)
        else:
            self.contador.evento_desconocido(tipo)
            logger.debug("simplex: evento desconocido %s ignorado", tipo[:64])

    @staticmethod
    def _id_contacto(contacto: Any) -> Any:
        return contacto.get("contactId") if isinstance(contacto, dict) else None

    def _nuevos_elementos(self, resp: dict[str, Any]) -> None:
        elementos = resp.get("chatItems")
        if not isinstance(elementos, list):
            self.contador.registro_invalido()
            return
        for bruto in elementos:
            try:
                elemento = p.AElementoChat.model_validate(bruto)
            except ValidationError:  # never logged: its text would quote the record
                self.contador.registro_invalido()
                continue
            self._nuevo_elemento(elemento)

    def _nuevo_elemento(self, elemento: p.AElementoChat) -> None:
        info, item = elemento.chat_info, elemento.chat_item
        if info.type != "direct" or info.contact is None:
            if info.type == "group":
                logger.debug("simplex: mensaje de grupo ignorado")
            return
        contenido = item.content.msg_content
        if item.chat_dir.type != "directRcv" or item.content.type != "rcvMsgContent" or contenido is None:
            return
        if item.meta.item_deleted is not None:
            return
        if not self._vistos.agregar(f"{self._usuario_id}:{item.meta.item_id}"):
            logger.debug("simplex: mensaje=%s repetido; ignorado", item.meta.item_id)
            return
        if contenido.type not in _TIPOS_CONTENIDO:
            self.contador.evento_desconocido(f"msgContent:{contenido.type}")
        contacto = str(info.contact.contact_id)
        mensaje_id = str(item.meta.item_id)
        texto_completo = contenido.text or ""
        self._tamanos_cita.agregar(mensaje_id, bytes_json(texto_completo))
        cita = None
        if item.quoted_item is not None and item.quoted_item.content is not None:
            cita = (item.quoted_item.content.text or "")[:MAX_CITA] or None
        recibido = item.meta.item_ts or item.meta.created_at or datetime.now(UTC)
        if recibido.tzinfo is None:
            recibido = recibido.replace(tzinfo=UTC)
        base: dict[str, Any] = {
            "canal": "simplex",
            "contacto": contacto,
            "mensaje_id": mensaje_id,
            "texto": texto_completo[:MAX_TEXTO_ENTRANTE],
            "truncado": len(texto_completo) > MAX_TEXTO_ENTRANTE,
            "reenviado": item.meta.item_forwarded is not None,
            "cita": cita,
            "es_voz": contenido.type == "voice",
            "recibido": recibido,
            "nombre_mostrado": info.contact.nombre[:200] or None,
        }
        recepcion = self._iniciar_recepcion(base, item.file, contenido.type) if item.file is not None else None
        logger.info(
            "simplex: mensaje recibido contacto=%s mensaje=%s tipo=%s archivo=%s",
            contacto,
            mensaje_id,
            contenido.type if contenido.type in _TIPOS_CONTENIDO else "otro",
            "no" if recepcion is None else ("en_espera" if isinstance(recepcion, _ArchivoPendiente) else recepcion),
        )
        if isinstance(recepcion, _ArchivoPendiente):
            self._encolar(contacto, recepcion)
        else:
            # Delivered even without text: the gateway tells the user why the file did not arrive.
            self._encolar(contacto, self._mensaje(base, rechazo=recepcion))

    # --- files ---

    @staticmethod
    def _mensaje(
        base: dict[str, Any], *, adjunto: AdjuntoEntrante | None = None, rechazo: str | None = None
    ) -> MensajeEntrante:
        return MensajeEntrante(
            **base,
            adjuntos=[adjunto] if adjunto is not None else [],
            adjuntos_rechazados=[rechazo] if rechazo else [],
        )

    def _iniciar_recepcion(
        self, base: dict[str, Any], archivo: p.ArchivoCI, tipo_contenido: str
    ) -> _ArchivoPendiente | str:
        """Ask the CLI for the file; the pending download, or the reason code when it is refused or cannot start."""
        ids = f"contacto={base['contacto']} mensaje={base['mensaje_id']} archivo={archivo.file_id}"
        estado = archivo.file_status.type if archivo.file_status is not None else "rcvInvitation"
        if estado != "rcvInvitation":
            logger.info("simplex: archivo no recibido (%s; estado %s)", ids, estado[:40])
            return RECHAZO_DESCARGA
        if len(self._por_fichero) >= MAX_ARCHIVOS_PENDIENTES:
            logger.warning("simplex: archivo no recibido (%s; demasiadas descargas en curso)", ids)
            return RECHAZO_DESCARGA
        try:
            clase = self._recepcion.admitir(tipo_contenido, archivo.file_name, archivo.file_size)
        except ArchivoRechazado as e:
            logger.info("simplex: archivo rechazado (%s; motivo %s; %d bytes)", ids, e.motivo, archivo.file_size)
            return e.codigo
        cliente = self._cliente
        ruta = self._recepcion.ruta_temporal()
        try:
            if cliente is None:
                raise ErrorDesconectado("sin conexión")
            resp = cliente.comando(
                p.cmd_recibir_archivo(archivo.file_id, str(ruta)), timeout=self._cfg.timeout_comando_s
            )
        except ErrorClienteSimplex as e:
            logger.warning("simplex: no se pudo pedir el archivo (%s; %s)", ids, self._codigo(e))
            self._recepcion.descartar(ruta)
            return RECHAZO_DESCARGA
        if resp.get("type") != p.R_ARCHIVO_ACEPTADO:
            logger.info("simplex: archivo no disponible (%s; %s)", ids, str(resp.get("type"))[:64])
            self._recepcion.descartar(ruta)
            return RECHAZO_DESCARGA
        pendiente = _ArchivoPendiente(
            file_id=archivo.file_id,
            contacto=base["contacto"],
            base=base,
            ruta=ruta,
            nombre=archivo.file_name,
            tamano=archivo.file_size,
            clase=clase,
            caduca=time.monotonic() + self._cfg.timeout_archivo_s,
        )
        self._por_fichero[archivo.file_id] = pendiente
        return pendiente

    @staticmethod
    def _id_archivo(resp: dict[str, Any]) -> int | None:
        for clave in ("chatItem", "chatItem_"):
            elemento = resp.get(clave)
            if isinstance(elemento, dict):
                archivo = (elemento.get("chatItem") or {}).get("file")
                if isinstance(archivo, dict) and isinstance(archivo.get("fileId"), int):
                    return int(archivo["fileId"])
        transferencia = resp.get("rcvFileTransfer")
        if isinstance(transferencia, dict) and isinstance(transferencia.get("fileId"), int):
            return int(transferencia["fileId"])
        return None

    def _archivo_completo(self, resp: dict[str, Any]) -> None:
        file_id = self._id_archivo(resp)
        pendiente = self._por_fichero.pop(file_id, None) if file_id is not None else None
        if pendiente is None:
            return
        ids = f"contacto={pendiente.contacto} mensaje={pendiente.base['mensaje_id']} archivo={file_id}"
        try:
            adjunto = self._recepcion.finalizar(pendiente.ruta, pendiente.nombre, pendiente.tamano, pendiente.clase)
            pendiente.listo = self._mensaje(pendiente.base, adjunto=adjunto)
            logger.info("simplex: archivo recibido (%s; %d bytes; %s)", ids, adjunto.tamano, adjunto.mime)
        except ArchivoRechazado as e:
            logger.warning("simplex: archivo descartado tras la descarga (%s; motivo %s)", ids, e.motivo)
            pendiente.listo = self._mensaje(pendiente.base, rechazo=e.codigo)
        self._vaciar(pendiente.contacto)

    def _archivo_fallido(self, resp: dict[str, Any], tipo: str) -> None:
        file_id = self._id_archivo(resp)
        pendiente = self._por_fichero.pop(file_id, None) if file_id is not None else None
        if pendiente is None:
            return
        logger.warning("simplex: la descarga del archivo=%s falló (%s)", file_id, tipo)
        self._recepcion.descartar(pendiente.ruta)
        pendiente.listo = self._mensaje(pendiente.base, rechazo=RECHAZO_DESCARGA)
        self._vaciar(pendiente.contacto)

    def _limpiar_archivos(self) -> None:
        self._proxima_limpieza = time.monotonic() + _LIMPIEZA_CADA_S
        borrados = self._recepcion.limpiar()
        if borrados:
            logger.info("simplex: limpieza de archivos: %d entradas antiguas eliminadas", borrados)

    def _revisar_caducados(self) -> None:
        if not self._por_fichero:
            return
        ahora = time.monotonic()
        for file_id, pendiente in list(self._por_fichero.items()):
            if pendiente.caduca > ahora:
                continue
            del self._por_fichero[file_id]
            logger.warning("simplex: la descarga del archivo=%s tardó demasiado; se cancela", file_id)
            cliente = self._cliente
            if cliente is not None and cliente.conectado:
                try:
                    cliente.comando(p.cmd_cancelar_archivo(file_id), timeout=min(5.0, self._cfg.timeout_comando_s))
                except ErrorClienteSimplex:
                    pass
            self._recepcion.descartar(pendiente.ruta)
            pendiente.listo = self._mensaje(pendiente.base, rechazo=RECHAZO_TIEMPO)
            self._vaciar(pendiente.contacto)

    # --- ordered delivery per contact ---

    def _encolar(self, contacto: str, elemento: MensajeEntrante | _ArchivoPendiente) -> None:
        cola = self._en_espera.get(contacto)
        if cola is None:
            if isinstance(elemento, MensajeEntrante):
                self._entregar(elemento)
                return
            cola = self._en_espera[contacto] = deque()
        if len(cola) >= MAX_EN_ESPERA_POR_CONTACTO and isinstance(elemento, MensajeEntrante):
            logger.warning("simplex: demasiados mensajes en espera del contacto=%s; se entrega sin esperar", contacto)
            self._entregar(elemento)
            return
        cola.append(elemento)
        self._vaciar(contacto)

    def _vaciar(self, contacto: str) -> None:
        cola = self._en_espera.get(contacto)
        while cola:
            cabeza = cola[0]
            if isinstance(cabeza, _ArchivoPendiente):
                if cabeza.listo is None:
                    return
                listo = cabeza.listo
            else:
                listo = cabeza
            cola.popleft()
            self._entregar(listo)
        self._en_espera.pop(contacto, None)

    def _entregar(self, mensaje: MensajeEntrante) -> None:
        ejecutor = self._ejecutor
        if ejecutor is None:
            return
        while not self._parar.is_set():
            if ejecutor.enviar(mensaje.contacto, self._invocar, mensaje, timeout=0.5):
                return
        logger.warning("simplex: mensaje=%s no entregado (canal parado)", mensaje.mensaje_id)

    def _invocar(self, mensaje: MensajeEntrante) -> None:
        manejador = self._manejador
        if manejador is not None:
            manejador(mensaje)  # exceptions are caught and logged (class and place only) by the executor

    # --- sending ---

    def _cliente_listo(self) -> ClienteSimplex | None:
        with self._lock:
            cliente = self._cliente
            if self._preparado and cliente is not None and cliente.conectado:
                return cliente
            return None

    def _enviar(self, mensaje: MensajeSaliente) -> str | None:
        cid = _id_numerico(mensaje.contacto)
        if cid is None:
            logger.warning("simplex: envío rechazado: contacto no válido")
            return None
        cliente = self._cliente_listo()
        if cliente is None:
            logger.warning("simplex: envío al contacto=%s imposible: sin conexión con simplex-chat", cid)
            return None
        partes = dividir_texto(mensaje.texto, self.capacidades.max_texto, PRESUPUESTO_BYTES)
        cita_id = _id_numerico(mensaje.responde_a)
        if cita_id is not None and mensaje.responde_a is not None:
            # The quoted message travels inside the reply: drop the quote when both would not fit in one message.
            tamano_cita = self._tamanos_cita.obtener(mensaje.responde_a.strip())
            if tamano_cita is not None and tamano_cita + bytes_json(partes[0]) > PRESUPUESTO_BYTES:
                cita_id = None
        primero: str | None = None
        for i, parte in enumerate(partes):
            cita = cita_id if i == 0 else None
            item = self._enviar_parte(cliente, cid, parte, cita)
            if item is None and cita is not None:
                item = self._enviar_parte(cliente, cid, parte, None)  # e.g. the quoted item no longer exists
            if item is None:
                logger.warning("simplex: envío al contacto=%s fallido en la parte %d de %d", cid, i + 1, len(partes))
                return None
            primero = primero or item
        logger.info("simplex: mensaje enviado contacto=%s mensaje=%s partes=%d", cid, primero, len(partes))
        return primero

    def _enviar_parte(self, cliente: ClienteSimplex, cid: int, texto: str, cita: int | None) -> str | None:
        try:
            resp = cliente.comando(p.cmd_enviar_texto(cid, texto, cita), timeout=self._cfg.timeout_comando_s)
        except ErrorClienteSimplex as e:
            logger.warning("simplex: /_send al contacto=%s falló (%s)", cid, self._codigo(e))
            return None
        if resp.get("type") != p.R_NUEVOS:
            logger.warning("simplex: /_send: respuesta inesperada (%s)", str(resp.get("type"))[:64])
            return None
        for bruto in resp.get("chatItems") or []:
            try:
                return str(p.AElementoChat.model_validate(bruto).chat_item.meta.item_id)
            except ValidationError:
                self.contador.registro_invalido()
        return None

    def _editar(self, contacto: str, mensaje_id: str, texto: str) -> bool:
        cid, iid = _id_numerico(contacto), _id_numerico(mensaje_id)
        if cid is None or iid is None or not texto.strip():
            logger.warning("simplex: edición rechazada: contacto, mensaje o texto no válidos")
            return False
        cliente = self._cliente_listo()
        if cliente is None:
            logger.warning("simplex: edición del mensaje=%s imposible: sin conexión con simplex-chat", iid)
            return False
        partes = dividir_texto(texto, self.capacidades.max_texto, PRESUPUESTO_BYTES)
        try:
            resp = cliente.comando(p.cmd_editar_texto(cid, iid, partes[0]), timeout=self._cfg.timeout_comando_s)
        except ErrorClienteSimplex as e:
            logger.warning("simplex: edición del mensaje=%s falló (%s)", iid, self._codigo(e))
            return False
        if resp.get("type") not in (p.R_ACTUALIZADO, p.R_SIN_CAMBIOS):
            logger.warning("simplex: edición: respuesta inesperada (%s)", str(resp.get("type"))[:64])
            return False
        for parte in partes[1:]:  # what does not fit in the edited message follows as new messages
            if self._enviar_parte(cliente, cid, parte, None) is None:
                return False
        logger.info("simplex: mensaje=%s editado contacto=%s partes=%d", iid, cid, len(partes))
        return True

    # --- helpers ---

    @staticmethod
    def _codigo(e: ErrorClienteSimplex) -> str:
        return e.codigo if isinstance(e, ErrorComando) else type(e).__name__

    @staticmethod
    def _exigir(resp: dict[str, Any], tipo: str, orden: str) -> None:
        if resp.get("type") != tipo:
            raise ErrorClienteSimplex(f"respuesta inesperada de simplex-chat a {orden} ({str(resp.get('type'))[:64]})")

    def _modelo(self, modelo: type[_M], datos: Any, orden: str) -> _M:
        try:
            return modelo.model_validate(datos)
        except ValidationError:
            self.contador.registro_invalido()
            raise ErrorClienteSimplex(f"respuesta de simplex-chat a {orden} no reconocida") from None

    def _poner(self, *, detalle: str) -> None:
        with self._lock:
            self._detalle = detalle
            self._actualizado = datetime.now(UTC)

    def _marcar_error(self, error: str) -> None:
        with self._lock:
            self._preparado = False
            self._ultimo_error = error
            self._detalle = f"Sin conexión con simplex-chat en {self._cfg.destino}; se reintentará."
            self._actualizado = datetime.now(UTC)

    def _soltar_cliente(self) -> None:
        with self._lock:
            cliente = self._cliente
            self._cliente = None
            self._preparado = False
        if cliente is not None:
            cliente.cerrar()

    def _cerrar_todo(self) -> None:
        sin_entregar = len(self._por_fichero) + sum(
            1 for cola in self._en_espera.values() for e in cola if isinstance(e, MensajeEntrante)
        )
        for pendiente in self._por_fichero.values():
            self._recepcion.descartar(pendiente.ruta)
        self._por_fichero.clear()
        self._en_espera.clear()
        if sin_entregar:
            logger.warning("simplex: %d mensajes quedaron sin entregar al parar", sin_entregar)
        ejecutor = self._ejecutor
        if ejecutor is not None:
            restantes = ejecutor.cerrar(timeout=10)
            if restantes:
                logger.warning("simplex: %d llamadas al manejador seguían en curso al parar", restantes)
        with self._lock:
            self._en_marcha = False
            self._preparado = False
            self._detalle = "Detenido."
            self._actualizado = datetime.now(UTC)
        self._terminado.set()


__all__ = ["MAX_TEXTO_CANAL", "PRESUPUESTO_BYTES", "CanalSimplex", "VersionNoSoportada", "dividir_texto"]

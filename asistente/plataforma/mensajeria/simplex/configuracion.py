"""Configuration of the SimpleX Chat channel, read from ``settings`` (block ``# --- mensajeria.simplex ---``).

Security gate on the WebSocket URL (why): the simplex-chat CLI's WebSocket API has NO authentication and NO
encryption — whoever reaches the port can read every message and act as the bot. The official bot guide says the CLI
binds to localhost only and that a bot on another machine must go through a TLS proxy (with HTTP basic auth on public
networks). So a URL that is not loopback is refused unless ``SIMPLEX_PERMITIR_REMOTO`` is on, and even then only
``wss://`` is accepted. Credentials in the URL (basic auth) are allowed for that case but never shown or logged.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asistente.plataforma.mensajeria.contrato import MAX_ADJUNTO_BYTES

HOSTS_LOCALES = frozenset({"localhost"})


class ConfiguracionSimplexInvalida(Exception):  # noqa: N818 - Spanish name; NOT a ValueError on purpose
    """The channel's settings are unusable (the message is Spanish, for the operator).

    Deliberately not a ``ValueError``: pydantic would wrap it into a ``ValidationError`` (whose text quotes the input,
    credentials in the URL included); this way the clear message reaches the caller unchanged.
    """


def es_host_local(host: str) -> bool:
    """True for loopback hosts only (``localhost``, 127.0.0.0/8, ``::1``)."""
    host = (host or "").strip().lower().strip("[]")
    if host in HOSTS_LOCALES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validar_url(url: str, permitir_remoto: bool) -> None:
    """Raise :class:`ConfiguracionSimplexInvalida` unless ``url`` is an acceptable WebSocket URL (see module doc)."""
    try:
        partes = urlsplit(url)
        host = partes.hostname or ""
        partes.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError as e:
        raise ConfiguracionSimplexInvalida("SIMPLEX_WS_URL no es una URL válida.") from e
    if partes.scheme not in {"ws", "wss"}:
        raise ConfiguracionSimplexInvalida("SIMPLEX_WS_URL debe empezar por ws:// (o wss:// para un servidor remoto).")
    if not host:
        raise ConfiguracionSimplexInvalida("SIMPLEX_WS_URL no indica el servidor.")
    if es_host_local(host):
        return
    if not permitir_remoto:
        raise ConfiguracionSimplexInvalida(
            "SIMPLEX_WS_URL apunta a otra máquina: la API de simplex-chat no tiene autenticación ni cifrado y solo "
            "debe usarse en local (127.0.0.1). Si de verdad necesitas un servidor remoto, ponlo detrás de un proxy "
            "con TLS y autenticación, usa wss:// y activa SIMPLEX_PERMITIR_REMOTO."
        )
    if partes.scheme != "wss":
        raise ConfiguracionSimplexInvalida(
            "Con un servidor remoto solo se admite wss:// (TLS): ws:// enviaría los mensajes en claro por la red."
        )


def url_para_mostrar(url: str) -> str:
    """``host:port`` of the URL, without scheme, credentials or path (for logs and the UI)."""
    try:
        partes = urlsplit(url)
        host = partes.hostname or "?"
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{partes.port}" if partes.port else host
    except ValueError:
        return "?"


class ConfiguracionSimplex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ws_url: str = "ws://127.0.0.1:5225"
    permitir_remoto: bool = False
    bot_nombre: str = Field(default="Asistente del hogar", min_length=1, max_length=100)
    bienvenida: str = Field(default="", max_length=2000)
    autoaceptar: bool = True
    archivos_dir: Path = Path("data/mensajeria/simplex/archivos")
    # Where the CLI writes a file being received (a path WE choose per file); None: <archivos_dir>/recepcion.
    recepcion_dir: Path | None = None
    archivos_max_bytes: int = Field(default=MAX_ADJUNTO_BYTES, ge=1)
    aceptar_voz: bool = True
    timeout_archivo_s: float = Field(default=300.0, gt=0)
    # Days the CLI keeps the conversation (its own copy, in its database); 0: leave the CLI's setting as it is.
    historial_dias: int = Field(default=7, ge=0, le=3650)
    servidores_smp: tuple[str, ...] = ()
    servidores_xftp: tuple[str, ...] = ()
    timeout_conexion_s: float = Field(default=10.0, gt=0)
    timeout_comando_s: float = Field(default=30.0, gt=0)
    # Commands that wait for the network (creating the address, changing its settings) get at least this long.
    timeout_comando_red_s: float = Field(default=60.0, gt=0)
    ping_s: float = Field(default=20.0, ge=0)
    reconexion_base_s: float = Field(default=1.0, gt=0)
    reconexion_max_s: float = Field(default=60.0, gt=0)
    manejadores_max: int = Field(default=4, ge=1, le=64)
    pendientes_max: int = Field(default=200, ge=1)
    cola_eventos_max: int = Field(default=1000, ge=10)

    @model_validator(mode="after")
    def _validar(self) -> ConfiguracionSimplex:
        validar_url(self.ws_url, self.permitir_remoto)
        if any(c in self.bot_nombre for c in "\r\n\t") or self.bot_nombre != self.bot_nombre.strip():
            raise ConfiguracionSimplexInvalida(
                "SIMPLEX_BOT_NOMBRE no puede tener saltos de línea ni espacios al borde."
            )
        for ruta in (self.archivos_dir, self.recepcion):
            if any(c in str(ruta) for c in "\r\n"):
                raise ConfiguracionSimplexInvalida(
                    "Las carpetas de archivos de SimpleX no pueden tener saltos de línea."
                )
        return self

    @property
    def recepcion(self) -> Path:
        return self.recepcion_dir if self.recepcion_dir is not None else self.archivos_dir / "recepcion"

    @property
    def max_bytes_archivo(self) -> int:
        """The file size limit actually applied: the setting, never above the gateway's contract limit."""
        return min(self.archivos_max_bytes, MAX_ADJUNTO_BYTES)

    @property
    def destino(self) -> str:
        return url_para_mostrar(self.ws_url)

    @classmethod
    def desde_settings(cls) -> ConfiguracionSimplex:
        """Build it from ``asistente.plataforma.settings`` (read now, so reloaded settings are honoured)."""
        from asistente.plataforma import settings

        recepcion = str(getattr(settings, "SIMPLEX_ARCHIVOS_RECEPCION_DIR", "") or "").strip()
        try:
            return cls(
                ws_url=str(getattr(settings, "SIMPLEX_WS_URL", "ws://127.0.0.1:5225")).strip(),
                permitir_remoto=bool(getattr(settings, "SIMPLEX_PERMITIR_REMOTO", False)),
                bot_nombre=str(getattr(settings, "SIMPLEX_BOT_NOMBRE", "Asistente del hogar")),
                bienvenida=str(getattr(settings, "SIMPLEX_BIENVENIDA", "") or "").strip(),
                autoaceptar=bool(getattr(settings, "SIMPLEX_AUTOACEPTAR", True)),
                archivos_dir=Path(str(getattr(settings, "SIMPLEX_ARCHIVOS_DIR", "data/mensajeria/simplex/archivos"))),
                recepcion_dir=Path(recepcion) if recepcion else None,
                archivos_max_bytes=int(getattr(settings, "SIMPLEX_ARCHIVOS_MAX_BYTES", MAX_ADJUNTO_BYTES)),
                aceptar_voz=bool(getattr(settings, "SIMPLEX_ACEPTAR_VOZ", True)),
                timeout_archivo_s=float(getattr(settings, "SIMPLEX_ARCHIVOS_TIMEOUT_S", 300.0)),
                historial_dias=int(getattr(settings, "SIMPLEX_HISTORIAL_DIAS", 7)),
                servidores_smp=tuple(str(getattr(settings, "SIMPLEX_SERVIDORES_SMP", "") or "").split()),
                servidores_xftp=tuple(str(getattr(settings, "SIMPLEX_SERVIDORES_XFTP", "") or "").split()),
                timeout_conexion_s=float(getattr(settings, "SIMPLEX_TIMEOUT_CONEXION_S", 10.0)),
                timeout_comando_s=float(getattr(settings, "SIMPLEX_TIMEOUT_COMANDO_S", 30.0)),
                ping_s=float(getattr(settings, "SIMPLEX_PING_S", 20.0)),
                reconexion_max_s=float(getattr(settings, "SIMPLEX_RECONEXION_MAX_S", 60.0)),
            )
        except ConfiguracionSimplexInvalida:
            raise
        except ValueError as e:  # pydantic's ValidationError is a ValueError; its text may quote the values
            campos = ", ".join(str(err.get("loc", ("?",))[0]) for err in getattr(e, "errors", lambda: [])())
            raise ConfiguracionSimplexInvalida(
                f"Configuración de SimpleX no válida ({campos or 'valores'}): revisa el bloque SIMPLEX_* del entorno."
            ) from None


__all__ = [
    "ConfiguracionSimplex",
    "ConfiguracionSimplexInvalida",
    "es_host_local",
    "url_para_mostrar",
    "validar_url",
]

"""Receiving the files a contact sends, safely.

Everything about an incoming file is untrusted: its name, its declared size, its declared kind and its bytes. The
gates, in order (why each one):

1. **Before downloading** (:meth:`RecepcionArchivos.admitir`): the declared size must be within the limit and the
   declared kind must be an image, a PDF or (if enabled) a voice note — anything else is never downloaded at all.
2. **Where it lands**: the CLI is told to write to a path WE choose (``<recepcion>/<random>.part``), never one derived
   from the sender's file name, so a name like ``../../.ssh/authorized_keys`` cannot place anything anywhere.
3. **After downloading** (:meth:`RecepcionArchivos.finalizar`): the staged file is opened with ``O_NOFOLLOW`` and must
   be a regular file with one link (no symlink or hard-link games inside the shared staging folder), its real size
   must be within the limit and equal to the declared one, and its first bytes must match an allowed type (magic
   numbers, not the extension: a renamed executable is refused). The MIME type given to the gateway comes from
   these bytes, never from the sender.
4. **Where it is kept**: copied (not moved, so the copy belongs to this process with mode 0600) into
   ``<archivos>/<random>/<sanitised name>``, folders 0700. The sanitised name keeps only safe characters, has no
   path separators and gets the extension of the DETECTED type. The staged file is removed.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import time
import unicodedata

from asistente.plataforma.mensajeria.contrato import AdjuntoEntrante

CLASE_IMAGEN = "imagen"
CLASE_PDF = "pdf"
CLASE_VOZ = "voz"

EXTENSIONES_IMAGEN = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"})
_BLOQUE = 64 * 1024


# Reason codes the gateway receives in ``MensajeEntrante.adjuntos_rechazados`` (contract): too big, a kind of file
# that is not accepted, a download that failed (or could not even start, or did not pass the checks after it), and a
# download that took longer than SIMPLEX_ARCHIVOS_TIMEOUT_S.
RECHAZO_GRANDE = "demasiado_grande"
RECHAZO_TIPO = "tipo_no_permitido"
RECHAZO_DESCARGA = "error_descarga"
RECHAZO_TIEMPO = "tiempo_agotado"


def codigo_rechazo(motivo: str) -> str:
    """The contract's reason code for one of this module's internal ``motivo`` codes."""
    if motivo == "demasiado_grande":
        return RECHAZO_GRANDE
    if motivo == "tipo_no_permitido":
        return RECHAZO_TIPO
    return RECHAZO_DESCARGA


class ArchivoRechazado(Exception):  # noqa: N818 - Spanish names across the project
    """The file is not accepted. ``motivo`` is a short content-free code (for logs and tests)."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo

    @property
    def codigo(self) -> str:
        """The contract's reason code (``adjuntos_rechazados``)."""
        return codigo_rechazo(self.motivo)


@dataclass(frozen=True)
class TipoDetectado:
    mime: str
    extension: str
    clase: str


_MARCAS_ISO_IMAGEN = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heif", b"avif"}
_MARCAS_ISO_AUDIO = {b"M4A ", b"M4B ", b"mp42", b"mp41", b"isom", b"iso2", b"3gp4", b"3gp5", b"dash"}


def detectar_tipo(cabecera: bytes) -> TipoDetectado | None:
    """The type of a file from its first bytes (at least 16), among the allowed ones; None otherwise."""
    if cabecera.startswith(b"\xff\xd8\xff"):
        return TipoDetectado("image/jpeg", ".jpg", CLASE_IMAGEN)
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return TipoDetectado("image/png", ".png", CLASE_IMAGEN)
    if cabecera[:6] in (b"GIF87a", b"GIF89a"):
        return TipoDetectado("image/gif", ".gif", CLASE_IMAGEN)
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return TipoDetectado("image/webp", ".webp", CLASE_IMAGEN)
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WAVE":
        return TipoDetectado("audio/wav", ".wav", CLASE_VOZ)
    if cabecera.startswith(b"%PDF-"):
        return TipoDetectado("application/pdf", ".pdf", CLASE_PDF)
    if cabecera.startswith(b"OggS"):
        return TipoDetectado("audio/ogg", ".ogg", CLASE_VOZ)
    if cabecera[4:8] == b"ftyp":
        marca = cabecera[8:12]
        if marca in _MARCAS_ISO_IMAGEN:
            return TipoDetectado("image/heic", ".heic", CLASE_IMAGEN)
        if marca in _MARCAS_ISO_AUDIO:
            return TipoDetectado("audio/mp4", ".m4a", CLASE_VOZ)
    return None


def clase_declarada(tipo_contenido: str, nombre: str, aceptar_voz: bool) -> str | None:
    """The kind the sender declares (message content type + extension), if it is one we accept."""
    extension = Path(_solo_nombre(nombre)).suffix.lower()
    if tipo_contenido == "image":
        return CLASE_IMAGEN
    if tipo_contenido == "voice":
        return CLASE_VOZ if aceptar_voz else None
    if tipo_contenido in {"file", "text"}:
        if extension == ".pdf":
            return CLASE_PDF
        if extension in EXTENSIONES_IMAGEN:
            return CLASE_IMAGEN
    return None


def _solo_nombre(nombre: str) -> str:
    # The last component whatever separator the sender used (POSIX or Windows).
    return re.split(r"[\\/]", nombre or "")[-1]


_NO_PERMITIDO = re.compile(r"[^\w.\- ]+", re.UNICODE)


def nombre_saneado(nombre: str, extension: str) -> str:
    """A safe file name derived from the sender's: one component, safe characters only, the detected extension."""
    base = unicodedata.normalize("NFKC", _solo_nombre(nombre))
    base = "".join(c for c in base if unicodedata.category(c)[0] != "C")  # no control/format characters
    base = Path(base).stem if Path(base).suffix else base
    base = _NO_PERMITIDO.sub("_", base).strip(" ._-")
    base = re.sub(r"_+", "_", base)[:80].strip(" ._-")
    return f"{base or 'adjunto'}{extension}"


class RecepcionArchivos:
    """Staging and final folders for incoming files, and the checks of the module docstring."""

    def __init__(self, archivos_dir: Path, recepcion_dir: Path, max_bytes: int, aceptar_voz: bool) -> None:
        self.archivos_dir = Path(archivos_dir)
        self.recepcion_dir = Path(recepcion_dir)
        self.max_bytes = max_bytes
        self.aceptar_voz = aceptar_voz
        self.error: str | None = "Recepción de archivos sin preparar."

    def preparar(self) -> str | None:
        """Create the folders (0700 for ours). Returns None when ready, or a Spanish reason (files then refused)."""
        try:
            self._carpeta_privada(self.archivos_dir)
            if self._dentro(self.recepcion_dir, self.archivos_dir):
                self._carpeta_privada(self.recepcion_dir)
            else:
                # A folder shared with the CLI's system user: created if missing, its permissions are the operator's
                # (see docs/mensajeria/simplex.md); it must be a real directory, not a symlink.
                self.recepcion_dir.mkdir(parents=True, exist_ok=True)
                if self.recepcion_dir.is_symlink() or not self.recepcion_dir.is_dir():
                    raise OSError(errno.ENOTDIR, "no es una carpeta")
            self.error = None
        except OSError as e:
            self.error = f"No se puede preparar la carpeta de archivos de SimpleX ({e.strerror or type(e).__name__})."
        return self.error

    @staticmethod
    def _dentro(ruta: Path, padre: Path) -> bool:
        try:
            ruta.resolve().relative_to(padre.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _carpeta_privada(ruta: Path) -> None:
        ruta.mkdir(mode=0o700, parents=True, exist_ok=True)
        if ruta.is_symlink() or not ruta.is_dir():
            raise OSError(errno.ENOTDIR, "no es una carpeta")
        if stat.S_IMODE(ruta.stat().st_mode) != 0o700:
            ruta.chmod(0o700)

    @property
    def disponible(self) -> bool:
        return self.error is None

    def admitir(self, tipo_contenido: str, nombre: str, tamano_declarado: int) -> str:
        """Gate 1. Returns the declared kind; raises :class:`ArchivoRechazado` when it must not be downloaded."""
        if not self.disponible:
            raise ArchivoRechazado("recepcion_no_disponible")
        if tamano_declarado <= 0:
            raise ArchivoRechazado("tamano_desconocido")
        if tamano_declarado > self.max_bytes:
            raise ArchivoRechazado("demasiado_grande")
        clase = clase_declarada(tipo_contenido, nombre, self.aceptar_voz)
        if clase is None:
            raise ArchivoRechazado("tipo_no_permitido")
        return clase

    def ruta_temporal(self) -> Path:
        """Gate 2: a fresh path for the CLI to create (random; the sender's name plays no part)."""
        return self.recepcion_dir.resolve() / f"{secrets.token_hex(16)}.part"

    def finalizar(self, ruta_temporal: Path, nombre: str, tamano_declarado: int, clase: str) -> AdjuntoEntrante:
        """Gates 3 and 4. Returns the attachment for the gateway; raises :class:`ArchivoRechazado` (and cleans up)."""
        destino_dir: Path | None = None
        try:
            if ruta_temporal.parent != self.recepcion_dir.resolve():
                raise ArchivoRechazado("ruta_inesperada")
            try:
                fd = os.open(ruta_temporal, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
            except OSError as e:
                raise ArchivoRechazado("enlace" if e.errno == errno.ELOOP else "no_legible") from None
            with os.fdopen(fd, "rb") as origen:
                info = os.fstat(origen.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ArchivoRechazado("no_es_fichero")
                if info.st_size > self.max_bytes:
                    raise ArchivoRechazado("demasiado_grande")
                if info.st_size != tamano_declarado:
                    raise ArchivoRechazado("tamano_distinto")
                cabecera = origen.read(64)
                tipo = detectar_tipo(cabecera)
                if tipo is None or not self._compatible(clase, tipo.clase):
                    raise ArchivoRechazado("tipo_no_permitido")
                destino_dir = self.archivos_dir.resolve() / secrets.token_hex(16)
                destino_dir.mkdir(mode=0o700)
                destino = destino_dir / nombre_saneado(nombre, tipo.extension)
                copiados = self._copiar(origen, cabecera, destino)
            if copiados != tamano_declarado:
                raise ArchivoRechazado("tamano_distinto")
            self.descartar(ruta_temporal)
            return AdjuntoEntrante(
                nombre=(_solo_nombre(nombre) or "adjunto")[:255],
                mime=tipo.mime,
                ruta_local=str(destino),
                tamano=copiados,
            )
        except ArchivoRechazado:
            self._limpiar(ruta_temporal, destino_dir)
            raise
        except OSError:
            self._limpiar(ruta_temporal, destino_dir)
            raise ArchivoRechazado("error_de_disco") from None

    @staticmethod
    def _compatible(declarada: str, detectada: str) -> bool:
        # A «file» message may carry an image; otherwise the declared kind must be the detected one.
        return declarada == detectada or (declarada == CLASE_PDF and detectada == CLASE_IMAGEN)

    def _copiar(self, origen, cabecera: bytes, destino: Path) -> int:  # type: ignore[no-untyped-def]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(destino, flags, 0o600)
        total = 0
        with os.fdopen(fd, "wb") as salida:
            bloque = cabecera
            while bloque:
                total += len(bloque)
                if total > self.max_bytes:  # the staged file grew while being copied
                    raise ArchivoRechazado("demasiado_grande")
                salida.write(bloque)
                bloque = origen.read(_BLOQUE)
        return total

    _TEMPORAL = re.compile(r"[0-9a-f]{32}\.part")
    _CARPETA = re.compile(r"[0-9a-f]{32}")

    def limpiar(self, antiguedad_s: float = 24 * 3600) -> int:
        """Housekeeping: remove staged files left behind (a crash, a cancelled download) and the empty per-file
        folders the gateway leaves when it deletes an attachment, both older than ``antiguedad_s``. Only names this
        module creates are touched. Returns how many entries were removed."""
        if not self.disponible:
            return 0
        limite = time.time() - antiguedad_s
        borrados = 0
        try:
            recepcion = self.recepcion_dir.resolve()
            for entrada in recepcion.iterdir():
                if self._TEMPORAL.fullmatch(entrada.name) and entrada.lstat().st_mtime < limite:
                    entrada.unlink(missing_ok=True)
                    borrados += 1
            for entrada in self.archivos_dir.resolve().iterdir():
                if (
                    self._CARPETA.fullmatch(entrada.name)
                    and entrada.is_dir()
                    and not entrada.is_symlink()
                    and entrada.lstat().st_mtime < limite
                    and not any(entrada.iterdir())
                ):
                    entrada.rmdir()
                    borrados += 1
        except OSError:
            pass
        return borrados

    def descartar(self, ruta_temporal: Path) -> None:
        """Remove a staged file (best effort; only inside the staging folder)."""
        try:
            if ruta_temporal.parent == self.recepcion_dir.resolve():
                ruta_temporal.unlink(missing_ok=True)
        except OSError:
            pass

    def _limpiar(self, ruta_temporal: Path, destino_dir: Path | None) -> None:
        self.descartar(ruta_temporal)
        if destino_dir is not None:
            shutil.rmtree(destino_dir, ignore_errors=True)


__all__ = [
    "RECHAZO_DESCARGA",
    "RECHAZO_GRANDE",
    "RECHAZO_TIEMPO",
    "RECHAZO_TIPO",
    "ArchivoRechazado",
    "RecepcionArchivos",
    "TipoDetectado",
    "clase_declarada",
    "codigo_rechazo",
    "detectar_tipo",
    "nombre_saneado",
]

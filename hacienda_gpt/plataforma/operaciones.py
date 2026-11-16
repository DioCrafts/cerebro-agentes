"""Operator tasks on the databases: online backups (optionally encrypted) and revoking every session.

Backups use SQLite's online backup API: a consistent copy taken while the API and the worker keep writing
(WAL), checked with ``PRAGMA integrity_check`` before it is kept. With a key (``PLATAFORMA_CLAVE_COPIAS``: one
or more comma-separated Fernet keys, the first encrypts) the copy is written encrypted (AES-128-CBC + HMAC,
``cryptography``'s Fernet) and the plain copy never reaches the destination directory. The cases' database
holds personal data: an unencrypted copy must live on an encrypted disk (see docs/despliegue.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import tempfile

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

SUFIJO_CIFRADO = ".fernet"


class CopiaError(RuntimeError):
    """A backup that could not be made or opened. Spanish message, for the operator."""


def _fernet(clave: str) -> MultiFernet:
    claves = [c.strip() for c in clave.split(",") if c.strip()]
    if not claves:
        raise CopiaError("Falta la clave de cifrado de las copias (PLATAFORMA_CLAVE_COPIAS).")
    try:
        return MultiFernet([Fernet(c.encode("ascii")) for c in claves])
    except (ValueError, UnicodeError) as exc:
        raise CopiaError("La clave de cifrado de las copias no es una clave Fernet válida.") from exc


def nueva_clave() -> str:
    return Fernet.generate_key().decode("ascii")


def _copia_consistente(origen: str, destino: Path) -> None:
    if not Path(origen).exists():
        raise CopiaError(f"No existe la base de datos {origen}.")
    # A normal connection (a read-only one cannot open a WAL database whose -shm file is missing); it only reads.
    fuente = sqlite3.connect(origen, timeout=30.0)
    copia = sqlite3.connect(str(destino))
    try:
        fuente.backup(copia)
        resultado = copia.execute("PRAGMA integrity_check").fetchone()
        if resultado is None or resultado[0] != "ok":
            raise CopiaError(f"La copia no supera la comprobación de integridad: {resultado}")
    finally:
        copia.close()
        fuente.close()


def copiar(origen: str, directorio: str, *, clave: str | None = None, ahora: datetime | None = None) -> Path:
    """Write a consistent copy of ``origen`` into ``directorio`` (encrypted when ``clave``). Returns its path."""
    momento = (ahora or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    carpeta = Path(directorio)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"{Path(origen).stem}-{momento}.sqlite3"
    fernet = _fernet(clave) if clave else None
    if fernet is None:
        final = carpeta / nombre
        temporal = carpeta / f".{nombre}.parcial"
        _copia_consistente(origen, temporal)
        os.chmod(temporal, 0o600)
        temporal.replace(final)
        return final
    final = carpeta / (nombre + SUFIJO_CIFRADO)
    # The plain copy is made in a private temporary directory, never next to the encrypted ones.
    with tempfile.TemporaryDirectory(prefix="copia-") as privado:
        plano = Path(privado) / nombre
        _copia_consistente(origen, plano)
        datos = fernet.encrypt(plano.read_bytes())
    temporal = carpeta / f".{final.name}.parcial"
    temporal.write_bytes(datos)
    os.chmod(temporal, 0o600)
    temporal.replace(final)
    return final


def descifrar(fichero: str, destino: str, *, clave: str) -> Path:
    """Decrypt a backup made by :func:`copiar` into ``destino`` (a .sqlite3 file). Any of the keys opens it."""
    try:
        datos = _fernet(clave).decrypt(Path(fichero).read_bytes())
    except InvalidToken as exc:
        raise CopiaError("Ninguna de las claves abre esta copia (o el fichero está dañado).") from exc
    salida = Path(destino)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes(datos)
    os.chmod(salida, 0o600)
    return salida


def recifrar(fichero: str, *, clave: str) -> None:
    """Re-encrypt a backup with the FIRST key of ``clave`` (key rotation: new key first, old ones after)."""
    ruta = Path(fichero)
    try:
        datos = _fernet(clave).rotate(ruta.read_bytes())
    except InvalidToken as exc:
        raise CopiaError("Ninguna de las claves abre esta copia (o el fichero está dañado).") from exc
    temporal = ruta.with_name(f".{ruta.name}.parcial")
    temporal.write_bytes(datos)
    os.chmod(temporal, 0o600)
    temporal.replace(ruta)


def revocar_sesiones(db_path: str, *, ahora: datetime | None = None) -> int:
    """Revoke every open session (everyone logs in again). Returns how many were open."""
    momento = (ahora or datetime.now(UTC)).isoformat()
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'auth_sessions'").fetchone()
        if existe is None:
            return 0
        with conn:
            cursor = conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL", (momento,))
        return cursor.rowcount
    finally:
        conn.close()


__all__ = ["SUFIJO_CIFRADO", "CopiaError", "copiar", "descifrar", "nueva_clave", "recifrar", "revocar_sesiones"]

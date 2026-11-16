"""Encryption at rest of the Gmail refresh token (Fernet, key from ``CORREO_CLAVE_CIFRADO``).

The plaintext is a small JSON document that also names the case and the user the token belongs to: a
ciphertext copied to another row (another case, another account) fails to open. ``CORREO_CLAVE_CIFRADO``
may hold several comma-separated keys (MultiFernet): the first encrypts, all decrypt, so the operator can
rotate keys (``python -m hacienda_gpt.capacidades.correo rotar-clave``). Tokens are never logged.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from hacienda_gpt.capacidades.correo.config import configuracion


class CifradoNoDisponibleError(RuntimeError):
    """No usable key configured. The message is Spanish, for the user/operator."""


def _fernet(clave: str | None = None) -> MultiFernet:
    texto = clave if clave is not None else configuracion().clave_cifrado
    claves = [c.strip() for c in texto.split(",") if c.strip()]
    if not claves:
        raise CifradoNoDisponibleError(
            "El servicio no tiene configurada la clave de cifrado del correo (CORREO_CLAVE_CIFRADO)."
        )
    try:
        return MultiFernet([Fernet(c.encode("ascii")) for c in claves])
    except (ValueError, UnicodeError) as exc:
        raise CifradoNoDisponibleError("La clave de cifrado del correo (CORREO_CLAVE_CIFRADO) no es válida.") from exc


def disponible() -> bool:
    try:
        _fernet()
    except CifradoNoDisponibleError:
        return False
    return True


def cifrar(secreto: str, *, case_id: str, user_id: str | None, proposito: str = "refresh_token") -> str:
    documento = json.dumps({"p": proposito, "c": case_id, "u": user_id, "s": secreto}, separators=(",", ":"))
    return _fernet().encrypt(documento.encode("utf-8")).decode("ascii")


def descifrar(cifrado: str, *, case_id: str, user_id: str | None, proposito: str = "refresh_token") -> str:
    """The secret, or ValueError when the key is wrong or the ciphertext belongs to another case/user."""
    try:
        datos: Any = json.loads(_fernet().decrypt(cifrado.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ValueError("no se pudo descifrar el token") from exc
    if (
        not isinstance(datos, dict)
        or datos.get("p") != proposito
        or datos.get("c") != case_id
        or datos.get("u") != user_id
    ):
        raise ValueError("el token cifrado no pertenece a este expediente")
    secreto = datos.get("s")
    if not isinstance(secreto, str) or not secreto:
        raise ValueError("token vacío")
    return secreto


def recifrar(cifrado: str) -> str:
    """Re-encrypt with the first key (rotation)."""
    return _fernet().rotate(cifrado.encode("ascii")).decode("ascii")


def nueva_clave() -> str:
    return Fernet.generate_key().decode("ascii")


__all__ = ["CifradoNoDisponibleError", "cifrar", "descifrar", "disponible", "nueva_clave", "recifrar"]

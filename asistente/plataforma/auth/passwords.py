"""Password hashing with the standard library's scrypt.

A hash is stored as ``scrypt$<n>$<r>$<p>$<salt b64>$<key b64>`` so the work
factor can be raised later without invalidating existing passwords.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading

from hacienda_gpt import settings

PASSWORD_MIN_LENGTH = 10
# Upper bound so a huge body cannot be used to burn CPU in the KDF.
PASSWORD_MAX_LENGTH = 1024

_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32
# Never accept parameters from a stored hash beyond this (128 * n * r bytes of memory).
_MAX_N = 2**20
# Each derivation holds 128 * n * r bytes (128 MB at the default n = 2**17) and logins and registrations are
# unauthenticated: without a cap, the 40 worker threads of the server could hold 5 GB at once. Requests over
# the cap wait their turn instead (docs/seguridad.md).
_KDF_CONCURRENCY = 4
_kdf_slots = threading.BoundedSemaphore(_KDF_CONCURRENCY)


class PasswordPolicyError(ValueError):
    """The password does not meet the policy. The message is in Spanish, for the user."""


def check_password_policy(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(f"La contraseña no puede superar los {PASSWORD_MAX_LENGTH} caracteres.")


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    with _kdf_slots:
        return _scrypt(password, salt, n=n, r=r, p=p)


def _scrypt(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_KEY_BYTES,
        # OpenSSL refuses by default anything above 32 MB.
        maxmem=128 * n * r * p + 1024 * 1024,
    )


def hash_password(password: str) -> str:
    """Hash ``password`` with a fresh random salt."""
    n = settings.AUTH_SCRYPT_N
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, n=n, r=_SCRYPT_R, p=_SCRYPT_P)
    return "$".join(
        [
            "scrypt",
            str(n),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored hash; False on any malformed hash."""
    if len(password) > PASSWORD_MAX_LENGTH:
        return False
    try:
        scheme, n_text, r_text, p_text, salt_text, key_text = stored.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(key_text, validate=True)
    except ValueError:
        return False
    if scheme != "scrypt" or not 2 <= n <= _MAX_N or r != _SCRYPT_R or p != _SCRYPT_P:
        return False
    try:
        actual = _derive(password, salt, n=n, r=r, p=p)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def burn_password_check(password: str) -> None:
    """Spend the same time as a real verification, for logins against unknown e-mails."""
    _derive(password[:PASSWORD_MAX_LENGTH], b"\x00" * _SALT_BYTES, n=settings.AUTH_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)

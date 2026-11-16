"""Web Push message encryption: RFC 8291 over the ``aes128gcm`` content coding of RFC 8188.

Only what a push sender needs, written with ``cryptography`` (already a dependency):

* :func:`cifrar` — encrypt one message for one subscription (the browser's ``p256dh`` key and ``auth``
  secret). A fresh ephemeral key pair and a random salt per message, a single record (the payloads this app
  sends are a few hundred bytes; push services refuse more than 4096 anyway).
* :func:`descifrar` — the receiving side, used by the tests to check the sender against the RFC example and
  by the self-check; the application server never needs it in production.

Key derivation (RFC 8291 §3.3–3.4, RFC 8188 §2.2–2.3)::

    ecdh_secret = ECDH(as_private, ua_public)
    PRK_key     = HMAC-SHA-256(auth_secret, ecdh_secret)
    IKM         = HMAC-SHA-256(PRK_key, "WebPush: info" || 0x00 || ua_public || as_public || 0x01)
    PRK         = HMAC-SHA-256(salt, IKM)
    CEK         = HMAC-SHA-256(PRK, "Content-Encoding: aes128gcm" || 0x00 || 0x01)[0:16]
    NONCE       = HMAC-SHA-256(PRK, "Content-Encoding: nonce" || 0x00 || 0x01)[0:12]
    body        = salt || rs (uint32) || idlen (= 65) || as_public || AES-128-GCM(CEK, NONCE, plaintext || 0x02)

The test vector of RFC 8291 §5 is in ``tests/push/test_cifrado.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# RFC 8188 §2: the record size this sender announces (one record is always enough for our payloads).
TAMANO_REGISTRO = 4096
_TAG = 16
_LONGITUD_SAL = 16
_LONGITUD_AUTH = 16
_LONGITUD_PUBLICA = 65  # uncompressed P-256 point: 0x04 || X || Y
_CABECERA = _LONGITUD_SAL + 4 + 1 + _LONGITUD_PUBLICA


class CifradoError(ValueError):
    """A key, secret or message that cannot be used (the message is for the logs, not the user)."""


def b64url_decode(texto: str) -> bytes:
    """Base64url without padding (as browsers and RFC 8291 write keys), padding tolerated."""
    limpio = texto.strip()
    try:
        return base64.urlsafe_b64decode(limpio + "=" * (-len(limpio) % 4))
    except (ValueError, TypeError) as exc:
        raise CifradoError("base64url no válido") from exc


def b64url_encode(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def publica_cruda(clave: ec.EllipticCurvePublicKey) -> bytes:
    """The 65-byte uncompressed encoding of a P-256 public key."""
    return clave.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def cargar_publica(cruda: bytes) -> ec.EllipticCurvePublicKey:
    """A P-256 public key from its uncompressed encoding; rejects anything that is not a point on the curve."""
    if len(cruda) != _LONGITUD_PUBLICA or cruda[0] != 0x04:
        raise CifradoError("la clave pública debe ser un punto P-256 sin comprimir de 65 bytes")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), cruda)
    except ValueError as exc:
        raise CifradoError("la clave pública no es un punto de la curva P-256") from exc


def cargar_privada(cruda: bytes) -> ec.EllipticCurvePrivateKey:
    """A P-256 private key from its 32-byte scalar."""
    if len(cruda) != 32:
        raise CifradoError("la clave privada P-256 debe tener 32 bytes")
    try:
        return ec.derive_private_key(int.from_bytes(cruda, "big"), ec.SECP256R1())
    except ValueError as exc:
        raise CifradoError("la clave privada no es válida para P-256") from exc


def _hmac(clave: bytes, datos: bytes) -> bytes:
    return hmac.new(clave, datos, hashlib.sha256).digest()


def _claves_de_contenido(
    ecdh: bytes, auth: bytes, ua_publica: bytes, as_publica: bytes, sal: bytes
) -> tuple[bytes, bytes]:
    """(CEK, NONCE) for one message — RFC 8291 §3.4 then RFC 8188 §2.2/§2.3 (single-block HKDF expands)."""
    prk_clave = _hmac(auth, ecdh)
    ikm = _hmac(prk_clave, b"WebPush: info\x00" + ua_publica + as_publica + b"\x01")
    prk = _hmac(sal, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    return cek, nonce


def cifrar(
    p256dh: bytes,
    auth: bytes,
    mensaje: bytes,
    *,
    clave_efimera: ec.EllipticCurvePrivateKey | None = None,
    sal: bytes | None = None,
    tamano_registro: int = TAMANO_REGISTRO,
) -> bytes:
    """Encrypt ``mensaje`` for the subscription with keys ``p256dh`` (65 bytes) and ``auth`` (16 bytes).

    ``clave_efimera`` and ``sal`` exist only so the RFC example can be reproduced; in production both are
    fresh for every message (never reuse them: the content key would repeat).
    """
    if len(auth) != _LONGITUD_AUTH:
        raise CifradoError("el secreto de autenticación debe tener 16 bytes")
    ua_publica = cargar_publica(p256dh)
    if len(mensaje) + 1 + _TAG > tamano_registro:
        raise CifradoError("el mensaje no cabe en un registro")
    if tamano_registro > 0xFFFFFFFF or tamano_registro < 18:
        raise CifradoError("tamaño de registro no válido")
    privada = clave_efimera if clave_efimera is not None else ec.generate_private_key(ec.SECP256R1())
    as_publica = publica_cruda(privada.public_key())
    sal = sal if sal is not None else os.urandom(_LONGITUD_SAL)
    if len(sal) != _LONGITUD_SAL:
        raise CifradoError("la sal debe tener 16 bytes")
    ecdh = privada.exchange(ec.ECDH(), ua_publica)
    cek, nonce = _claves_de_contenido(ecdh, auth, p256dh, as_publica, sal)
    # The only (so the last) record: the plaintext followed by the 0x02 delimiter, no padding.
    cifrado = AESGCM(cek).encrypt(nonce, mensaje + b"\x02", None)
    cabecera = sal + struct.pack("!I", tamano_registro) + bytes([len(as_publica)]) + as_publica
    return cabecera + cifrado


def descifrar(ua_privada: ec.EllipticCurvePrivateKey, auth: bytes, cuerpo: bytes) -> bytes:
    """Decrypt a single-record ``aes128gcm`` Web Push message (the browser's side; for tests)."""
    if len(cuerpo) < _CABECERA + _TAG + 1:
        raise CifradoError("mensaje demasiado corto")
    sal = cuerpo[:_LONGITUD_SAL]
    (tamano_registro,) = struct.unpack("!I", cuerpo[_LONGITUD_SAL : _LONGITUD_SAL + 4])
    longitud_id = cuerpo[_LONGITUD_SAL + 4]
    if longitud_id != _LONGITUD_PUBLICA:
        raise CifradoError("el identificador de clave debe ser la clave pública del servidor (65 bytes)")
    as_publica = cuerpo[_LONGITUD_SAL + 5 : _CABECERA]
    cifrado = cuerpo[_CABECERA:]
    if len(cifrado) > tamano_registro:
        raise CifradoError("solo se admite un registro")
    ua_publica = publica_cruda(ua_privada.public_key())
    ecdh = ua_privada.exchange(ec.ECDH(), cargar_publica(as_publica))
    cek, nonce = _claves_de_contenido(ecdh, auth, ua_publica, as_publica, sal)
    claro = AESGCM(cek).decrypt(nonce, cifrado, None)
    # Strip the padding (zeros) and check the last-record delimiter.
    sin_relleno = claro.rstrip(b"\x00")
    if not sin_relleno or sin_relleno[-1] != 0x02:
        raise CifradoError("falta el delimitador del último registro")
    return sin_relleno[:-1]

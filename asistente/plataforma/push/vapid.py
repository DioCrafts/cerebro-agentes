"""VAPID (RFC 8292): how this server identifies itself to the browsers' push services.

The operator generates ONE key pair (``python -m hacienda_gpt.push.vapid``) and sets:

* ``PUSH_VAPID_PRIVATE_KEY`` — the private key: the base64url of its 32-byte scalar (the format of
  ``web-push generate-vapid-keys``) or a PEM block. A secret: environment or secret store, never the repo.
* ``PUSH_VAPID_PUBLIC_KEY`` — the public key, base64url of the 65-byte uncompressed point. Optional: it is
  derived from the private key; when set it must match (a typo would make every subscription unusable).
* ``PUSH_VAPID_SUBJECT`` — a contact for the push services: ``mailto:…`` or an ``https://`` URL (RFC 8292 §2.1).

Each request to a push service carries ``Authorization: vapid t=<JWT>, k=<public key>`` (RFC 8292 §3), the JWT
signed with ES256 (RFC 7515/7518: the signature is the raw ``r || s``, 64 bytes) and claiming ``aud`` = the
origin of the push endpoint, ``exp`` = at most 24 h ahead, ``sub`` = the subject above.

Changing the key pair invalidates every subscription (the browsers bound them to the old public key): the
users have to switch the notices on again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from hacienda_gpt.push.cifrado import (
    CifradoError,
    b64url_decode,
    b64url_encode,
    cargar_privada,
    cargar_publica,
    publica_cruda,
)

# RFC 8292 §2: "exp" MUST NOT be more than 24 hours from the time of the request.
VALIDEZ_MAXIMA = timedelta(hours=24)
VALIDEZ = timedelta(hours=12)


class ConfiguracionVapidError(ValueError):
    """The PUSH_VAPID_* settings are incomplete or wrong. The message is for the operator (Spanish)."""


@dataclass(frozen=True)
class ClavesVapid:
    privada: ec.EllipticCurvePrivateKey
    publica_b64: str
    sujeto: str


def _cargar_privada_texto(texto: str) -> ec.EllipticCurvePrivateKey:
    texto = texto.strip()
    if texto.startswith("-----BEGIN"):
        try:
            clave = serialization.load_pem_private_key(texto.encode("ascii"), password=None)
        except (ValueError, TypeError) as exc:
            raise ConfiguracionVapidError("PUSH_VAPID_PRIVATE_KEY no es una clave PEM legible.") from exc
        if not isinstance(clave, ec.EllipticCurvePrivateKey) or not isinstance(clave.curve, ec.SECP256R1):
            raise ConfiguracionVapidError("PUSH_VAPID_PRIVATE_KEY debe ser una clave de curva P-256.")
        return clave
    try:
        return cargar_privada(b64url_decode(texto))
    except CifradoError as exc:
        raise ConfiguracionVapidError(
            "PUSH_VAPID_PRIVATE_KEY debe ser el base64url de los 32 bytes de una clave P-256 (o un bloque PEM)."
        ) from exc


def validar_sujeto(sujeto: str) -> str:
    sujeto = sujeto.strip()
    if sujeto.startswith("mailto:") and "@" in sujeto:
        return sujeto
    partes = urlsplit(sujeto)
    if partes.scheme == "https" and partes.hostname:
        return sujeto
    raise ConfiguracionVapidError("PUSH_VAPID_SUBJECT debe ser «mailto:dirección@dominio» o una URL https://.")


def claves_desde_entorno(entorno: dict[str, str] | None = None) -> ClavesVapid | None:
    """The configured keys, or None when Web Push is off (no ``PUSH_VAPID_PRIVATE_KEY``).

    Raises :class:`ConfiguracionVapidError` when it is configured only in part or wrongly: silently running
    without push would look like "push is on" while nothing can be delivered.
    """
    entorno = dict(os.environ) if entorno is None else entorno
    privada_texto = entorno.get("PUSH_VAPID_PRIVATE_KEY", "").strip()
    publica_texto = entorno.get("PUSH_VAPID_PUBLIC_KEY", "").strip()
    sujeto = entorno.get("PUSH_VAPID_SUBJECT", "").strip()
    if not privada_texto:
        if publica_texto or sujeto:
            raise ConfiguracionVapidError(
                "Web Push está configurado a medias: falta PUSH_VAPID_PRIVATE_KEY "
                "(hacen falta PUSH_VAPID_PRIVATE_KEY y PUSH_VAPID_SUBJECT)."
            )
        return None
    if not sujeto:
        raise ConfiguracionVapidError("Web Push está configurado a medias: falta PUSH_VAPID_SUBJECT.")
    privada = _cargar_privada_texto(privada_texto)
    publica = b64url_encode(publica_cruda(privada.public_key()))
    if publica_texto:
        try:
            dada = publica_cruda(cargar_publica(b64url_decode(publica_texto)))
        except CifradoError as exc:
            raise ConfiguracionVapidError("PUSH_VAPID_PUBLIC_KEY no es una clave pública P-256 en base64url.") from exc
        if b64url_encode(dada) != publica:
            raise ConfiguracionVapidError("PUSH_VAPID_PUBLIC_KEY no corresponde a PUSH_VAPID_PRIVATE_KEY.")
    return ClavesVapid(privada=privada, publica_b64=publica, sujeto=validar_sujeto(sujeto))


def audiencia(endpoint: str) -> str:
    """The ``aud`` claim: the origin of the push endpoint (RFC 8292 §2)."""
    partes = urlsplit(endpoint)
    if partes.scheme != "https" or not partes.hostname:
        raise ConfiguracionVapidError("el endpoint de push debe ser una URL https")
    puerto = f":{partes.port}" if partes.port not in (None, 443) else ""
    return f"https://{partes.hostname}{puerto}"


def firmar_jwt(claves: ClavesVapid, aud: str, *, ahora: datetime | None = None, validez: timedelta = VALIDEZ) -> str:
    """A compact ES256 JWT with ``aud``, ``exp`` and ``sub`` (RFC 8292 §2)."""
    if validez > VALIDEZ_MAXIMA or validez <= timedelta(0):
        raise ValueError("la validez de un JWT de VAPID debe ser positiva y de 24 h como mucho")
    ahora = ahora or datetime.now(UTC)
    cabecera = {"typ": "JWT", "alg": "ES256"}
    reclamaciones = {"aud": aud, "exp": int((ahora + validez).timestamp()), "sub": claves.sujeto}
    entrada = ".".join(
        b64url_encode(json.dumps(parte, separators=(",", ":")).encode("utf-8")) for parte in (cabecera, reclamaciones)
    )
    der = claves.privada.sign(entrada.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{entrada}.{b64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def cabecera_autorizacion(claves: ClavesVapid, endpoint: str, *, ahora: datetime | None = None) -> str:
    """The ``Authorization`` header value for a request to ``endpoint`` (RFC 8292 §3)."""
    return f"vapid t={firmar_jwt(claves, audiencia(endpoint), ahora=ahora)}, k={claves.publica_b64}"


def verificar_jwt(jwt: str, publica_b64: str) -> dict[str, object]:
    """Check an ES256 JWT against a public key and return its claims (what a push service does; for tests).

    Raises ``ValueError`` when the token is malformed or the signature does not match.
    """
    try:
        cabecera_b64, reclamaciones_b64, firma_b64 = jwt.split(".")
    except ValueError as exc:
        raise ValueError("un JWT tiene tres partes") from exc
    cabecera = json.loads(b64url_decode(cabecera_b64))
    if cabecera.get("alg") != "ES256":
        raise ValueError("algoritmo distinto de ES256")
    firma = b64url_decode(firma_b64)
    if len(firma) != 64:
        raise ValueError("una firma ES256 tiene 64 bytes")
    der = encode_dss_signature(int.from_bytes(firma[:32], "big"), int.from_bytes(firma[32:], "big"))
    publica = cargar_publica(b64url_decode(publica_b64))
    try:
        publica.verify(der, f"{cabecera_b64}.{reclamaciones_b64}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("firma no válida") from exc
    reclamaciones: dict[str, object] = json.loads(b64url_decode(reclamaciones_b64))
    return reclamaciones


def generar_claves() -> tuple[str, str]:
    """A new VAPID key pair as (private, public), both base64url — the values of the two settings."""
    privada = ec.generate_private_key(ec.SECP256R1())
    escalar = privada.private_numbers().private_value.to_bytes(32, "big")
    return b64url_encode(escalar), b64url_encode(publica_cruda(privada.public_key()))


if __name__ == "__main__":  # pragma: no cover - operator helper
    clave_privada, clave_publica = generar_claves()
    print("# Guarda la clave privada como secreto; la pública puede ser visible.")
    print(f"PUSH_VAPID_PRIVATE_KEY={clave_privada}")
    print(f"PUSH_VAPID_PUBLIC_KEY={clave_publica}")
    print("PUSH_VAPID_SUBJECT=mailto:tu-direccion@tu-dominio")

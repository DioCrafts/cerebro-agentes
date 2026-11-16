"""Web Push encryption (RFC 8291 / RFC 8188) and VAPID (RFC 8292).

The encryption vector is the worked example of RFC 8291 §5 ("When I grow up, I want to be a watermelon"),
taken as hex from the IETF-vector tests of Mozilla's ``ece`` crate 2.3.1 (``try_encrypt_ietf_rfc`` /
``test_decrypt_ietf_rfc`` in src/lib.rs, "test data from the RFC"); the base64url forms in the RFC text
(salt ``DGv6ra1nlYgDCS1FRnbzlw``, output starting ``DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGR``) are the
same bytes. RFC 8292 has no deterministic vector (ES256 signatures are randomised), so VAPID is checked by
verifying the signature and the claims the RFC requires.
"""

from datetime import UTC, datetime, timedelta
import json

from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from asistente.plataforma.push.cifrado import (
    CifradoError,
    b64url_decode,
    b64url_encode,
    cargar_privada,
    cargar_publica,
    cifrar,
    descifrar,
    publica_cruda,
)
from asistente.plataforma.push.vapid import (
    ClavesVapid,
    ConfiguracionVapidError,
    audiencia,
    cabecera_autorizacion,
    claves_desde_entorno,
    firmar_jwt,
    generar_claves,
    verificar_jwt,
)

# RFC 8291 §5.
PLAINTEXT = b"When I grow up, I want to be a watermelon"
AS_PRIVATE = "c9f58f89813e9f8e872e71f42aa64e1757c9254dcc62b72ddc010bb4043ea11c"
AS_PUBLIC = (
    "04fe33f4ab0dea71914db55823f73b54948f41306d920732dbb9a59a53286482200e597a7b7bc260ba1c227998580992e93973002f30"
    "12a28ae8f06bbb78e5ec0f"
)
UA_PRIVATE = "ab5757a70dd4a53e553a6bbf71ffefea2874ec07a6b379e3c48f895a02dc33de"
UA_PUBLIC = (
    "042571b2becdfde360551aaf1ed0f4cd366c11cebe555f89bcb7b186a53339173168ece2ebe018597bd30479b86e3c8f8eced577ca59"
    "187e9246990db682008b0e"
)
AUTH_SECRET = "05305932a1c7eabe13b6cec9fda48882"
SALT = "0c6bfaadad67958803092d454676f397"
EXPECTED = (
    "0c6bfaadad67958803092d454676f397000010004104fe33f4ab0dea71914db55823f73b54948f41306d920732dbb9a59a5328648220"
    "0e597a7b7bc260ba1c227998580992e93973002f3012a28ae8f06bbb78e5ec0ff297de5b429bba7153d3a4ae0caa091fd425f3b4b541"
    "4add8ab37a19c1bbb05cf5cb5b2a2e0562d558635641ec52812c6c8ff42e95ccb86be7cd"
)


def test_rfc8291_example_encrypts_to_the_published_message() -> None:
    as_private = cargar_privada(bytes.fromhex(AS_PRIVATE))
    assert publica_cruda(as_private.public_key()).hex() == AS_PUBLIC
    salida = cifrar(
        bytes.fromhex(UA_PUBLIC),
        bytes.fromhex(AUTH_SECRET),
        PLAINTEXT,
        clave_efimera=as_private,
        sal=bytes.fromhex(SALT),
    )
    assert salida.hex() == EXPECTED
    # The base64url form printed in the RFC text starts like this.
    assert b64url_encode(salida).startswith("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGR")


def test_rfc8291_example_decrypts_with_the_user_agent_key() -> None:
    ua_private = cargar_privada(bytes.fromhex(UA_PRIVATE))
    assert publica_cruda(ua_private.public_key()).hex() == UA_PUBLIC
    assert descifrar(ua_private, bytes.fromhex(AUTH_SECRET), bytes.fromhex(EXPECTED)) == PLAINTEXT


def test_fresh_keys_and_salt_every_message_and_round_trip() -> None:
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    mensaje = json.dumps({"titulo": "HaciendaGPT", "cuerpo": "Tienes un aviso urgente: abre la app"}).encode()
    uno = cifrar(publica_cruda(ua.public_key()), auth, mensaje)
    dos = cifrar(publica_cruda(ua.public_key()), auth, mensaje)
    assert uno[:16] != dos[:16]  # salt
    assert uno[21:86] != dos[21:86]  # ephemeral server key
    assert descifrar(ua, auth, uno) == mensaje == descifrar(ua, auth, dos)
    with pytest.raises(Exception):
        descifrar(ua, b"fedcba9876543210", uno)


def test_rejects_bad_keys_and_oversized_messages() -> None:
    ua = publica_cruda(ec.generate_private_key(ec.SECP256R1()).public_key())
    with pytest.raises(CifradoError):
        cifrar(ua[:-1], b"0" * 16, b"x")
    with pytest.raises(CifradoError):
        cifrar(b"\x04" + b"\x01" * 64, b"0" * 16, b"x")  # not on the curve
    with pytest.raises(CifradoError):
        cifrar(ua, b"short", b"x")
    with pytest.raises(CifradoError):
        cifrar(ua, b"0" * 16, b"x" * 4096)
    with pytest.raises(CifradoError):
        cargar_publica(b"")
    assert b64url_decode("AQ") == b"\x01"


def _claves() -> ClavesVapid:
    privada, _ = generar_claves()
    claves = claves_desde_entorno({"PUSH_VAPID_PRIVATE_KEY": privada, "PUSH_VAPID_SUBJECT": "mailto:op@example.org"})
    assert claves is not None
    return claves


def test_vapid_jwt_is_es256_with_the_required_claims() -> None:
    claves = _claves()
    ahora = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    jwt = firmar_jwt(claves, "https://fcm.googleapis.com", ahora=ahora)
    cabecera = json.loads(b64url_decode(jwt.split(".")[0]))
    assert cabecera == {"typ": "JWT", "alg": "ES256"}
    reclamaciones = verificar_jwt(jwt, claves.publica_b64)
    assert reclamaciones["aud"] == "https://fcm.googleapis.com"
    assert reclamaciones["sub"] == "mailto:op@example.org"
    exp = datetime.fromtimestamp(int(str(reclamaciones["exp"])), UTC)
    assert ahora < exp <= ahora + timedelta(hours=24)
    # Another key does not verify it; a tampered token does not either.
    otra = _claves()
    with pytest.raises(ValueError):
        verificar_jwt(jwt, otra.publica_b64)
    partes = jwt.split(".")
    falsas = b64url_encode(json.dumps({"aud": "https://evil.example", "exp": 1, "sub": "x"}).encode())
    with pytest.raises(ValueError):
        verificar_jwt(f"{partes[0]}.{falsas}.{partes[2]}", claves.publica_b64)
    with pytest.raises(ValueError):
        firmar_jwt(claves, "https://x", validez=timedelta(hours=25))


def test_authorization_header_uses_the_endpoint_origin() -> None:
    claves = _claves()
    cabecera = cabecera_autorizacion(claves, "https://updates.push.services.mozilla.com/wpush/v2/abc?x=1")
    assert cabecera.startswith("vapid t=")
    token, k = cabecera[len("vapid t=") :].split(", k=")
    assert k == claves.publica_b64
    assert verificar_jwt(token, k)["aud"] == "https://updates.push.services.mozilla.com"
    assert audiencia("https://push.example:8443/a") == "https://push.example:8443"
    with pytest.raises(ConfiguracionVapidError):
        audiencia("http://fcm.googleapis.com/x")


def test_vapid_settings_validation() -> None:
    privada, publica = generar_claves()
    assert claves_desde_entorno({}) is None
    with pytest.raises(ConfiguracionVapidError, match="PUSH_VAPID_PRIVATE_KEY"):
        claves_desde_entorno({"PUSH_VAPID_SUBJECT": "mailto:a@b.c"})
    with pytest.raises(ConfiguracionVapidError, match="PUSH_VAPID_SUBJECT"):
        claves_desde_entorno({"PUSH_VAPID_PRIVATE_KEY": privada})
    with pytest.raises(ConfiguracionVapidError):
        claves_desde_entorno({"PUSH_VAPID_PRIVATE_KEY": privada, "PUSH_VAPID_SUBJECT": "op@example.org"})
    with pytest.raises(ConfiguracionVapidError, match="no corresponde"):
        claves_desde_entorno(
            {
                "PUSH_VAPID_PRIVATE_KEY": privada,
                "PUSH_VAPID_PUBLIC_KEY": generar_claves()[1],
                "PUSH_VAPID_SUBJECT": "https://example.org",
            }
        )
    with pytest.raises(ConfiguracionVapidError):
        claves_desde_entorno({"PUSH_VAPID_PRIVATE_KEY": "no-es-una-clave", "PUSH_VAPID_SUBJECT": "mailto:a@b.c"})
    claves = claves_desde_entorno(
        {"PUSH_VAPID_PRIVATE_KEY": privada, "PUSH_VAPID_PUBLIC_KEY": publica, "PUSH_VAPID_SUBJECT": "mailto:a@b.c"}
    )
    assert claves is not None and claves.publica_b64 == publica

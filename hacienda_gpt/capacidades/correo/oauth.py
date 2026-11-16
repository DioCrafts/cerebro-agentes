"""Google OAuth 2.0 for Gmail: authorization-code flow with PKCE (S256), read-only scope, revocation.

Flow (Google's documented web-server flow):

1. :func:`iniciar` — random ``state`` (stored hashed, single use, 10 min) and PKCE ``code_verifier`` (stored
   encrypted); returns the consent URL with ``scope=gmail.readonly``, ``access_type=offline``,
   ``prompt=consent`` (so a refresh token comes back), ``include_granted_scopes=true`` (later phases add
   scopes incrementally), ``code_challenge`` and ``code_challenge_method=S256``.
2. Google redirects the browser to ``CORREO_REDIRECT_URI`` (the API's ``/correo/oauth/callback``):
   :func:`completar` consumes the ``state``, exchanges the ``code`` with the verifier, checks the granted
   scopes, reads the account address (``users.getProfile``) and stores the refresh token ENCRYPTED.
3. :func:`token_de_acceso` refreshes an access token when needed (kept in memory only).
4. :func:`desconectar` revokes the grant at Google and deletes the token, whatever Google answers.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import logging
import secrets
import threading
from typing import Any
from urllib.parse import urlencode

from hacienda_gpt.capacidades.correo import almacen, cifrado
from hacienda_gpt.capacidades.correo.config import SCOPE_LECTURA, SCOPES_FASE_1, Configuracion, configuracion
from hacienda_gpt.capacidades.correo.http import CorreoError, Transporte, transporte

logger = logging.getLogger(__name__)


class ConexionCaducadaError(CorreoError):
    """Google no longer accepts the refresh token (revoked by the user, expired): connect again."""


_cache: dict[str, tuple[str, datetime]] = {}
_cache_lock = threading.Lock()


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _exigir(conf: Configuracion) -> None:
    if not conf.completa:
        raise CorreoError(
            "El servicio no tiene configurada la conexión con Gmail (faltan: " + ", ".join(conf.faltan()) + ")."
        )


def iniciar(case_id: str, user_id: str | None, *, db_path: str | None = None) -> str:
    """The Google consent URL for this case and user."""
    conf = configuracion()
    _exigir(conf)
    estado = secrets.token_urlsafe(32)
    verificador = secrets.token_urlsafe(64)[:96]
    reto = _b64(hashlib.sha256(verificador.encode("ascii")).digest())
    almacen.guardar_estado_oauth(
        estado,
        case_id=case_id,
        user_id=user_id,
        verificador_cifrado=cifrado.cifrar(verificador, case_id=case_id, user_id=user_id, proposito="pkce"),
        db_path=db_path,
    )
    params = {
        "client_id": conf.client_id,
        "redirect_uri": conf.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES_FASE_1),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": estado,
        "code_challenge": reto,
        "code_challenge_method": "S256",
    }
    return f"{conf.auth_url}?{urlencode(params)}"


def _token(conf: Configuracion, datos: dict[str, str], http: Transporte) -> dict[str, Any]:
    respuesta = http("POST", conf.token_url, datos=datos)
    cuerpo = respuesta.json() if respuesta.cuerpo else {}
    if respuesta.estado == 400 and isinstance(cuerpo, dict) and cuerpo.get("error") == "invalid_grant":
        raise ConexionCaducadaError("Google ya no acepta el permiso concedido. Vuelve a conectar Gmail.")
    if respuesta.estado != 200 or not isinstance(cuerpo, dict) or not cuerpo.get("access_token"):
        logger.warning("Google token endpoint answered %s", respuesta.estado)
        raise CorreoError("Google no ha entregado el permiso. Vuelve a intentarlo.")
    return cuerpo


def completar(
    estado: str, codigo: str, *, db_path: str | None = None, http: Transporte | None = None
) -> almacen.Conexion:
    """Finish the flow for a ``state`` this service issued. Raises CorreoError (Spanish) otherwise."""
    conf = configuracion()
    _exigir(conf)
    http = http or transporte()
    pendiente = almacen.consumir_estado_oauth(estado, db_path)
    if pendiente is None:
        raise CorreoError("La solicitud de conexión no es válida o ha caducado. Vuelve a pulsar «Conectar Gmail».")
    case_id, user_id = pendiente["case_id"], pendiente["user_id"]
    try:
        verificador = cifrado.descifrar(
            pendiente["verificador_cifrado"], case_id=case_id, user_id=user_id, proposito="pkce"
        )
    except ValueError as exc:
        raise CorreoError("La solicitud de conexión no es válida. Vuelve a empezar.") from exc
    cuerpo = _token(
        conf,
        {
            "code": codigo,
            "client_id": conf.client_id,
            "client_secret": conf.client_secret,
            "redirect_uri": conf.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verificador,
        },
        http,
    )
    concedidos = str(cuerpo.get("scope") or "").split()
    refresco = cuerpo.get("refresh_token")
    if SCOPE_LECTURA not in concedidos:
        _revocar(conf, str(refresco or cuerpo["access_token"]), http)
        raise CorreoError("No se concedió el permiso de lectura de Gmail; no se ha conectado nada.")
    if not isinstance(refresco, str) or not refresco:
        raise CorreoError(
            "Google no entregó un permiso duradero. Quita el acceso de esta aplicación en tu cuenta de Google y vuelve a conectar."
        )
    acceso = str(cuerpo["access_token"])
    _guardar_en_cache(case_id, acceso, int(cuerpo.get("expires_in") or 3600))
    cuenta: str | None = None
    perfil = http("GET", f"{conf.api_url}/users/me/profile", cabeceras={"Authorization": f"Bearer {acceso}"})
    if perfil.estado == 200 and isinstance(datos := perfil.json(), dict):
        cuenta = str(datos.get("emailAddress") or "")[:320] or None
    conexion = almacen.Conexion(
        case_id=case_id,
        user_id=user_id,
        cuenta=cuenta,
        alcance=sorted(set(concedidos) & set(SCOPES_FASE_1)),
        token_cifrado=cifrado.cifrar(refresco, case_id=case_id, user_id=user_id),
        conectada_el=datetime.now(UTC),
    )
    anterior = almacen.conexion_de(case_id, db_path)
    if anterior is not None:  # a new grant replaces the old one, which is revoked
        _revocar_conexion(conf, anterior, http)
    almacen.guardar_conexion(conexion, db_path)
    return conexion


def _guardar_en_cache(case_id: str, acceso: str, segundos: int) -> None:
    with _cache_lock:
        _cache[case_id] = (acceso, datetime.now(UTC) + timedelta(seconds=max(60, segundos) - 60))


def olvidar_cache(case_id: str | None = None) -> None:
    with _cache_lock:
        if case_id is None:
            _cache.clear()
        else:
            _cache.pop(case_id, None)


def token_de_acceso(conexion: almacen.Conexion, *, db_path: str | None = None, http: Transporte | None = None) -> str:
    """A valid access token for the connection (refreshed when needed; never stored on disk)."""
    with _cache_lock:
        guardado = _cache.get(conexion.case_id)
    if guardado is not None and guardado[1] > datetime.now(UTC):
        return guardado[0]
    conf = configuracion()
    _exigir(conf)
    try:
        refresco = cifrado.descifrar(conexion.token_cifrado, case_id=conexion.case_id, user_id=conexion.user_id)
    except ValueError as exc:
        almacen.actualizar_conexion(conexion.case_id, db_path, estado="reconectar", ultimo_error="token ilegible")
        raise ConexionCaducadaError("No se puede usar el permiso guardado. Vuelve a conectar Gmail.") from exc
    try:
        cuerpo = _token(
            conf,
            {
                "client_id": conf.client_id,
                "client_secret": conf.client_secret,
                "refresh_token": refresco,
                "grant_type": "refresh_token",
            },
            http or transporte(),
        )
    except ConexionCaducadaError:
        almacen.actualizar_conexion(
            conexion.case_id, db_path, estado="reconectar", ultimo_error="permiso revocado o caducado"
        )
        raise
    acceso = str(cuerpo["access_token"])
    _guardar_en_cache(conexion.case_id, acceso, int(cuerpo.get("expires_in") or 3600))
    return acceso


def _revocar(conf: Configuracion, token: str, http: Transporte) -> bool:
    try:
        respuesta = http("POST", conf.revoke_url, datos={"token": token})
    except CorreoError:
        return False
    return respuesta.estado == 200


def _revocar_conexion(conf: Configuracion, conexion: almacen.Conexion, http: Transporte) -> bool:
    try:
        refresco = cifrado.descifrar(conexion.token_cifrado, case_id=conexion.case_id, user_id=conexion.user_id)
    except (ValueError, cifrado.CifradoNoDisponibleError):
        return False
    return _revocar(conf, refresco, http)


def revocar_token_cifrado(
    token_cifrado: str, *, case_id: str, user_id: str | None, http: Transporte | None = None
) -> bool:
    """Revoke a stored grant at Google (used after an erasure). False when not configured or refused."""
    conf = configuracion()
    if not conf.completa:
        return False
    try:
        refresco = cifrado.descifrar(token_cifrado, case_id=case_id, user_id=user_id)
    except (ValueError, cifrado.CifradoNoDisponibleError):
        return False
    return _revocar(conf, refresco, http or transporte())


def desconectar(case_id: str, *, db_path: str | None = None, http: Transporte | None = None) -> bool | None:
    """Revoke at Google and delete the token. Returns whether Google confirmed (None: nothing connected).

    The token is deleted even if Google cannot be reached: from then on this service cannot read the mail.
    """
    conexion = almacen.conexion_de(case_id, db_path)
    if conexion is None:
        return None
    conf = configuracion()
    revocado = _revocar_conexion(conf, conexion, http or transporte()) if conf.completa else False
    almacen.borrar_conexion(case_id, db_path)
    olvidar_cache(case_id)
    return revocado


def revocar_de_usuario(case_ids: list[str], *, db_path: str | None = None) -> int:
    """Account deletion hook: revoke and delete the Gmail grant of every case of the user."""
    return sum(1 for case_id in case_ids if desconectar(case_id, db_path=db_path))


__all__ = [
    "ConexionCaducadaError",
    "completar",
    "desconectar",
    "iniciar",
    "olvidar_cache",
    "revocar_de_usuario",
    "revocar_token_cifrado",
    "token_de_acceso",
]

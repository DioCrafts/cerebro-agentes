"""Operator settings of the ``correo`` module, read from the environment at call time.

Nothing here is a secret that is logged or returned: ``client_secret`` and the encryption key are only
handed to the OAuth client and to Fernet. The Google endpoints default to Google's documented URLs; the
operator may point them elsewhere only for a local test double (see ``tests/modulos/correo/servidor_falso.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
import os

# The ONLY scope this phase requests. Later phases (drafts, labels) add scopes incrementally with
# ``include_granted_scopes``; nothing here needs to change for that except this tuple.
SCOPE_LECTURA = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES_FASE_1: tuple[str, ...] = (SCOPE_LECTURA,)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1"

VENTANA_DIAS_POR_DEFECTO = 30
VENTANA_DIAS_MAX = 90
MAX_MENSAJES_POR_SINCRONIZACION = 300


def _entero(nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(os.environ.get(nombre, "") or defecto)
    except ValueError:
        return defecto
    return max(minimo, min(maximo, valor))


@dataclass(frozen=True)
class Configuracion:
    client_id: str
    client_secret: str
    redirect_uri: str
    clave_cifrado: str
    auth_url: str
    token_url: str
    revoke_url: str
    api_url: str
    # Where the browser goes back after the consent screen (the web app); never taken from a request.
    url_retorno: str
    ventana_dias: int
    max_mensajes: int
    # Optional LLM classifier for what the rules leave as «otro» (off by default).
    clasificador_llm: bool

    @property
    def completa(self) -> bool:
        """The operator set everything the OAuth flow needs."""
        return bool(self.client_id and self.client_secret and self.redirect_uri and self.clave_cifrado)

    def faltan(self) -> list[str]:
        nombres = {
            "CORREO_GOOGLE_CLIENT_ID": self.client_id,
            "CORREO_GOOGLE_CLIENT_SECRET": self.client_secret,
            "CORREO_REDIRECT_URI": self.redirect_uri,
            "CORREO_CLAVE_CIFRADO": self.clave_cifrado,
        }
        return [nombre for nombre, valor in nombres.items() if not valor]


def configuracion() -> Configuracion:
    entorno = os.environ.get
    return Configuracion(
        client_id=entorno("CORREO_GOOGLE_CLIENT_ID", "").strip(),
        client_secret=entorno("CORREO_GOOGLE_CLIENT_SECRET", "").strip(),
        redirect_uri=entorno("CORREO_REDIRECT_URI", "").strip(),
        clave_cifrado=entorno("CORREO_CLAVE_CIFRADO", "").strip(),
        auth_url=entorno("CORREO_GOOGLE_AUTH_URL", "").strip() or AUTH_URL,
        token_url=entorno("CORREO_GOOGLE_TOKEN_URL", "").strip() or TOKEN_URL,
        revoke_url=entorno("CORREO_GOOGLE_REVOKE_URL", "").strip() or REVOKE_URL,
        api_url=(entorno("CORREO_GMAIL_API_URL", "").strip() or GMAIL_API_URL).rstrip("/"),
        url_retorno=entorno("CORREO_URL_RETORNO", "").strip() or "/",
        ventana_dias=_entero("CORREO_VENTANA_DIAS", VENTANA_DIAS_POR_DEFECTO, 1, VENTANA_DIAS_MAX),
        max_mensajes=_entero("CORREO_MAX_MENSAJES", MAX_MENSAJES_POR_SINCRONIZACION, 1, 2000),
        clasificador_llm=entorno("CORREO_CLASIFICADOR_LLM", "").strip() == "1",
    )


__all__ = [
    "GMAIL_API_URL",
    "SCOPES_FASE_1",
    "SCOPE_LECTURA",
    "VENTANA_DIAS_MAX",
    "VENTANA_DIAS_POR_DEFECTO",
    "Configuracion",
    "configuracion",
]

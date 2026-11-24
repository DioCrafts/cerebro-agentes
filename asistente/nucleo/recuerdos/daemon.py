"""Where Hindsight runs: an existing server (``HINDSIGHT_URL``) or the local embedded daemon.

Without ``HINDSIGHT_URL`` the app uses ``hindsight-all``'s :class:`~hindsight.HindsightEmbedded`: a daemon in its
own OS process, listening on ``127.0.0.1``, with an embedded PostgreSQL under ``~/.pg0/instances/
hindsight-embed-<HINDSIGHT_PERFIL>/``. It is started on first use and reused afterwards, also by later processes
(the API, the worker and the CLIs share it); the first start downloads the local models and takes a minute or two.

The daemon is configured from the settings: the LLM it uses to extract facts (``HINDSIGHT_LLM_*``), a multilingual
embedder (``HINDSIGHT_EMBEDDINGS``) and the Spanish dictionary for its BM25 arm (``HINDSIGHT_IDIOMA``). The API key is
NOT written into the profile: the daemon inherits it from this process's environment (``HINDSIGHT_API_LLM_API_KEY``,
taken from ``ANTHROPIC_API_KEY`` when unset), so it never lands in ``~/.hindsight``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from asistente.plataforma import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_embebido: Any = None
_calentando: threading.Thread | None = None
_avisado = False

# The provider's own API key variable, for the LLM Hindsight uses.
_CLAVE_DEL_PROVEEDOR = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def configuracion_daemon() -> dict[str, str]:
    """The daemon's settings (``HINDSIGHT_API_*`` variables), without any secret."""
    return {
        "HINDSIGHT_API_LLM_PROVIDER": settings.HINDSIGHT_LLM_PROVEEDOR,
        "HINDSIGHT_API_LLM_MODEL": settings.HINDSIGHT_LLM_MODELO,
        "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL": settings.HINDSIGHT_EMBEDDINGS,
        "HINDSIGHT_API_TEXT_SEARCH_EXTENSION": "native",
        "HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE": settings.HINDSIGHT_IDIOMA,
        "HINDSIGHT_API_LOG_LEVEL": "warning",
    }


def _heredar_clave() -> None:
    """Expose the provider's key to the daemon through the environment it inherits (never through its profile)."""
    if os.environ.get("HINDSIGHT_API_LLM_API_KEY"):
        return
    variable = _CLAVE_DEL_PROVEEDOR.get(settings.HINDSIGHT_LLM_PROVEEDOR)
    clave = os.environ.get(variable or "", "")
    if clave:
        os.environ["HINDSIGHT_API_LLM_API_KEY"] = clave


def tiene_clave() -> bool:
    """Whether the daemon can start: Hindsight refuses to without its LLM's key (a local provider needs none)."""
    variable = _CLAVE_DEL_PROVEEDOR.get(settings.HINDSIGHT_LLM_PROVEEDOR)
    return variable is None or bool(os.environ.get("HINDSIGHT_API_LLM_API_KEY") or os.environ.get(variable))


def _arrancar_embebido() -> str:
    global _embebido
    if not tiene_clave():
        raise RuntimeError(
            f"Hindsight necesita la clave de {settings.HINDSIGHT_LLM_PROVEEDOR} para arrancar "
            f"({_CLAVE_DEL_PROVEEDOR.get(settings.HINDSIGHT_LLM_PROVEEDOR)})."
        )
    with _lock:
        if _embebido is None:
            from hindsight import HindsightEmbedded

            _heredar_clave()
            embebido = HindsightEmbedded(profile=settings.HINDSIGHT_PERFIL)
            embebido.config.update(configuracion_daemon())
            _embebido = embebido
        embebido = _embebido
    # Starts the daemon if it is not running (thread-safe inside hindsight); restarts it after a crash.
    embebido._ensure_started()
    return str(embebido.url)


def url() -> str:
    """The base URL of Hindsight, starting the embedded daemon if needed (may take minutes the first time)."""
    if settings.HINDSIGHT_URL:
        return settings.HINDSIGHT_URL
    return _arrancar_embebido()


def calentar() -> None:
    """Start the embedded daemon in the background, so the first turn does not wait for it. Never raises."""
    global _calentando
    if settings.HINDSIGHT_URL or not settings.MEMORIA_CONVERSACIONES:
        return
    if not tiene_clave():
        global _avisado
        if not _avisado:
            _avisado = True
            logger.warning("Hindsight: no API key for %s; the memory stays off", settings.HINDSIGHT_LLM_PROVEEDOR)
        return

    def _arrancar() -> None:
        try:
            _arrancar_embebido()
        except Exception:
            logger.exception("Hindsight: the embedded daemon could not be started")

    with _lock:
        if _calentando is not None and _calentando.is_alive():
            return
        _calentando = threading.Thread(target=_arrancar, name="hindsight-arranque", daemon=True)
        _calentando.start()


def listo() -> bool:
    """Whether Hindsight can be used right now without waiting for a start-up."""
    if settings.HINDSIGHT_URL:
        return True
    with _lock:
        embebido = _embebido
    try:
        return embebido is not None and bool(embebido.is_running)
    except Exception:
        return False


def olvidar() -> None:
    """Forget the embedded handle (tests). The daemon itself keeps running."""
    global _embebido, _calentando
    with _lock:
        _embebido = None
        _calentando = None


__all__ = ["calentar", "configuracion_daemon", "listo", "olvidar", "tiene_clave", "url"]

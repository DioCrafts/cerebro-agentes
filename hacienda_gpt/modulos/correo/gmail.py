"""Read-only Gmail API client (REST, v1), built from Google's documented request/response format.

Used endpoints (all ``GET`` under ``https://gmail.googleapis.com/gmail/v1/users/me``):

* ``/profile`` — ``{emailAddress, messagesTotal, threadsTotal, historyId}``;
* ``/messages?q=&maxResults=&pageToken=`` — ``{messages: [{id, threadId}], nextPageToken?}``;
* ``/messages/{id}?format=metadata&metadataHeaders=…`` and ``?format=full`` — a Message resource;
* ``/history?startHistoryId=&historyTypes=messageAdded&pageToken=`` — ``{history: [{messagesAdded:
  [{message: {id, threadId, labelIds}}]}], nextPageToken?, historyId}``; **404** when the start id is too old
  (the documented signal to do a full sync again).

Never ran against the real API from the development machine (only www.boe.es is reachable): tested with
fixtures written from the documentation and a local fake server.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import re
from typing import Any

from hacienda_gpt.modulos.correo.http import CorreoError, Transporte

logger = logging.getLogger(__name__)

CABECERAS_METADATOS: tuple[str, ...] = (
    "From",
    "Reply-To",
    "Subject",
    "Date",
    "List-Unsubscribe",
    "Authentication-Results",
    "Content-Type",
)
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MAX_PAGINAS = 20


class HistorialCaducadoError(CorreoError):
    """``startHistoryId`` is no longer valid (HTTP 404): a windowed full sync is needed."""


class ClienteGmail:
    def __init__(self, api_url: str, token: Callable[[], str], http: Transporte) -> None:
        self._base = f"{api_url.rstrip('/')}/users/me"
        self._token = token
        self._http = http

    def _get(self, ruta: str, params: dict[str, Any] | None = None) -> Any:
        respuesta = self._http(
            "GET", f"{self._base}{ruta}", params=params, cabeceras={"Authorization": f"Bearer {self._token()}"}
        )
        if respuesta.estado == 404 and ruta == "/history":
            raise HistorialCaducadoError("El historial de Gmail ha caducado.")
        if respuesta.estado == 401:
            raise CorreoError("Gmail no ha aceptado el permiso. Vuelve a conectar Gmail.")
        if respuesta.estado == 429:
            raise CorreoError("Gmail pide esperar (límite de uso). Inténtalo en unos minutos.")
        if respuesta.estado != 200:
            logger.warning("Gmail API %s answered %s", ruta.split("/")[1], respuesta.estado)
            raise CorreoError(f"Gmail respondió con un error ({respuesta.estado}).")
        return respuesta.json()

    def perfil(self) -> dict[str, Any]:
        datos = self._get("/profile")
        return datos if isinstance(datos, dict) else {}

    def listar(self, consulta: str, maximo: int) -> list[str]:
        """Message ids matching ``consulta`` (Gmail search syntax), newest first, at most ``maximo``."""
        ids: list[str] = []
        pagina: str | None = None
        for _ in range(MAX_PAGINAS):
            params: dict[str, Any] = {"q": consulta, "maxResults": min(100, maximo - len(ids))}
            if pagina:
                params["pageToken"] = pagina
            datos = self._get("/messages", params)
            for item in (datos or {}).get("messages") or []:
                if isinstance(item, dict) and _ID_RE.match(str(item.get("id", ""))):
                    ids.append(str(item["id"]))
            pagina = (datos or {}).get("nextPageToken")
            if not pagina or len(ids) >= maximo:
                break
        return ids[:maximo]

    def historial(self, desde: str, maximo: int) -> tuple[list[tuple[str, frozenset[str]]], str | None]:
        """(added message ids with their labels, newest historyId) since ``desde``."""
        nuevos: dict[str, frozenset[str]] = {}
        ultimo: str | None = None
        pagina: str | None = None
        for _ in range(MAX_PAGINAS):
            params: dict[str, Any] = {"startHistoryId": desde, "historyTypes": "messageAdded", "maxResults": 500}
            if pagina:
                params["pageToken"] = pagina
            datos = self._get("/history", params) or {}
            for registro in datos.get("history") or []:
                for anadido in (registro or {}).get("messagesAdded") or []:
                    mensaje = (anadido or {}).get("message") or {}
                    if _ID_RE.match(str(mensaje.get("id", ""))):
                        nuevos[str(mensaje["id"])] = frozenset(str(e) for e in mensaje.get("labelIds") or [])
            ultimo = str(datos.get("historyId") or ultimo or "") or None
            pagina = datos.get("nextPageToken")
            if not pagina or len(nuevos) >= maximo:
                break
        return list(nuevos.items())[:maximo], ultimo

    def mensaje(self, mensaje_id: str, *, completo: bool) -> dict[str, Any]:
        if not _ID_RE.match(mensaje_id):
            raise CorreoError("Identificador de mensaje no válido.")
        params: dict[str, Any] = {"format": "full" if completo else "metadata"}
        if not completo:
            params["metadataHeaders"] = list(CABECERAS_METADATOS)
        datos = self._get(f"/messages/{mensaje_id}", params)
        if not isinstance(datos, dict):
            raise CorreoError("Gmail devolvió un mensaje con un formato inesperado.")
        return datos


__all__ = ["CABECERAS_METADATOS", "ClienteGmail", "HistorialCaducadoError"]

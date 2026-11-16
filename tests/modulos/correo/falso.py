"""Gmail messages in the documented API format, and a fake Google (OAuth + Gmail API) as a transport.

Built from Google's public documentation of the REST resources (users.messages, users.history,
users.getProfile, the OAuth 2.0 token and revoke endpoints); never compared with real responses. The
same fake is served over HTTP by ``servidor_falso.py`` for the browser check.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from asistente.capacidades.correo.config import SCOPE_LECTURA
from asistente.capacidades.correo.http import RespuestaHttp

ACCESO = "ya29.acceso-falso"
REFRESCO = "1//refresco-falso-secreto"


def b64(texto: str | bytes) -> str:
    datos = texto.encode("utf-8") if isinstance(texto, str) else texto
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _auth(dominio: str, dmarc: str = "pass", spf: str = "pass", dkim: str = "pass") -> str:
    return (
        f"mx.google.com; dkim={dkim} header.i=@{dominio} header.s=s1; spf={spf} (google.com: domain of "
        f"x@{dominio} designates 1.2.3.4 as permitted sender) smtp.mailfrom=x@{dominio}; "
        f"dmarc={dmarc} (p=REJECT sp=REJECT dis=NONE) header.from={dominio}"
    )


def correo(
    id_: str,
    *,
    de: str = "Agencia Tributaria <avisos@agenciatributaria.gob.es>",
    asunto: str = "Aviso",
    texto: str | None = None,
    html: str | None = None,
    fecha: datetime = datetime(2026, 11, 15, 9, 30, tzinfo=UTC),
    etiquetas: Iterable[str] = ("INBOX", "UNREAD"),
    cabeceras: Iterable[tuple[str, str]] = (),
    adjuntos: Iterable[tuple[str, str]] = (),
    autenticacion: str | None = None,
    responder_a: str | None = None,
) -> dict[str, Any]:
    """A ``users.messages.get?format=full`` resource."""
    dominio = de.rsplit("@", 1)[-1].rstrip(">").strip()
    lista = [
        {"name": "Delivered-To", "value": "yo@gmail.com"},
        {"name": "Authentication-Results", "value": autenticacion or _auth(dominio)},
        {"name": "From", "value": de},
        {"name": "To", "value": "yo@gmail.com"},
        {"name": "Subject", "value": asunto},
        {"name": "Date", "value": fecha.strftime("%a, %d %b %Y %H:%M:%S +0000")},
        {"name": "Content-Type", "value": "multipart/mixed; boundary=xyz"},
    ]
    if responder_a:
        lista.append({"name": "Reply-To", "value": responder_a})
    lista.extend({"name": n, "value": v} for n, v in cabeceras)
    alternativas: list[dict[str, Any]] = []
    if texto is not None:
        alternativas.append(
            {
                "partId": "0.0",
                "mimeType": "text/plain",
                "filename": "",
                "headers": [{"name": "Content-Type", "value": "text/plain; charset=UTF-8"}],
                "body": {"size": len(texto.encode()), "data": b64(texto)},
            }
        )
    if html is not None:
        alternativas.append(
            {
                "partId": "0.1",
                "mimeType": "text/html",
                "filename": "",
                "headers": [{"name": "Content-Type", "value": "text/html; charset=UTF-8"}],
                "body": {"size": len(html.encode()), "data": b64(html)},
            }
        )
    partes: list[dict[str, Any]] = [
        {
            "partId": "0",
            "mimeType": "multipart/alternative",
            "filename": "",
            "headers": [],
            "body": {"size": 0},
            "parts": alternativas,
        }
    ]
    for i, (nombre, tipo) in enumerate(adjuntos, 1):
        partes.append(
            {
                "partId": str(i),
                "mimeType": tipo,
                "filename": nombre,
                "headers": [{"name": "Content-Disposition", "value": f'attachment; filename="{nombre}"'}],
                "body": {"attachmentId": f"ANGjdJ_{id_}_{i}", "size": 1234},
            }
        )
    return {
        "id": id_,
        "threadId": f"t{id_}",
        "labelIds": list(etiquetas),
        "snippet": (texto or "")[:100],
        "historyId": "999",
        "internalDate": str(int(fecha.timestamp() * 1000)),
        "payload": {
            "partId": "",
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": lista,
            "body": {"size": 0},
            "parts": partes,
        },
        "sizeEstimate": 4321,
    }


def metadatos(completo: Mapping[str, Any], cabeceras: Iterable[str] | None = None) -> dict[str, Any]:
    """``format=metadata``: the same resource without bodies, only the requested headers."""
    pedidas = {c.lower() for c in cabeceras} if cabeceras else None
    payload = completo["payload"]
    return {
        **{k: v for k, v in completo.items() if k != "payload"},
        "payload": {
            "partId": "",
            "mimeType": payload["mimeType"],
            "filename": "",
            "headers": [h for h in payload["headers"] if pedidas is None or h["name"].lower() in pedidas],
            "body": {"size": 0},
        },
    }


def _respuesta(estado: int, cuerpo: Any) -> RespuestaHttp:
    return RespuestaHttp(estado=estado, cuerpo=json.dumps(cuerpo).encode("utf-8"))


@dataclass
class GoogleFalso:
    """Fake Google: OAuth token/revoke endpoints and the Gmail read endpoints, in memory."""

    mensajes: list[dict[str, Any]] = field(default_factory=list)
    history_id: int = 5000
    alcance: str = SCOPE_LECTURA
    con_refresco: bool = True
    historial_caducado: bool = False
    refresco_revocado: bool = False
    cuenta: str = "yo@gmail.com"
    llamadas: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    revocados: list[str] = field(default_factory=list)
    _historial: list[tuple[int, str]] = field(default_factory=list)
    codigos: dict[str, str] = field(default_factory=dict)

    def llega(self, mensaje: dict[str, Any]) -> None:
        """A new message arrives: Gmail records it in the history."""
        self.history_id += 7
        self.mensajes.insert(0, mensaje)
        self._historial.append((self.history_id, mensaje["id"]))

    def rutas(self) -> list[str]:
        return [urlsplit(u).path for _m, u, _d in self.llamadas]

    def completos(self) -> list[str]:
        return [u.rsplit("/", 1)[-1] for _m, u, d in self.llamadas if d.get("params", {}).get("format") == "full"]

    def __call__(
        self,
        metodo: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        datos: Mapping[str, str] | None = None,
        cabeceras: Mapping[str, str] | None = None,
    ) -> RespuestaHttp:
        self.llamadas.append((metodo, url, {"params": dict(params or {}), "datos": dict(datos or {})}))
        ruta = urlsplit(url).path
        if ruta.endswith("/token"):
            return self._token(dict(datos or {}))
        if ruta.endswith("/revoke"):
            self.revocados.append(str((datos or {}).get("token")))
            return _respuesta(200, {})
        if (cabeceras or {}).get("Authorization") != f"Bearer {ACCESO}":
            return _respuesta(401, {"error": {"code": 401}})
        params = dict(params or {})
        if ruta.endswith("/users/me/profile"):
            return _respuesta(
                200,
                {"emailAddress": self.cuenta, "messagesTotal": len(self.mensajes), "historyId": str(self.history_id)},
            )
        if ruta.endswith("/users/me/history"):
            if self.historial_caducado:
                return _respuesta(404, {"error": {"code": 404, "message": "Requested entity was not found."}})
            desde = int(params["startHistoryId"])
            nuevos = [(h, i) for h, i in self._historial if h > desde]
            por_id = {m["id"]: m for m in self.mensajes}
            historia = [
                {
                    "id": str(h),
                    "messages": [{"id": i, "threadId": por_id[i]["threadId"]}],
                    "messagesAdded": [
                        {"message": {"id": i, "threadId": por_id[i]["threadId"], "labelIds": por_id[i]["labelIds"]}}
                    ],
                }
                for h, i in nuevos
            ]
            cuerpo: dict[str, Any] = {"historyId": str(self.history_id)}
            if historia:
                cuerpo["history"] = historia
            return _respuesta(200, cuerpo)
        if ruta.endswith("/users/me/messages"):
            visibles = [m for m in self.mensajes if not {"SPAM", "TRASH"} & set(m["labelIds"])]
            inicio = int(params.get("pageToken") or 0)
            tam = int(params.get("maxResults") or 100)
            pagina = visibles[inicio : inicio + tam]
            cuerpo = {
                "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in pagina],
                "resultSizeEstimate": len(visibles),
            }
            if inicio + tam < len(visibles):
                cuerpo["nextPageToken"] = str(inicio + tam)
            return _respuesta(200, cuerpo)
        if "/users/me/messages/" in ruta:
            mensaje_id = ruta.rsplit("/", 1)[-1]
            mensaje = next((m for m in self.mensajes if m["id"] == mensaje_id), None)
            if mensaje is None:
                return _respuesta(404, {"error": {"code": 404}})
            if params.get("format") == "metadata":
                pedidas = params.get("metadataHeaders")
                return _respuesta(200, metadatos(mensaje, pedidas if isinstance(pedidas, list) else None))
            return _respuesta(200, mensaje)
        return _respuesta(404, {"error": "ruta desconocida"})

    def _token(self, datos: dict[str, Any]) -> RespuestaHttp:
        if datos.get("grant_type") == "authorization_code":
            if datos.get("code") not in self.codigos or not datos.get("code_verifier"):
                return _respuesta(400, {"error": "invalid_grant"})
            import hashlib

            reto = (
                base64.urlsafe_b64encode(hashlib.sha256(datos["code_verifier"].encode()).digest()).decode().rstrip("=")
            )
            if reto != self.codigos[datos["code"]]:
                return _respuesta(400, {"error": "invalid_grant", "error_description": "PKCE"})
            cuerpo = {"access_token": ACCESO, "expires_in": 3599, "scope": self.alcance, "token_type": "Bearer"}
            if self.con_refresco:
                cuerpo["refresh_token"] = REFRESCO
            return _respuesta(200, cuerpo)
        if datos.get("grant_type") == "refresh_token":
            if self.refresco_revocado or datos.get("refresh_token") != REFRESCO:
                return _respuesta(
                    400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
                )
            return _respuesta(
                200, {"access_token": ACCESO, "expires_in": 3599, "scope": self.alcance, "token_type": "Bearer"}
            )
        return _respuesta(400, {"error": "unsupported_grant_type"})

    def autorizar(self, url_consentimiento: str, codigo: str = "codigo-1") -> tuple[str, str]:
        """The user accepts on the consent screen: returns (state, code) as Google would redirect them."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(url_consentimiento).query).items()}
        self.codigos[codigo] = params["code_challenge"]
        return params["state"], codigo


def ahora() -> datetime:
    return datetime.now(UTC)

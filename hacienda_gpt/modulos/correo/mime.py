"""A Gmail message resource (``users.messages.get``, ``format=full`` or ``metadata``) -> what we read of it.

Everything here runs in memory and the result is discarded after classification: only the derived item
(``almacen.Elemento``) is stored. Text/plain is preferred; HTML is reduced to its VISIBLE text (scripts,
styles and hidden elements dropped, and their presence recorded as a signal), and its links are kept as
(visible text, target host) pairs for the phishing checks. Attachment names are read only to flag
dangerous types; they are never stored nor shown to a model.

Gmail API format (documented): ``payload`` is a MessagePart ``{mimeType, filename, headers: [{name,
value}], body: {size, data?, attachmentId?}, parts: [...]}``; ``body.data`` is base64url; ``internalDate``
is epoch milliseconds as a string.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.header import decode_header, make_header
from email.utils import getaddresses
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from hacienda_gpt.modulos.correo.dominios import host_valido

ZONA = ZoneInfo("Europe/Madrid")
MAX_TEXTO = 200_000  # characters of visible text read per message
MAX_PARTES = 60
_INVISIBLES = re.compile("[­​‌‍⁠﻿‪-‮⁦-⁩]")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_OMITIR = frozenset({"script", "style", "head", "title", "noscript", "template", "svg", "math"})
_BLOQUES = frozenset(
    {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "hr", "td"}
)
_VACIOS = frozenset({"br", "img", "hr", "meta", "link", "input", "area", "base", "col", "source", "wbr"})
_OCULTO_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])|opacity\s*:\s*0(?![.\d]*[1-9])"
    r"|max-height\s*:\s*0(?![.\d]*[1-9])|(?<![-\w])height\s*:\s*0(?![.\d]*[1-9])|mso-hide\s*:\s*all",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Enlace:
    texto: str
    host: str | None
    esquema: str


@dataclass(frozen=True)
class Adjunto:
    nombre: str
    tipo: str


@dataclass
class Mensaje:
    id: str
    thread_id: str
    momento: datetime
    etiquetas: frozenset[str]
    remitente_nombre: str
    remitente_direccion: str
    remitente_dominio: str
    responder_a_dominio: str | None
    asunto: str
    cabeceras: dict[str, list[str]]
    texto: str = ""
    enlaces: list[Enlace] = field(default_factory=list)
    texto_oculto: str = ""
    adjuntos: list[Adjunto] = field(default_factory=list)
    solo_metadatos: bool = False

    @property
    def fecha(self) -> date:
        return self.momento.astimezone(ZONA).date()

    def cabecera(self, nombre: str) -> str | None:
        valores = self.cabeceras.get(nombre.lower())
        return valores[0] if valores else None


class _Html(HTMLParser):
    """Visible text and links of an HTML body; hidden text collected apart (never shown)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.ocultas: list[str] = []
        self.enlaces: list[tuple[str, str]] = []
        self._pila: list[tuple[str, bool, bool]] = []  # (tag, hidden, skipped)
        self._enlace: tuple[str, list[str]] | None = None

    def _oculto(self) -> bool:
        return any(oculto for _t, oculto, _s in self._pila)

    def _omitido(self) -> bool:
        return any(omitido for _t, _o, omitido in self._pila)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = {k.lower(): (v or "") for k, v in attrs}
        if tag in _BLOQUES:
            self.partes.append("\n")
        if tag == "a" and "href" in atributos and not self._omitido():
            self._enlace = (atributos["href"], [])
        if tag in _VACIOS:
            return
        oculto = (
            "hidden" in atributos
            or atributos.get("aria-hidden", "").lower() == "true"
            or bool(_OCULTO_RE.search(atributos.get("style", "")))
        )
        self._pila.append((tag, oculto, tag in _OMITIR))
        if len(self._pila) > 500:  # pathological nesting: stop tracking, keep reading text as hidden
            self._pila = self._pila[-500:]

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._enlace is not None:
            href, texto = self._enlace
            self.enlaces.append((href, " ".join("".join(texto).split())))
            self._enlace = None
        for i in range(len(self._pila) - 1, -1, -1):
            if self._pila[i][0] == tag:
                del self._pila[i:]
                break
        if tag in _BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if self._omitido():
            return
        if self._oculto():
            if data.strip():
                self.ocultas.append(data)
            return
        self.partes.append(data)
        if self._enlace is not None:
            self._enlace[1].append(data)


def _decodificar_cabecera(valor: str) -> str:
    try:
        return str(make_header(decode_header(valor)))
    except (UnicodeError, LookupError, binascii.Error, ValueError):
        return valor


def _b64url(dato: str) -> bytes:
    relleno = "=" * (-len(dato) % 4)
    try:
        return base64.urlsafe_b64decode(dato + relleno)
    except (binascii.Error, ValueError):
        return b""


def _charset(cabeceras: Mapping[str, list[str]]) -> str:
    tipo = (cabeceras.get("content-type") or [""])[0]
    encontrado = re.search(r"charset=\"?([A-Za-z0-9_\-]+)", tipo)
    return encontrado.group(1) if encontrado else "utf-8"


def _texto_de_bytes(datos: bytes, charset: str) -> str:
    try:
        return datos.decode(charset, errors="replace")
    except LookupError:
        return datos.decode("utf-8", errors="replace")


def _cabeceras(parte: Mapping[str, Any]) -> dict[str, list[str]]:
    salida: dict[str, list[str]] = {}
    for cabecera in parte.get("headers") or []:
        if isinstance(cabecera, Mapping) and isinstance(cabecera.get("name"), str):
            salida.setdefault(cabecera["name"].lower(), []).append(str(cabecera.get("value") or ""))
    return salida


def _limpiar(texto: str) -> str:
    texto = _INVISIBLES.sub("", texto).replace("\r", "")
    lineas = [" ".join(linea.split()) for linea in texto.split("\n")]
    salida: list[str] = []
    for linea in lineas:
        if linea or (salida and salida[-1]):
            salida.append(linea)
    return "\n".join(salida).strip()[:MAX_TEXTO]


def html_a_texto(html: str) -> tuple[str, list[tuple[str, str]], str]:
    """(visible text, links as (href, visible text), hidden text) of an HTML body."""
    lector = _Html()
    try:
        lector.feed(html[: MAX_TEXTO * 3])
        lector.close()
    except Exception:  # noqa: BLE001 - malformed HTML must never break a sync; what was read is kept
        pass
    return _limpiar("".join(lector.partes)), lector.enlaces, _limpiar(" ".join(lector.ocultas))


def _enlace(href: str, texto: str) -> Enlace | None:
    href = href.strip()
    try:
        partes = urlsplit(href)
    except ValueError:
        return None
    esquema = partes.scheme.lower()
    if esquema not in ("http", "https"):
        return None if esquema in ("mailto", "tel", "cid", "") else Enlace(texto=texto[:200], host=None, esquema=esquema)
    return Enlace(texto=texto[:200], host=host_valido(partes.hostname or ""), esquema=esquema)


def _recorrer(parte: Mapping[str, Any], planos: list[str], htmls: list[str], adjuntos: list[Adjunto], n: list[int]) -> None:
    n[0] += 1
    if n[0] > MAX_PARTES:
        return
    tipo = str(parte.get("mimeType") or "").lower()
    nombre = str(parte.get("filename") or "")
    cuerpo = parte.get("body") or {}
    if nombre:
        adjuntos.append(Adjunto(nombre=nombre[:200], tipo=tipo))
    elif tipo in ("text/plain", "text/html") and isinstance(cuerpo.get("data"), str):
        texto = _texto_de_bytes(_b64url(cuerpo["data"]), _charset(_cabeceras(parte)))
        (planos if tipo == "text/plain" else htmls).append(texto)
    for hija in parte.get("parts") or []:
        if isinstance(hija, Mapping):
            _recorrer(hija, planos, htmls, adjuntos, n)


def _direccion(valor: str) -> tuple[str, str]:
    pares = getaddresses([valor])
    nombre, direccion = pares[0] if pares else ("", "")
    return _decodificar_cabecera(nombre).strip().strip('"'), direccion.strip().lower()


def _dominio(direccion: str) -> str:
    host = direccion.rsplit("@", 1)[-1] if "@" in direccion else ""
    return host_valido(host) or ""


def leer_mensaje(recurso: Mapping[str, Any], *, solo_metadatos: bool = False) -> Mensaje:
    """Parse a Gmail message resource (``solo_metadatos``: it was fetched with ``format=metadata``).

    Raises ValueError when it has no valid id.
    """
    mensaje_id = recurso.get("id")
    if not isinstance(mensaje_id, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", mensaje_id):
        raise ValueError("mensaje sin identificador válido")
    payload = recurso.get("payload") or {}
    cabeceras = _cabeceras(payload)
    try:
        momento = datetime.fromtimestamp(int(str(recurso.get("internalDate") or "0")) / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        momento = datetime.fromtimestamp(0, tz=UTC)
    nombre, direccion = _direccion((cabeceras.get("from") or [""])[0])
    responder = cabeceras.get("reply-to")
    responder_dominio = _dominio(_direccion(responder[0])[1]) if responder else None
    planos: list[str] = []
    htmls: list[str] = []
    adjuntos: list[Adjunto] = []
    _recorrer(payload, planos, htmls, adjuntos, [0])
    enlaces: list[Enlace] = []
    oculto = ""
    texto_html = ""
    for html in htmls:
        visible, pares, escondido = html_a_texto(html)
        texto_html += ("\n" if texto_html else "") + visible
        oculto += escondido
        enlaces.extend(e for href, txt in pares if (e := _enlace(href, txt)) is not None)
    texto = _limpiar("\n".join(planos)) if planos else texto_html
    if planos:
        enlaces.extend(e for url in _URL_RE.findall(texto) if (e := _enlace(url, url)) is not None)
    return Mensaje(
        id=mensaje_id,
        thread_id=str(recurso.get("threadId") or mensaje_id)[:64],
        momento=momento,
        etiquetas=frozenset(str(e) for e in recurso.get("labelIds") or []),
        remitente_nombre=nombre[:200],
        remitente_direccion=direccion[:320],
        remitente_dominio=_dominio(direccion),
        responder_a_dominio=responder_dominio,
        asunto=_limpiar(_decodificar_cabecera((cabeceras.get("subject") or [""])[0]))[:500],
        cabeceras=cabeceras,
        texto=texto,
        enlaces=enlaces[:300],
        texto_oculto=oculto[:5000],
        adjuntos=adjuntos[:50],
        solo_metadatos=solo_metadatos,
    )


__all__ = ["Adjunto", "Enlace", "Mensaje", "html_a_texto", "leer_mensaje"]

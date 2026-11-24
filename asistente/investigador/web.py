"""The searchers' tool: search and read the web. Default = the existing research capability's web searcher.

``asistente.capacidades.investigacion.web`` already searches and reads (server-side web search + fetch, domain
allow / block lists). The army reuses it through :class:`WebDeCapacidad`; tests pass a fake ``HerramientaWeb``.

The server-side fetch cannot open many official pages (the AEAT's sede answers ``url_not_accessible``), which
left whole subquestions without a single source. Search results that were found but not read are then read
locally by :class:`LectorLocal`: public http(s) only, private addresses refused (also after redirects), HTML
only, size and time capped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
import logging
import socket
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_BYTES = 3_000_000
TIMEOUT_S = 15
MAX_REDIRECCIONES = 3
_AGENTE = "Mozilla/5.0 (compatible; asistente-investigador/1.0)"


@dataclass(frozen=True)
class Documento:
    url: str
    titulo: str
    texto: str
    recuperado_el: datetime | None = None


class HerramientaWeb(Protocol):
    def buscar_y_leer(self, *, pregunta: str, consultas: Sequence[str], max_lecturas: int) -> list[Documento]: ...


def _ip_publica(url: str) -> str | None:
    """The one address the reader will connect to for ``url``: resolved ONCE, and only when every address of
    the name is globally routable (``is_global``: no private, loopback, link-local, CGNAT 100.64/10, reserved,
    documentation or IPv4-mapped tricks). None otherwise. Connecting to this very address (not resolving the
    name again) closes the DNS-rebinding window between the check and the connection."""
    partes = urlparse(url)
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        return None
    try:
        direcciones = [str(info[4][0]) for info in socket.getaddrinfo(partes.hostname, partes.port or None)]
    except OSError, UnicodeError, ValueError:
        return None
    elegida: str | None = None
    for d in direcciones:
        try:
            ip = ipaddress.ip_address(d.split("%")[0])
        except ValueError:
            return None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return None
        elegida = elegida or str(ip)
    return elegida


def _host_publico(url: str) -> bool:
    return _ip_publica(url) is not None


class _RespuestaFijada:
    """The slice of a response the reader uses (status, headers, the body in chunks), over a streamed ``httpx``
    response. ``close`` releases the connection; the body iterator also closes it when it ends."""

    def __init__(self, cliente: httpx.Client, respuesta: httpx.Response):
        self._cliente = cliente
        self._r = respuesta
        self.status_code = respuesta.status_code
        self.headers = respuesta.headers

    def iter_content(self, tamano: int) -> Iterator[bytes]:
        try:
            yield from self._r.iter_bytes(tamano)
        finally:
            self.close()

    def close(self) -> None:
        self._r.close()
        self._cliente.close()


def _obtener_fijado(url: str, *, ip: str, timeout: float, headers: dict[str, str], **_: Any) -> _RespuestaFijada:
    """GET ``url`` connecting to ``ip`` (TLS still verified against the URL's host name, sent as SNI).

    The request goes to the already-checked address, never to a second resolution of the name: that closes the
    DNS-rebinding window. ``Host`` carries the original name and ``sni_hostname`` makes the TLS handshake (SNI and
    certificate check) use it too. No redirects (the reader re-checks every hop) and no retries.
    """
    partes = urlparse(url)
    host = partes.hostname or ""
    destino = f"[{ip}]" if ":" in ip else ip
    puerto = f":{partes.port}" if partes.port else ""
    ruta = (partes.path or "/") + (f"?{partes.query}" if partes.query else "")
    cabeceras = {**headers, "Host": partes.netloc.rpartition("@")[2]}
    cliente = httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False)
    try:
        peticion = cliente.build_request(
            "GET",
            f"{partes.scheme}://{destino}{puerto}{ruta}",
            headers=cabeceras,
            extensions={"sni_hostname": host},
        )
        return _RespuestaFijada(cliente, cliente.send(peticion, stream=True))
    except BaseException:
        cliente.close()
        raise


def texto_de_html(html: str) -> tuple[str, str]:
    """(title, visible text) of an HTML page, without scripts, styles and navigation."""
    import lxml.html

    raiz = lxml.html.fromstring(html)
    for nodo in raiz.xpath("//script|//style|//noscript|//nav|//header|//footer|//form|//svg"):
        nodo.drop_tree()
    titulo = (raiz.findtext(".//title") or "").strip()
    lineas = (linea.strip() for linea in raiz.text_content().splitlines())
    return titulo, "\n".join(linea for linea in lineas if linea)


def _decodificar(cuerpo: bytes, content_type: str) -> str:
    """UTF-8 first (most pages, often without a declared charset: HTTP's ISO-8859-1 default garbled «Exención»
    into «ExenciÃ³n»), then the declared charset, then Latin-1."""
    try:
        return cuerpo.decode("utf-8")
    except UnicodeDecodeError:
        pass
    declarado = content_type.lower().partition("charset=")[2].split(";")[0].strip()
    if declarado:
        try:
            return cuerpo.decode(declarado, errors="replace")
        except LookupError:
            pass
    return cuerpo.decode("latin-1", errors="replace")


class LectorLocal:
    """Reads one public HTML page; None when it is not public, not HTML, too big or unreachable."""

    def __init__(self, obtener: Callable[..., Any] | None = None):
        self._obtener = obtener or _obtener_fijado

    def leer(self, url: str, titulo: str = "") -> Documento | None:
        actual = url
        for _ in range(MAX_REDIRECCIONES + 1):
            ip = _ip_publica(actual)  # every hop, redirects included, is checked again
            if ip is None:
                return None
            try:
                r = self._obtener(
                    actual,
                    ip=ip,
                    timeout=TIMEOUT_S,
                    allow_redirects=False,
                    stream=True,
                    headers={"User-Agent": _AGENTE, "Accept": "text/html"},
                )
            except Exception as exc:  # network errors: the page is simply not read
                logger.info("local read of %s failed: %s", actual, type(exc).__name__)
                return None
            try:
                resultado = self._procesar(actual, titulo, r)
            finally:
                cerrar = getattr(r, "close", None)
                if callable(cerrar):
                    cerrar()
            if isinstance(resultado, str):  # a redirect to follow
                actual = resultado
                continue
            return resultado
        return None

    def _procesar(self, actual: str, titulo: str, r: Any) -> Documento | str | None:
        """One answer: the next URL of a redirect, the document, or None."""
        if r.status_code in {301, 302, 303, 307, 308}:
            destino = r.headers.get("Location")
            if not destino:
                return None
            siguiente = urljoin(actual, destino)
            if urlparse(siguiente).scheme not in {"http", "https"}:
                return None
            return siguiente
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "").lower():
            return None
        cuerpo = b""
        try:
            for trozo in r.iter_content(65536):
                cuerpo += trozo
                if len(cuerpo) > MAX_BYTES:
                    return None
        except Exception as exc:  # the connection dropped mid-body: the page is simply not read
            logger.info("local read of %s failed: %s", actual, type(exc).__name__)
            return None
        try:
            propio, texto = texto_de_html(_decodificar(cuerpo, r.headers.get("Content-Type", "")))
        except Exception:
            return None
        if not texto.strip():
            return None
        return Documento(actual, propio or titulo or actual, texto, datetime.now(UTC))


class WebDeCapacidad:
    """Adapter over ``asistente.capacidades.investigacion.web.BuscadorWeb`` (``web_para(proveedor)``)."""

    def __init__(
        self,
        buscador: Any,
        *,
        permitidos: Sequence[str] | None = None,
        bloqueados: Sequence[str] = (),
        lector: LectorLocal | None = None,
    ):
        self.buscador = buscador
        self.permitidos = permitidos
        self.bloqueados = tuple(bloqueados)
        self.lector = lector

    def buscar_y_leer(self, *, pregunta: str, consultas: Sequence[str], max_lecturas: int) -> list[Documento]:
        r = self.buscador.buscar(
            pregunta=pregunta,
            consultas=list(consultas),
            permitidos=self.permitidos,
            bloqueados=self.bloqueados,
            max_busquedas=max(1, len(consultas)),
            max_lecturas=max_lecturas,
        )
        docs = [Documento(d.url, d.titulo, d.texto, d.recuperado_el) for d in r.documentos]
        if getattr(r, "errores", None):
            logger.info("web search for %r: %s", pregunta[:80], "; ".join(r.errores))
        if self.lector is not None:
            leidas = {d.url for d in docs}
            # Found by the search but not read (vistos is already filtered by the domain lists).
            for url, titulo in getattr(r, "vistos", []):
                if len(docs) >= max_lecturas:
                    break
                if url in leidas:
                    continue
                doc = self.lector.leer(url, titulo)
                if doc is not None:
                    docs.append(doc)
                    leidas.add(url)
        return docs


def web_por_defecto(proveedor: Any) -> HerramientaWeb:
    from asistente.capacidades.investigacion.web import web_para

    return WebDeCapacidad(web_para(proveedor), lector=LectorLocal())

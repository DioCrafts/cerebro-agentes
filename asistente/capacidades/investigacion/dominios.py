"""Host names: which sources are official, and the allow / block lists of the web search.

«Official» is a LABEL shown next to each source (a page of a public administration), decided here by host name
only — never by what the page says about itself. The default allow-list is the same set: the central State's
``*.gob.es`` sites, the BOE, the Tax Agency, the Social Security, the DGT, and the regional and EU portals listed
below. Operators extend or replace it with ``INVESTIGACION_WEB_PERMITIDOS``.

A list entry is a plain host name (no scheme, path, port or wildcard): it covers the host and its sub-domains,
as the provider's ``allowed_domains`` / ``blocked_domains`` do.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import re
from urllib.parse import urlsplit

# Public administrations (hosts and parents). Sub-domains included: sede.agenciatributaria.gob.es, …
OFICIALES_POR_DEFECTO: tuple[str, ...] = (
    "boe.es",
    "gob.es",
    "agenciatributaria.es",
    "agenciatributaria.gob.es",
    "seg-social.es",
    "seg-social.gob.es",
    "dgt.es",
    "administracion.gob.es",
    "comunidad.madrid",
    "madrid.es",
    "juntadeandalucia.es",
    "gencat.cat",
    "euskadi.eus",
    "xunta.gal",
    "gva.es",
    "jcyl.es",
    "castillalamancha.es",
    "aragon.es",
    "carm.es",
    "navarra.es",
    "larioja.org",
    "cantabria.es",
    "asturias.es",
    "caib.es",
    "gobiernodecanarias.org",
    "juntaex.es",
    "europa.eu",
)

_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")


def host_de(url: str | None) -> str | None:
    """The lower-case host of ``url`` (without ``www.``), or None when it is not an http(s) URL."""
    if not url:
        return None
    try:
        partes = urlsplit(url.strip())
    except ValueError:
        return None
    if partes.scheme not in ("http", "https") or not partes.hostname:
        return None
    host = partes.hostname.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def cubre(dominio: str, host: str) -> bool:
    """Does list entry ``dominio`` cover ``host`` (itself or a sub-domain)?"""
    return host == dominio or host.endswith("." + dominio)


def en_lista(host: str | None, lista: Iterable[str]) -> bool:
    return host is not None and any(cubre(d, host) for d in lista)


def es_oficial(url: str | None, oficiales: Sequence[str] = OFICIALES_POR_DEFECTO) -> bool:
    return en_lista(host_de(url), oficiales)


def normalizar_lista_dominios(texto: str, nombre: str = "lista de dominios") -> tuple[str, ...]:
    """``"a.es, www.b.gob.es"`` -> ``("a.es", "b.gob.es")``. Raises ValueError on an entry that is not a host."""
    salida: list[str] = []
    for parte in texto.split(","):
        entrada = parte.strip().lower().rstrip(".")
        if not entrada:
            continue
        if entrada.startswith("www."):
            entrada = entrada[4:]
        if not _HOST_RE.match(entrada):
            raise ValueError(
                f"{nombre}: {parte.strip()!r} no es un nombre de host (sin esquema, ruta, puerto ni comodines)."
            )
        if entrada not in salida:
            salida.append(entrada)
    return tuple(salida)


def permitido(url: str | None, *, permitidos: Sequence[str] | None, bloqueados: Sequence[str]) -> bool:
    """May a page at ``url`` be used? Blocked hosts never; with an allow-list, only hosts on it."""
    host = host_de(url)
    if host is None or en_lista(host, bloqueados):
        return False
    return permitidos is None or en_lista(host, permitidos)


__all__ = [
    "OFICIALES_POR_DEFECTO",
    "cubre",
    "en_lista",
    "es_oficial",
    "host_de",
    "normalizar_lista_dominios",
    "permitido",
]

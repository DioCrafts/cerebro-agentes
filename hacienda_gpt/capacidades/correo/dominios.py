"""Domains: the registrable part of a host, official domains of the brands people impersonate, lookalikes.

Everything here is deterministic string work on hosts taken from headers and links; nothing is resolved
or fetched. The brand list (``data/remitentes.json``) is operator configuration, not law: see its note.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import unicodedata

DATOS = Path(__file__).resolve().parent / "data" / "remitentes.json"

# Two-label public suffixes we may meet in Spanish mail (enough for registrable-domain purposes here).
_SUFIJOS_DOBLES = frozenset(
    {
        "gob.es",
        "com.es",
        "org.es",
        "nom.es",
        "edu.es",
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.mx",
        "com.ar",
        "com.br",
        "com.pt",
        "co.jp",
        "com.au",
    }
)
SUFIJO_SEDE_ESTATAL = "gob.es"

# Characters that look like Latin letters (a short, explicit list: enough for the usual tricks).
_HOMOGLIFOS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
        "ɡ": "g",
        "0": "o",
        "1": "l",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "$": "s",
        "@": "a",
    }
)
_HOST_RE = re.compile(r"^[a-z0-9.\-]{1,253}$")


@dataclass(frozen=True)
class Marca:
    clave: str
    nombre: str
    tipo: str  # administracion | banco | pagos | mensajeria
    area: str  # module the matter belongs to ("" none)
    nombres: tuple[str, ...]
    fichas: tuple[str, ...]
    dominios: frozenset[str]

    @property
    def es_administracion(self) -> bool:
        return self.tipo == "administracion"


@dataclass(frozen=True)
class Remitentes:
    marcas: dict[str, Marca]
    correo_gratuito: frozenset[str]
    comercializadoras_luz: tuple[str, ...]


def _extra_del_entorno() -> dict[str, set[str]]:
    """``CORREO_DOMINIOS_OFICIALES_EXTRA="bbva=bbva.net,bbva.info;aeat=…"`` (operator additions)."""
    extra: dict[str, set[str]] = {}
    for parte in os.environ.get("CORREO_DOMINIOS_OFICIALES_EXTRA", "").split(";"):
        if "=" not in parte:
            continue
        clave, dominios = parte.split("=", 1)
        extra.setdefault(clave.strip().lower(), set()).update(
            d.strip().lower() for d in dominios.split(",") if d.strip()
        )
    return extra


@lru_cache(maxsize=1)
def _crudo() -> dict:  # type: ignore[type-arg]
    datos: dict = json.loads(DATOS.read_text(encoding="utf-8"))  # type: ignore[type-arg]
    return datos


def cargar() -> Remitentes:
    crudo = _crudo()
    extra = _extra_del_entorno()
    marcas = {
        clave: Marca(
            clave=clave,
            nombre=m["nombre"],
            tipo=m["tipo"],
            area=m.get("area", ""),
            nombres=tuple(normalizar(n) for n in m["nombres"]),
            fichas=tuple(m["fichas"]),
            dominios=frozenset(d.lower() for d in m["dominios"]) | frozenset(extra.get(clave, ())),
        )
        for clave, m in crudo["marcas"].items()
    }
    return Remitentes(
        marcas=marcas,
        correo_gratuito=frozenset(crudo["correo_gratuito"]),
        comercializadoras_luz=tuple(normalizar(c) for c in crudo["comercializadoras_luz"]),
    )


def normalizar(texto: str) -> str:
    """Lower case, no accents, single spaces (to match names and wording)."""
    sin = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return " ".join(sin.split())


def host_valido(host: str) -> str | None:
    """Lower-cased ASCII host (IDNs as punycode), or None when it is not a host."""
    host = host.strip().strip(".").lower()
    if not host:
        return None
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        return None
    return ascii_host if _HOST_RE.match(ascii_host) and "." in ascii_host else None


def registrable(host: str) -> str:
    """The registrable domain: ``sede.agenciatributaria.gob.es`` -> ``agenciatributaria.gob.es``."""
    partes = host.lower().strip(".").split(".")
    if len(partes) >= 3 and ".".join(partes[-2:]) in _SUFIJOS_DOBLES:
        return ".".join(partes[-3:])
    return ".".join(partes[-2:])


def etiqueta(host: str) -> str:
    """The registrable label without its suffix (``agenciatributaria`` for ``agenciatributaria.gob.es``)."""
    return registrable(host).split(".")[0]


def es_sede_estatal(host: str) -> bool:
    """Under «.gob.es» (state e-offices must be, art. 18.2 RD 203/2021)."""
    reg = registrable(host)
    return reg.endswith("." + SUFIJO_SEDE_ESTATAL)


def _visual(texto: str) -> str:
    """What a label looks like: punycode decoded, homoglyphs and digits mapped, hyphens dropped."""
    try:
        texto = texto.encode("ascii").decode("idna") if texto.startswith("xn--") else texto
    except UnicodeError:
        pass
    texto = texto.translate(_HOMOGLIFOS)
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", texto.lower())


def _distancia(a: str, b: str) -> int:
    """Levenshtein distance (short strings)."""
    if a == b:
        return 0
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


def es_oficial(host: str, marca: Marca) -> bool:
    reg = registrable(host)
    if reg in marca.dominios:
        return True
    return marca.es_administracion and es_sede_estatal(host)


def marca_oficial_de(host: str, remitentes: Remitentes) -> Marca | None:
    """The brand ``host`` officially belongs to, if any."""
    for marca in remitentes.marcas.values():
        if es_oficial(host, marca):
            return marca
    return None


def parecido_a(host: str, remitentes: Remitentes) -> Marca | None:
    """The brand ``host`` imitates without being one of its domains (lookalike), or None.

    Caught: the brand inside a longer label (``bbva-avisos.com``, ``agenciatributaria-es.net``), a label one
    or two edits away (``santnader.es``), homoglyphs and digits (``bbv4.es``, punycode), and the brand as a
    subdomain of an unrelated domain (``santander.es.acceso-seguro.com``). A label that IS the brand token on
    another suffix (``bbva.net``) is left alone unless it is an administration (they live under «.gob.es» or
    their listed domains).
    """
    reg = registrable(host)
    etiqueta_reg = reg.split(".")[0]
    visual = _visual(etiqueta_reg)
    subdominios = [_visual(p) for p in host.lower().split(".")[: -len(reg.split("."))]]
    for marca in remitentes.marcas.values():
        if es_oficial(host, marca):
            continue
        for ficha in marca.fichas:
            ficha_v = _visual(ficha)
            if len(ficha_v) < 3:
                continue
            if etiqueta_reg == ficha and not marca.es_administracion:
                continue  # e.g. bbva.net: plausibly theirs; not a lookalike by itself
            if visual == ficha_v and (etiqueta_reg != ficha or marca.es_administracion):
                return marca
            if len(ficha_v) >= 4 and ficha_v in visual:
                return marca
            umbral = 2 if len(ficha_v) >= 8 else 1 if len(ficha_v) >= 5 else 0
            if umbral and _distancia(visual, ficha_v) <= umbral:
                return marca
            if any(s == ficha_v or (len(ficha_v) >= 5 and ficha_v in s) for s in subdominios):
                return marca
    return None


def marca_nombrada(texto: str, remitentes: Remitentes) -> Marca | None:
    """The brand a display name or a text names («Agencia Tributaria», «BBVA»…), longest name first."""
    relleno = f" {' '.join(re.sub(r'[^a-z0-9@ ]+', ' ', normalizar(texto)).split())} "
    candidatos = sorted(
        ((n, m) for m in remitentes.marcas.values() for n in m.nombres),
        key=lambda par: -len(par[0]),
    )
    for nombre, marca in candidatos:
        limpio = " ".join(re.sub(r"[^a-z0-9@ ]+", " ", nombre).split())
        if limpio and f" {limpio} " in relleno:
            return marca
    return None


def es_correo_gratuito(dominio: str, remitentes: Remitentes) -> bool:
    return registrable(dominio) in remitentes.correo_gratuito


__all__ = [
    "Marca",
    "Remitentes",
    "cargar",
    "es_correo_gratuito",
    "es_oficial",
    "es_sede_estatal",
    "etiqueta",
    "host_valido",
    "marca_nombrada",
    "marca_oficial_de",
    "normalizar",
    "parecido_a",
    "registrable",
]

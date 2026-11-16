"""How the text of a law is read and normalised, for every check of legal data (no model here).

One normalisation for everything: the weekly re-verification of the tax parameters, the deterministic gate of the
re-encoding loop and the self-check of every household module compare quotes with :func:`comprimido` — NFKC, lower
case, without whitespace or quotation marks (the BOE's links and line breaks add stray spaces) — over the visible
text of the page (:func:`texto_plano`). Figures are looked up with :func:`formas`, the ways a Spanish law prints a
number.

Pages come from www.boe.es (:func:`url_de`) or from a folder of snapshots (:func:`leer_snapshot`).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
import html
from pathlib import Path
import re
import unicodedata

# ``texto_de(boe_id, version)``: the law's page (HTML), or None when it cannot be read.
TextoDe = Callable[[str, str | None], str | None]
# Not a version of a consolidated text: the norm as the daily BOE published it.
VERSION_DIARIO = "diario"
# Characters within which the figures of a scale must appear together.
VENTANA_TABLA = 6000


def texto_plano(pagina: str) -> str:
    """Visible text of a BOE page, lower-cased and with whitespace collapsed."""
    sin_codigo = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", pagina, flags=re.S | re.I)
    texto = html.unescape(re.sub(r"<[^>]+>", " ", sin_codigo))
    texto = unicodedata.normalize("NFKC", texto).replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip().lower()


def comprimido(texto: str) -> str:
    """``texto`` without whitespace or quotation marks, NFKC and lower case: what quotes are compared on."""
    return re.sub(r"[\s«»\"“”]", "", unicodedata.normalize("NFKC", texto).lower())


def texto_de_html(raw: str) -> str:
    """Plain text of a boe.es page, one paragraph per line (for extracts kept as text and for the reader)."""
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = " ".join(raw.split())  # line breaks in the source are not paragraph breaks
    raw = re.sub(r"(?i)</(p|div|h\d|li|tr|blockquote)>|<br\s*/?>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw).replace("\xa0", " ")
    return "\n".join(" ".join(line.split()) for line in raw.splitlines() if line.strip())


def formas(x: Decimal) -> set[str]:
    """Ways a Spanish law may print ``x``: 13362.22 → "13.362,22", "13 362,22", "13362,22"."""
    out: set[str] = set()
    q = x.normalize()
    for decimales in (0, 1, 2, 3):
        if q != q.quantize(Decimal(1).scaleb(-decimales)):
            continue
        entero, _, fraccion = f"{q:.{decimales}f}".partition(".")
        agrupado = f"{int(entero):,}"
        for forma in (entero, agrupado.replace(",", "."), agrupado.replace(",", " ")):
            out.add(forma + ("," + fraccion if fraccion else ""))
    return out


def aparece(texto: str, x: Decimal) -> bool:
    """Is ``x`` printed in ``texto`` as a whole number (a fraction below 1 also as a percentage)?"""
    if 0 < x < 1 and aparece(texto, x * 100):
        # A fraction stored as 0.15 is printed as "15 por ciento".
        return True
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d]|[.,]\d)", texto) for f in formas(x))


def juntas(texto: str, cifras: list[Decimal], ventana: int = VENTANA_TABLA) -> bool:
    """Do all the figures appear within one stretch of ``ventana`` characters?"""
    if not cifras:
        return True
    for forma in formas(cifras[0]):
        for match in re.finditer(r"(?<![\d.,])" + re.escape(forma) + r"(?![\d]|[.,]\d)", texto):
            tramo = texto[max(0, match.start() - ventana // 2) : match.start() + ventana]
            if all(aparece(tramo, c) for c in cifras):
                return True
    return False


def url_de(boe_id: str, version: str | None = None) -> str:
    """Page of a norm on boe.es: its current consolidated text, a superseded wording (AAAAMMDD) or the daily BOE."""
    if version == VERSION_DIARIO:
        return f"https://www.boe.es/diario_boe/txt.php?id={boe_id}"
    base = f"https://www.boe.es/buscar/act.php?id={boe_id}"
    return f"{base}&tn=1&p={version}" if version else base


def leer_snapshot(directorio: Path) -> TextoDe:
    """A ``texto_de`` that reads ``<boe_id>.html`` (or ``<boe_id>_p<version>.html``) under ``directorio``."""

    def texto_de(boe_id: str, version: str | None = None) -> str | None:
        nombre = f"{boe_id}_p{version}.html" if version else f"{boe_id}.html"
        for path in sorted(directorio.rglob(nombre)):
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    return texto_de


def textos_de_directorio(directorio: Path, patron: str = "BOE-*") -> dict[str, str]:
    """BOE id -> plain text, from ``<BOE-ID>.txt`` (extracts) or ``<BOE-ID>.html`` (pages from boe.es).

    ``<BOE-ID>_p<AAAAMMDD>`` files (a superseded wording) are keyed ``<BOE-ID>@<AAAAMMDD>``; an HTML page wins over
    an extract of the same norm.
    """
    textos: dict[str, str] = {}
    for sufijo, leer in ((".txt", lambda t: t), (".html", texto_de_html)):
        for path in sorted(directorio.glob(patron + sufijo)):
            boe_id, _, version = path.stem.partition("_p")
            clave = f"{boe_id}@{version}" if version else boe_id
            textos[clave] = leer(path.read_text(encoding="utf-8", errors="replace"))
    return textos


__all__ = [
    "VENTANA_TABLA",
    "VERSION_DIARIO",
    "TextoDe",
    "aparece",
    "comprimido",
    "formas",
    "juntas",
    "leer_snapshot",
    "texto_de_html",
    "texto_plano",
    "textos_de_directorio",
    "url_de",
]

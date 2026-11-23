"""Descarga los manuales prácticos de la AEAT (Renta, Sociedades, IVA, Patrimonio) al corpus.

Los manuales de la sede son HTML estático: un índice enorme con la tabla de
contenidos completa y una página corta por epígrafe. No hace falta Playwright
(el crawler ``web`` lo usa para toda la sede y además saca capturas), así que
este seed va con el cliente HTTP común (``asistente.plataforma.http``):

* lee ``robots.txt`` de la sede y no pide nada que prohíba;
* parte del índice de cada manual y sigue solo enlaces bajo su prefijo, en
  anchura y con un tope de páginas por manual;
* nunca baja PDFs (los manuales también se publican en un PDF de cientos de
  páginas que repetiría el contenido);
* espera ``--delay`` segundos entre peticiones y reanuda: lo que ya está en
  disco no se vuelve a pedir.

Cada página se guarda en ``<output-dir>/<snapshot>/aeat-manuales/<manual>/<ruta>.html``
con un sidecar ``.json`` (url, manual, ejercicio, autoridad) que el processor
añade a los metadatos de cada chunk.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import click
import httpx

from asistente.plataforma import http

logger = logging.getLogger(__name__)

SEDE = "https://sede.agenciatributaria.gob.es"
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]+)""", re.IGNORECASE)
_RAIZ_MANUALES = "/Sede/ayuda/manuales-videos-folletos/manuales-practicos/"


@dataclass(frozen=True)
class Manual:
    clave: str
    impuesto: str
    ejercicio: str
    indice: str  # ruta del índice (tabla de contenidos completa)
    prefijo: str  # solo se siguen enlaces cuya ruta empieza por aquí


MANUALES: dict[str, Manual] = {
    m.clave: m
    for m in (
        Manual("irpf-2025", "IRPF", "2025", "/Sede/Ayuda/25Manual/100.html", _RAIZ_MANUALES + "irpf-2025/"),
        Manual(
            "irpf-2025-deducciones-autonomicas",
            "IRPF",
            "2025",
            "/Sede/Ayuda/25Manual/100/deducciones-autonomicas.html",
            _RAIZ_MANUALES + "irpf-2025-deducciones-autonomicas/",
        ),
        Manual(
            "sociedades-2025",
            "Impuesto sobre Sociedades",
            "2025",
            "/Sede/Ayuda/25Manual/200.shtml",
            _RAIZ_MANUALES + "manual-sociedades-2025/",
        ),
        Manual("iva-2026", "IVA", "2026", "/Sede/ayuda/26manual/IVA.html", _RAIZ_MANUALES + "manual-iva-2026/"),
        Manual(
            "patrimonio-2025",
            "Impuesto sobre el Patrimonio",
            "2025",
            "/Sede/Ayuda/25Manual/714.shtml",
            _RAIZ_MANUALES + "patrimonio-2025/",
        ),
    )
}


def normalizar(url: str) -> str:
    """URL sin fragmento ni query, para no pedir dos veces la misma página."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def enlaces_del_manual(html: str, base: str, prefijo: str) -> list[str]:
    """Enlaces de ``html`` que caen bajo ``prefijo`` en la sede, sin PDFs, en orden y sin repetir."""
    vistos: dict[str, None] = {}
    for href in _HREF_RE.findall(html):
        url = normalizar(urljoin(base, href.strip()))
        parsed = urlparse(url)
        if parsed.netloc != urlparse(SEDE).netloc:
            continue
        if not parsed.path.lower().startswith(prefijo.lower()):
            continue
        if parsed.path.lower().endswith(".pdf"):
            continue
        vistos.setdefault(url, None)
    return list(vistos)


def ruta_local(raiz: Path, manual: Manual, url: str) -> Path:
    """Fichero de destino: la ruta bajo el prefijo, aplanada y acabada en ``.html``."""
    path = urlparse(url).path
    relativa = path[len(manual.prefijo) :] if path.lower().startswith(manual.prefijo.lower()) else path.lstrip("/")
    relativa = re.sub(r"\.s?html?$", "", relativa, flags=re.IGNORECASE).strip("/") or "indice"
    nombre = re.sub(r"[^A-Za-z0-9._-]+", "__", relativa.replace("/", "__"))[:180]
    return raiz / "aeat-manuales" / manual.clave / f"{nombre}.html"


def sidecar(manual: Manual, url: str) -> dict[str, str]:
    return {
        "url": url,
        "source_authority": "AEAT",
        "doc_type": "manual_practico",
        "manual": manual.clave,
        "impuesto": manual.impuesto,
        "ejercicio": manual.ejercicio,
    }


class Descargador:
    """Cliente HTTP común con robots.txt y pausa mínima entre peticiones a la sede."""

    def __init__(self, delay: float, cliente: http.Cliente | None = None) -> None:
        self.delay = delay
        self.cliente = cliente or http.cliente()
        self.robots = robotparser.RobotFileParser()
        self.peticiones = 0

    def cargar_robots(self) -> None:
        try:
            respuesta = self.cliente.get(f"{SEDE}/robots.txt", timeout=30, pausa_host=self.delay)
            lineas = respuesta.text.splitlines() if respuesta.is_success else []
        except httpx.HTTPError as exc:
            logger.warning("No se pudo leer robots.txt: %s", exc)
            lineas = []
        self.robots.parse(lineas)

    def permitido(self, url: str) -> bool:
        return self.robots.can_fetch(http.USER_AGENT, url)

    def get(self, url: str) -> str | None:
        self.peticiones += 1
        try:
            respuesta = self.cliente.get(url, timeout=30, pausa_host=self.delay)
        except httpx.HTTPError as exc:
            logger.warning("Fallo de red en %s: %s", url, exc)
            return None
        if respuesta.status_code != 200 or "html" not in respuesta.headers.get("Content-Type", "html"):
            logger.warning("HTTP %s en %s", respuesta.status_code, url)
            return None
        return respuesta.text


def descargar_manual(
    manual: Manual, raiz: Path, descargador: Descargador, max_paginas: int, dry_run: bool = False
) -> dict[str, int]:
    """Recorre un manual en anchura desde su índice. Devuelve el recuento."""
    cuenta = {"guardadas": 0, "existentes": 0, "fallidas": 0, "bloqueadas_robots": 0}
    indice = SEDE + manual.indice
    cola: deque[str] = deque([indice])
    vistos = {indice}
    procesadas = 0
    while cola and procesadas < max_paginas:
        url = cola.popleft()
        if not descargador.permitido(url):
            cuenta["bloqueadas_robots"] += 1
            continue
        procesadas += 1
        destino = ruta_local(raiz, manual, url)
        es_indice = url == indice
        if destino.exists() and not es_indice:
            cuenta["existentes"] += 1
            html = destino.read_text(encoding="utf-8", errors="replace")
        else:
            descargado = descargador.get(url)
            if descargado is None:
                cuenta["fallidas"] += 1
                continue
            html = descargado
            # El índice solo se usa para descubrir enlaces: es la tabla de contenidos
            # entera (cientos de KB de títulos) y metería ruido en la recuperación.
            if not es_indice and not dry_run:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(html, encoding="utf-8")
                destino.with_suffix(".json").write_text(
                    json.dumps(sidecar(manual, url), ensure_ascii=False, indent=2), encoding="utf-8"
                )
            if not es_indice:
                cuenta["guardadas"] += 1
        for enlace in enlaces_del_manual(html, url, manual.prefijo):
            if enlace not in vistos:
                vistos.add(enlace)
                cola.append(enlace)
    if cola:
        logger.warning("%s: tope de %d páginas alcanzado con %d pendientes", manual.clave, max_paginas, len(cola))
    return cuenta


@click.command()
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./data/html"),
    show_default=True,
    help="Raíz del corpus; se escribe en <output-dir>/<snapshot>/aeat-manuales/<manual>/.",
)
@click.option("--snapshot-date", default=None, help="Carpeta de snapshot (YYYY-MM-DD). Por defecto, hoy (UTC).")
@click.option(
    "--manual",
    "manuales",
    multiple=True,
    type=click.Choice(sorted(MANUALES)),
    help="Manual a descargar (repetible). Por defecto, todos.",
)
@click.option(
    "--delay", default=0.5, show_default=True, type=click.FloatRange(min=0.0), help="Segundos entre peticiones."
)
@click.option("--max-paginas", default=3000, show_default=True, type=click.IntRange(min=1), help="Tope por manual.")
@click.option("--dry-run", is_flag=True, help="Recorre y cuenta sin escribir nada.")
def cli(
    output_dir: Path,
    snapshot_date: str | None,
    manuales: tuple[str, ...],
    delay: float,
    max_paginas: int,
    dry_run: bool,
) -> None:
    """Descarga los manuales prácticos de la AEAT respetando robots.txt."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    snapshot = snapshot_date or datetime.now(UTC).strftime("%Y-%m-%d")
    raiz = output_dir / snapshot
    descargador = Descargador(delay)
    descargador.cargar_robots()
    resumen: dict[str, dict[str, int]] = {}
    for clave in manuales or sorted(MANUALES):
        click.echo(f"Manual {clave} → {raiz / 'aeat-manuales' / clave}")
        resumen[clave] = descargar_manual(MANUALES[clave], raiz, descargador, max_paginas, dry_run=dry_run)
        click.echo(f"  {resumen[clave]}")
    if not dry_run:
        destino = raiz / "aeat-manuales" / "_resumen.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(
                {"snapshot": snapshot, "terminado": datetime.now(UTC).isoformat(), "manuales": resumen},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    cli()

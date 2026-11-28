"""BOE consolidated law by blocks: one fragment group per article, cut where the law cuts.

The corpus keeps, for each consolidated norm, the text the BOE's open-data API serves
(``<ID>.boe.xml``, written by ``boe_seed`` / ``boe_watch``) and a small sidecar with its title and sigla
(``<ID>.boe.json``). That XML already is the structure Docling had to guess from the rendered page: one block per
article or provision, the headings above it, and every version with the date it came into force
(:mod:`asistente.conocimiento.boe.bloques`).

What this strategy indexes:

* the version in force of every article and provision, cut at paragraph boundaries into fragments that fit the
  embedder (800 characters of body), each starting with its norm and article («LIRPF · Artículo 68. Deducciones»);
* for an article that is repealed today, its last version with text, marked with the date it stopped applying, so a
  question about an earlier tax year still finds it (the searcher leaves it out unless a date is given).

Earlier versions are not embedded: the index stores them whole in its ``preceptos`` table (written by the ingest
writer from this same file), and the text applicable on a date is read from there.

Fragment ids are ``<BOE id>:<block>:<in force since>:<n>``: they depend on the norm, not on the folder the file
sits in, so the same law under two snapshot folders is one set of fragments, not two.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
import logging
from pathlib import Path
import re

from asistente.conocimiento.boe.bloques import Bloque, Norma, Version, es_xml_de_bloques, leer_norma, trocear
from asistente.conocimiento.boe.siglas import sigla_de
from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.conocimiento.processor.strategies.base import IngestStrategy

logger = logging.getLogger(__name__)

SUFIJO = ".boe.xml"
_BOE_ID = re.compile(r"BOE-[A-Z]-\d{4}-\d+")
_PROBE_BYTES = 4096


def es_fichero_de_bloques(ruta: str | Path) -> bool:
    return str(ruta).lower().endswith(SUFIJO)


def cargar_norma(ruta: str | Path) -> Norma:
    """The norm of a ``<ID>.boe.xml`` file, with the title and sigla of its sidecar (``<ID>.boe.json``) if any."""
    ruta = Path(ruta)
    m = _BOE_ID.search(ruta.name)
    if m is None:
        raise ValueError(f"{ruta.name}: el nombre no lleva el identificador del BOE.")
    boe_id = m.group(0)
    datos: dict = {}
    sidecar = ruta.with_suffix(".json")
    if sidecar.is_file():
        try:
            leido = json.loads(sidecar.read_text(encoding="utf-8"))
            datos = leido if isinstance(leido, dict) else {}
        except (OSError, ValueError) as exc:
            logger.warning("Sidecar %s ilegible (%s); se sigue sin título.", sidecar, exc)
    return leer_norma(
        ruta.read_bytes(),
        boe_id=boe_id,
        titulo=str(datos.get("titulo") or ""),
        sigla=str(datos.get("sigla") or sigla_de(boe_id)),
        actualizada=str(datos["actualizada"]) if datos.get("actualizada") else None,
    )


def version_indexable(bloque: Bloque) -> Version | None:
    """The version of ``bloque`` that gets embedded: the one in force or, when that one only says the article was
    repealed, the last one with text. None for a block that never had any."""
    return next((v for v in reversed(bloque.versiones) if not v.sin_contenido), None)


def fragmentos_de(norma: Norma, ruta: str) -> list[FragmentoRecuperado]:
    fragmentos: list[FragmentoRecuperado] = []
    titulo = norma.titulo or norma.nombre_corto
    for bloque in norma.preceptos:
        version = version_indexable(bloque)
        if version is None:
            continue
        for i, trozo in enumerate(trocear(norma, bloque, version)):
            metadatos = {
                "chunk_id": f"{norma.boe_id}:{bloque.bloque_id}:{version.vigente_desde}:{i}",
                "source_file": ruta,
                "source_url": f"https://www.boe.es/buscar/act.php?id={norma.boe_id}#{bloque.bloque_id}",
                "source_authority": "BOE",
                "title": titulo,
                "section": version.rotulo,
                # The heading path, in the shape the rest of the code reads it from Docling's chunks.
                "dl_meta": {"headings": [*bloque.jerarquia, version.rotulo]},
                "norma_id": norma.boe_id,
                "bloque_id": bloque.bloque_id,
                "vigente_desde": version.vigente_desde,
                "_strategy_id": BoeBloquesStrategy.strategy_id,
            }
            if norma.sigla:
                metadatos["norma"] = norma.sigla
            if bloque.numero:
                metadatos["articulo"] = bloque.numero
            if trozo.apartados:
                metadatos["apartados"] = list(trozo.apartados)
            if version.vigente_hasta:
                metadatos["vigente_hasta"] = version.vigente_hasta
            if norma.actualizada:
                # Not ``fecha_actualizacion``: the search's ``desde_fecha`` drops DATED documents older than the
                # cut-off, and consolidated law must never be dropped by it.
                metadatos["actualizada"] = norma.actualizada
            fragmentos.append(FragmentoRecuperado(texto=trozo.texto, metadatos=metadatos))
    return fragmentos


class BoeBloquesStrategy(IngestStrategy):
    """Claims ``<ID>.boe.xml`` files: the BOE API's consolidated text by blocks."""

    strategy_id = "boe-bloques-v1"

    def probe(self, file_path: str) -> bool:
        if not es_fichero_de_bloques(file_path):
            return False
        try:
            with open(file_path, "rb") as fh:
                return es_xml_de_bloques(fh.read(_PROBE_BYTES))
        except OSError:
            return False

    def parse(self, file_path: str) -> Sequence[FragmentoRecuperado]:
        try:
            norma = cargar_norma(file_path)
        except (OSError, ValueError) as exc:
            logger.warning("No se pudo leer %s por bloques: %s", file_path, exc)
            return []
        fragmentos = fragmentos_de(norma, file_path)
        logger.info(
            "%s: %d preceptos, %d fragmentos (por artículo)", norma.boe_id, len(norma.preceptos), len(fragmentos)
        )
        return fragmentos


__all__ = [
    "SUFIJO",
    "BoeBloquesStrategy",
    "cargar_norma",
    "es_fichero_de_bloques",
    "fragmentos_de",
    "version_indexable",
]

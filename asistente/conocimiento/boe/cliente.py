"""The BOE operations the app uses, over the shared HTTP client (:mod:`asistente.plataforma.http`).

One place for the URLs, the courtesy pause towards ``www.boe.es`` and the format
watch keys, so the CLIs (``boe_seed``, ``boe_watch``, ``proactive``, ``formatos``),
the subsidies review and the research capability ask the BOE the same way:

* :func:`metadatos` — metadata of a consolidated law (``fecha_actualizacion``…);
* :func:`texto_consolidado` — the consolidated HTML of a law (``act.php``);
* :func:`texto_bloques` — the same law by blocks (articles, with every version and its dates), as XML;
* :func:`guardar_bloques` — that XML and its sidecar written next to the corpus's other files;
* :func:`sumario` — the daily summary (None when there was no issue that day);
* :func:`xml` — the XML of one disposition;
* :func:`descargar` — any other ``www.boe.es`` URL (a PDF, a search page).

Network errors propagate as ``httpx`` exceptions after the client's retries; a
404 is "not there" (None) where the BOE uses it that way.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import httpx

from asistente.conocimiento import formatos
from asistente.conocimiento.formatos_boe import ReglasMetadatos, entrada_metadatos
from asistente.plataforma import http

RAIZ = "https://www.boe.es/"
METADATOS_URL = RAIZ + "datosabiertos/api/legislacion-consolidada/id/{id}/metadatos"
CONSOLIDADA_URL = RAIZ + "buscar/act.php?id={id}"
BLOQUES_URL = RAIZ + "datosabiertos/api/legislacion-consolidada/id/{id}/texto"
SUMARIO_URL = RAIZ + "datosabiertos/api/boe/sumario/{dia}"
XML_URL = RAIZ + "diario_boe/xml.php?id={id}"
# Courtesy towards a public service: at most ~3 requests per second from this process.
PAUSA = 0.3

__all__ = [
    "CONSOLIDADA_URL",
    "METADATOS_URL",
    "RAIZ",
    "SUMARIO_URL",
    "XML_URL",
    "BLOQUES_URL",
    "descargar",
    "guardar_bloques",
    "metadatos",
    "sumario",
    "texto_bloques",
    "texto_consolidado",
    "xml",
]


def descargar(
    url: str, accept: str = "*/*", *, timeout: float = 60, observar: str | None = None, contexto: str = ""
) -> bytes | None:
    """GET a ``www.boe.es`` URL (only that host): its body, or None on 404."""
    if not url.startswith(RAIZ):
        raise ValueError(f"Solo se descarga de {RAIZ}: {url}")
    return http.cliente().obtener(
        url, accept=accept, timeout=timeout, pausa_host=PAUSA, observar=observar, contexto=contexto
    )


def metadatos(boe_id: str, *, timeout: float = 30) -> dict[str, Any]:
    """``data[0]`` of the metadata of a consolidated law. Raises on a non-2xx answer or malformed JSON."""
    respuesta = http.cliente().get(
        METADATOS_URL.format(id=boe_id), accept="application/json", timeout=timeout, pausa_host=PAUSA
    )
    respuesta.raise_for_status()
    raw = respuesta.text
    # Format watch: recorded before parsing, so an answer in an unknown shape leaves a sample behind.
    formatos.observar("boe_metadatos", raw, contexto=boe_id)
    # Which keys are read is data: the ``boe_metadatos`` format spec.
    return entrada_metadatos(formatos.reglas_de("boe_metadatos", ReglasMetadatos), json.loads(raw), boe_id)


def texto_consolidado(boe_id: str, *, timeout: float = 60, observar: str | None = None) -> bytes:
    """The consolidated HTML of ``boe_id`` as served (bytes, so its SHA-256 matches the corpus manifest)."""
    respuesta = http.cliente().get(
        CONSOLIDADA_URL.format(id=boe_id), timeout=timeout, pausa_host=PAUSA, observar=observar, contexto=boe_id
    )
    respuesta.raise_for_status()
    return respuesta.content


def texto_bloques(boe_id: str, *, timeout: float = 120) -> bytes | None:
    """The consolidated text of ``boe_id`` by blocks, as the open-data API serves it (XML bytes).

    None when the BOE has no consolidated text for that id (a court ruling, an order it never consolidated) or
    answers with something that is not that document.
    """
    from asistente.conocimiento.boe.bloques import es_xml_de_bloques

    respuesta = http.cliente().get(
        BLOQUES_URL.format(id=boe_id), accept="application/xml", timeout=timeout, pausa_host=PAUSA
    )
    if respuesta.status_code in (400, 404):
        return None
    respuesta.raise_for_status()
    cuerpo = respuesta.content
    return cuerpo if es_xml_de_bloques(cuerpo[:4096]) else None


def guardar_bloques(
    boe_id: str, carpeta: Path, *, sigla: str = "", datos: dict[str, Any] | None = None, timeout: float = 120
) -> Path | None:
    """Write ``<carpeta>/<ID>.boe.xml`` (the law by blocks) and its sidecar ``<ID>.boe.json`` (title, sigla,
    date of the last consolidated amendment). Returns the XML's path, or None when the BOE has no blocks for it.

    ``datos`` is the law's metadata entry when the caller already has it (``boe_watch``); otherwise it is asked
    for, and a failure there only leaves the sidecar without a title.
    """
    from asistente.conocimiento.boe.siglas import sigla_de

    cuerpo = texto_bloques(boe_id, timeout=timeout)
    if cuerpo is None:
        return None
    if datos is None:
        try:
            datos = metadatos(boe_id)
        except httpx.HTTPError, RuntimeError, ValueError:
            datos = {}
    actualizada = str(datos.get("fecha_actualizacion") or "")[:8]
    rango = datos.get("rango")
    sidecar = {
        "id": boe_id,
        "titulo": str(datos.get("titulo") or "").strip().rstrip("."),
        "sigla": sigla or sigla_de(boe_id),
        # Not ``fecha_actualizacion``: the loader merges sidecar keys into every fragment, and the search's
        # ``desde_fecha`` drops dated documents older than its cut-off. Consolidated law must never be dropped.
        "actualizada": f"{actualizada[:4]}-{actualizada[4:6]}-{actualizada[6:8]}" if len(actualizada) == 8 else "",
        "rango": str(rango.get("texto") if isinstance(rango, dict) else rango or ""),
    }
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{boe_id}.boe.xml"
    destino.write_bytes(cuerpo)
    destino.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def sumario(dia: date, *, timeout: float = 60) -> dict[str, Any] | None:
    """The BOE summary of ``dia`` (parsed JSON), or None when there was no issue that day."""
    raw = descargar(SUMARIO_URL.format(dia=dia.strftime("%Y%m%d")), "application/json", timeout=timeout)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        # An HTML error page served with 200 never reaches the parser: tell the format watch before failing.
        formatos.observar("boe_sumario", raw.decode("utf-8", errors="replace"), contexto=dia.isoformat())
        raise
    return payload if isinstance(payload, dict) else None


def xml(boe_id: str, *, timeout: float = 60) -> bytes | None:
    """The XML of one disposition, or None when the BOE does not have it."""
    return descargar(XML_URL.format(id=boe_id), "application/xml", timeout=timeout)


# Re-exported so callers catch the client's errors without importing httpx themselves.
ErrorHttp = httpx.HTTPError

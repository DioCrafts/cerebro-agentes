"""The BOE operations the app uses, over the shared HTTP client (:mod:`asistente.plataforma.http`).

One place for the URLs, the courtesy pause towards ``www.boe.es`` and the format
watch keys, so the CLIs (``boe_seed``, ``boe_watch``, ``proactive``, ``formatos``),
the subsidies review and the research capability ask the BOE the same way:

* :func:`metadatos` — metadata of a consolidated law (``fecha_actualizacion``…);
* :func:`texto_consolidado` — the consolidated HTML of a law (``act.php``);
* :func:`sumario` — the daily summary (None when there was no issue that day);
* :func:`xml` — the XML of one disposition;
* :func:`descargar` — any other ``www.boe.es`` URL (a PDF, a search page).

Network errors propagate as ``httpx`` exceptions after the client's retries; a
404 is "not there" (None) where the BOE uses it that way.
"""

from __future__ import annotations

from datetime import date
import json
from typing import Any

import httpx

from asistente.conocimiento import formatos
from asistente.conocimiento.formatos_boe import ReglasMetadatos, entrada_metadatos
from asistente.plataforma import http

RAIZ = "https://www.boe.es/"
METADATOS_URL = RAIZ + "datosabiertos/api/legislacion-consolidada/id/{id}/metadatos"
CONSOLIDADA_URL = RAIZ + "buscar/act.php?id={id}"
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
    "descargar",
    "metadatos",
    "sumario",
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

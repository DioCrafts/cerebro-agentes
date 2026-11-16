"""Gathering sources: the app's own legal corpus and the BOE (no model here). The web lives in ``web.py``.

Every source keeps its FULL text (what the citation gate checks quotes against) and an EXTRACT (the passages
sent to the synthesis model, chosen by the query's words). Retrieved text is untrusted data: it is marked
``sospechosa`` when it carries prompt-injection bait (``llm/security.py``), and only ever reaches a model
between untrusted-data marks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
import re
import threading
import unicodedata
from typing import Any
import urllib.parse
import urllib.request

from hacienda_gpt.agent.evidence import infer_authority, source_type_for
from hacienda_gpt.agent.search import CorpusSearcher, SearchFilters
from hacienda_gpt.capacidades.investigacion.dominios import es_oficial, host_de
from hacienda_gpt.capacidades.investigacion.modelo import TipoFuente
from hacienda_gpt.conocimiento.texto import texto_de_html, url_de
from hacienda_gpt.llm.security import sanitize_retrieved_context

logger = logging.getLogger(__name__)

RAIZ_BOE = "https://www.boe.es/"
API_BOE = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
_PALABRA_RE = re.compile(r"[a-z0-9ñ]+")
# Words that say nothing about a topic (searches, passage scoring).
VACIAS = frozenset(
    "a al algo como con cual cuales cuando de del desde donde el ella en entre es esta este esto hay la las le lo "
    "los mas me mi mis muy no o para pero por que qué se sea ser si sin sobre su sus tambien te tengo tiene un una "
    "uno unos y ya yo necesito necesita puedo puede debo debe hacer hace dice dicen quiero cuanto cuanta cuantos "
    "segun año años".split()
)


def plano(texto: str) -> str:
    """Lower case without accents (ñ kept), for matching words."""
    descompuesto = unicodedata.normalize("NFKD", texto.lower().replace("ñ", "\x00"))
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).replace("\x00", "ñ")


def palabras_clave(texto: str, maximo: int = 6) -> list[str]:
    """The topic words of ``texto`` (no stop words, 3+ letters), first occurrence order."""
    vistas: list[str] = []
    for palabra in _PALABRA_RE.findall(plano(texto)):
        if len(palabra) >= 3 and palabra not in VACIAS and palabra not in vistas:
            vistas.append(palabra)
    return vistas[:maximo]


def sospechosa(texto: str) -> bool:
    """Does ``texto`` carry prompt-injection bait (what ``sanitize_retrieved_context`` would redact)?"""
    return sanitize_retrieved_context(texto) != re.sub("[­​‌‍⁠﻿]", "", texto)


def extracto(texto: str, terminos: Sequence[str], maximo: int) -> str:
    """The passages of ``texto`` that mention most of ``terminos``, in document order, up to ``maximo`` chars.

    Paragraphs are lines (``texto_de_html`` gives one per line); a long text without line breaks is cut in
    windows. Without any matching passage, the beginning of the text.
    """
    if len(texto) <= maximo:
        return texto
    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    if len(lineas) <= 1:
        lineas = [texto[i : i + 800] for i in range(0, len(texto), 800)]
    claves = [plano(t) for t in terminos if t.strip()]
    puntuadas = []
    for indice, linea in enumerate(lineas):
        texto_plano = plano(linea)
        puntos = sum(1 for clave in claves if clave in texto_plano)
        if puntos:
            puntuadas.append((puntos, indice))
    if not puntuadas:
        return texto[:maximo]
    elegidas: list[int] = []
    total = 0
    for _puntos, indice in sorted(puntuadas, key=lambda par: (-par[0], par[1])):
        largo = len(lineas[indice]) + 1
        if total + largo > maximo:
            continue
        elegidas.append(indice)
        total += largo
    return "\n".join(lineas[i] for i in sorted(elegidas)) or texto[:maximo]


@dataclass
class FuenteRecuperada:
    """A source as gathered: full text for the gate, extract for the model. ``id`` is assigned later."""

    tipo: TipoFuente
    titulo: str
    texto: str
    url: str | None = None
    referencia: str | None = None
    oficial: bool = False
    recuperada_el: datetime | None = None
    nota: str | None = None
    extracto: str = ""
    id: str = ""

    @property
    def clave(self) -> str:
        base = self.url or self.referencia or hashlib.sha256(self.texto.encode("utf-8")).hexdigest()
        return f"{self.tipo}:{base}"

    @property
    def dominio(self) -> str | None:
        return host_de(self.url)

    @property
    def sospechosa(self) -> bool:
        return sospechosa(self.texto)


def _ahora() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# The app's corpus (FAISS index + doctrine validity graph)
# --------------------------------------------------------------------------- #


def _primero(metadata: dict[str, Any], claves: Sequence[str]) -> str | None:
    for clave in claves:
        valor = metadata.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def _vigencia(grafo: Any, metadata: dict[str, Any]) -> str | None:
    """The doctrine graph's warning about the chunk's document (superseded, disputed, qualified), or None."""
    if grafo is None:
        return None
    try:
        from hacienda_gpt.doctrine.citas import identify_document

        doc = identify_document(metadata)
        if doc is None:
            return None
        estado = grafo.vigencia(doc.clave)
    except Exception:  # the graph is an aid, never a reason to lose the source
        logger.warning("doctrine graph lookup failed", exc_info=True)
        return None
    if estado.estado in ("superada", "discutida", "matizada"):
        return f"Doctrina {estado.estado} según el grafo de vigencia: {estado.resumen}"
    return None


def buscar_corpus(
    searcher: CorpusSearcher, consulta: str, *, k: int, grafo: Any | None = None
) -> list[FuenteRecuperada]:
    """The corpus chunks for ``consulta`` (the same search as the agent's ``buscar_fuentes``)."""
    documentos = searcher.search(consulta, k=k, filters=SearchFilters())
    fuentes: list[FuenteRecuperada] = []
    for doc in documentos:
        metadata = dict(getattr(doc, "metadata", None) or {})
        texto = " ".join(str(getattr(doc, "page_content", "") or "").split())
        if not texto:
            continue
        autoridad = infer_authority(metadata)
        url = _primero(metadata, ("source_url",))
        url = url if host_de(url) else None
        titulo = _primero(metadata, ("title", "section")) or _primero(metadata, ("source", "source_file")) or "Corpus"
        referencia = _primero(
            metadata, ("boe_id", "num_consulta", "numero_resolucion", "roj", "ecli", "criterio_id", "chunk_id")
        )
        tipo = source_type_for(autoridad, metadata)
        fuentes.append(
            FuenteRecuperada(
                tipo="corpus",
                titulo=f"{titulo} ({autoridad})" if autoridad and autoridad not in titulo else titulo,
                texto=texto,
                url=url,
                referencia=referencia,
                # The corpus holds law, doctrine, case law and the Tax Agency's pages: official when the issuing
                # authority is known or the page is on an official host.
                oficial=tipo is not None or es_oficial(url),
                recuperada_el=_ahora(),
                nota=_vigencia(grafo, metadata),
            )
        )
    return fuentes


# --------------------------------------------------------------------------- #
# The BOE: consolidated legislation (open-data API) and its texts
# --------------------------------------------------------------------------- #

Descargar = Callable[[str, str], bytes | None]


def descargar_boe(url: str, accept: str, *, timeout: int = 30, intentos: int = 2) -> bytes | None:
    """GET a www.boe.es URL (only that host). None after the retries."""
    if not url.startswith(RAIZ_BOE):
        raise ValueError(f"Solo se descarga de {RAIZ_BOE}: {url}")
    peticion = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "HaciendaGPT-investigacion/1"})
    for intento in range(intentos):
        try:
            with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:  # noqa: S310 - host checked above
                datos: bytes = respuesta.read()
                return datos
        except Exception as exc:  # network errors: retry, then give up
            logger.warning("BOE download failed (%s/%s): %s", intento + 1, intentos, type(exc).__name__)
    return None


@dataclass(frozen=True)
class NormaBOE:
    boe_id: str
    titulo: str
    url: str
    actualizada: str | None = None


_TERMINO_RE = re.compile(r"[^0-9a-záéíóúüñ ]", re.IGNORECASE)


def _terminos(terminos: Sequence[str]) -> list[str]:
    limpios: list[str] = []
    for termino in terminos:
        for palabra in _TERMINO_RE.sub(" ", termino).split():
            palabra = palabra.lower()[:30]
            if len(palabra) >= 3 and plano(palabra) not in VACIAS and palabra not in limpios:
                limpios.append(palabra)
    return limpios[:4]


class ClienteBOE:
    """Search the consolidated legislation of the BOE by title (then by text) and read a norm's text."""

    def __init__(self, descargar: Descargar | None = None, *, max_textos: int = 16) -> None:
        self._descargar = descargar or descargar_boe
        self._textos: dict[str, str] = {}
        self._max_textos = max_textos
        self._lock = threading.Lock()

    def _consulta(self, consulta: str, limite: int) -> list[NormaBOE]:
        query = json.dumps({"query": {"query_string": {"query": consulta}}})
        url = f"{API_BOE}?query={urllib.parse.quote(query)}&limit={max(1, min(limite * 3, 30))}"
        crudo = self._descargar(url, "application/json")
        if not crudo:
            return []
        try:
            datos = json.loads(crudo)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("BOE search answered something that is not JSON")
            return []
        filas = datos.get("data") if isinstance(datos, dict) else None
        normas: list[NormaBOE] = []
        for fila in filas if isinstance(filas, list) else []:
            if not isinstance(fila, dict):
                continue
            boe_id = fila.get("identificador")
            titulo = fila.get("titulo")
            if not isinstance(boe_id, str) or not re.fullmatch(r"BOE-[A-Z]-\d{4}-\d+", boe_id):
                continue
            if fila.get("vigencia_agotada") == "S":  # no longer in force
                continue
            normas.append(
                NormaBOE(
                    boe_id=boe_id,
                    titulo=str(titulo or boe_id),
                    url=url_de(boe_id),
                    actualizada=str(fila.get("fecha_actualizacion") or "") or None,
                )
            )
            if len(normas) >= limite:
                break
        return normas

    def buscar(self, terminos: Sequence[str], *, limite: int) -> list[NormaBOE]:
        palabras = _terminos(terminos)
        if not palabras or limite <= 0:
            return []
        conjuncion = " AND ".join(palabras)
        normas = self._consulta(f"titulo:({conjuncion})", limite)
        if len(normas) < limite:
            vistas = {n.boe_id for n in normas}
            normas += [n for n in self._consulta(f"texto:({conjuncion})", limite) if n.boe_id not in vistas]
        return normas[:limite]

    def texto(self, boe_id: str) -> str | None:
        """The plain text of the norm's consolidated version (one paragraph per line), or None."""
        with self._lock:
            if boe_id in self._textos:
                return self._textos[boe_id]
        crudo = self._descargar(url_de(boe_id), "text/html")
        if not crudo:
            return None
        texto = texto_de_html(crudo.decode("utf-8", errors="replace"))
        with self._lock:
            if len(self._textos) >= self._max_textos:
                self._textos.pop(next(iter(self._textos)))
            self._textos[boe_id] = texto
        return texto


def buscar_boe(cliente: ClienteBOE, terminos: Sequence[str], *, limite: int) -> list[FuenteRecuperada]:
    fuentes: list[FuenteRecuperada] = []
    for norma in cliente.buscar(terminos, limite=limite):
        texto = cliente.texto(norma.boe_id)
        if not texto:
            continue
        fuentes.append(
            FuenteRecuperada(
                tipo="boe",
                titulo=norma.titulo,
                texto=texto,
                url=norma.url,
                referencia=norma.boe_id,
                oficial=True,
                recuperada_el=_ahora(),
                nota=f"Texto consolidado (actualizado {norma.actualizada})" if norma.actualizada else None,
            )
        )
    return fuentes


__all__ = [
    "API_BOE",
    "VACIAS",
    "ClienteBOE",
    "Descargar",
    "FuenteRecuperada",
    "NormaBOE",
    "buscar_boe",
    "buscar_corpus",
    "descargar_boe",
    "extracto",
    "palabras_clave",
    "plano",
    "sospechosa",
]

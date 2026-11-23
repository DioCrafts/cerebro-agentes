"""Corpus search behind the agent's ``buscar_fuentes`` tool.

Unlike the legacy ``/qa`` chain there is no LLM here: no MultiQuery fan-out and
no history-aware rewriting. The agent writes its own queries, reads the results
and searches again if they fall short — that *is* the query rewriting, and it
is driven by what the model actually found missing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import threading
from typing import Any, Protocol

from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.nucleo.agent.evidence import infer_authority, source_type_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchFilters:
    source_type: str | None = None  # one of evidence.SOURCE_TYPES
    tributo: str | None = None
    jurisdiction_scope: str | None = None  # "estatal" | "autonomico:<código o nombre>"
    binding_only: bool = False
    date_from: str | None = None  # ISO "YYYY" or "YYYY-MM-DD": drop dated documents older than this

    def is_empty(self) -> bool:
        return not (self.source_type or self.tributo or self.jurisdiction_scope or self.binding_only or self.date_from)


class Reordenador(Protocol):
    """Second stage of the search (the cross-encoder reranker): reorders and trims what the first stage found."""

    def reordenar(self, fragmentos: Sequence[FragmentoRecuperado], consulta: str) -> list[FragmentoRecuperado]: ...


class AlmacenVectorial(Protocol):
    """First stage: dense neighbours, the metadata ``filter`` applied over the ``fetch_k`` nearest."""

    def similarity_search(
        self,
        query: str,
        k: int = ...,
        filter: Callable[[dict[str, Any]], bool] | None = ...,  # noqa: A002 - FAISS' parameter name
        fetch_k: int = ...,
    ) -> list[FragmentoRecuperado]: ...


class CorpusSearcher(Protocol):
    def search(self, query: str, *, k: int, filters: SearchFilters) -> list[FragmentoRecuperado]: ...


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return []


DATE_METADATA_KEYS = ("fecha_resolucion", "fecha_actualizacion", "last_updated", "fecha")
_ISO_DATE_RE = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


def normalize_date(value: Any) -> str | None:
    """Normalize ``YYYY`` / ``YYYY-MM`` / ``YYYY-MM-DD…`` to a comparable ``YYYY-MM-DD``."""
    if not isinstance(value, str):
        return None
    match = _ISO_DATE_RE.match(value.strip())
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month or '01'}-{day or '01'}"


def build_filter_predicate(filters: SearchFilters) -> Callable[[dict[str, Any]], bool]:
    """Metadata predicate for ``filters``.

    ``source_type`` and ``binding_only`` are **strict**: a document whose
    authority is unknown is not doctrine, and one without an explicit
    ``vinculante=True`` is not binding. ``tributo`` and ``jurisdiction_scope``
    are **lenient** (a document that does not declare them is kept), because
    most of the corpus carries no such tag and a strict match would silently
    hide the state-wide law that applies everywhere. ``date_from`` is lenient
    too: it removes rulings/consultas dated before the cut-off (superseded
    doctrine after a reform) but keeps undated documents — consolidated
    legislation carries no resolution date and is current by construction.
    """
    date_from = normalize_date(filters.date_from)
    tributo = (filters.tributo or "").strip().lower()
    scope = (filters.jurisdiction_scope or "").strip().lower()

    def predicate(metadata: dict[str, Any]) -> bool:
        if filters.source_type:
            if source_type_for(infer_authority(metadata), metadata) != filters.source_type:
                return False
        if filters.binding_only and metadata.get("vinculante") is not True:
            return False
        if date_from:
            dated = next((d for d in (normalize_date(metadata.get(key)) for key in DATE_METADATA_KEYS) if d), None)
            if dated is not None and dated < date_from:
                return False
        if tributo:
            declared = [
                item.lower()
                for key in ("covered_tributos", "tributos_detectados")
                for item in _as_list(metadata.get(key))
            ]
            if declared and not any(tributo in item or item in tributo for item in declared):
                return False
        if scope:
            declared_scope = str(metadata.get("jurisdiction_scope") or "").strip().lower()
            if declared_scope:
                if scope == "estatal":
                    if declared_scope.startswith("autonomico"):
                        return False
                elif declared_scope.startswith("autonomico") and declared_scope != scope:
                    # Another region's rules never apply; state-wide ones do.
                    return False
        return True

    return predicate


class SinCorpus:
    """No corpus built yet: every search finds nothing, so the grounding gate abstains on normative questions
    while the rest of the brain (calculations, other modules) keeps working."""

    def search(self, query: str, *, k: int, filters: SearchFilters) -> list[FragmentoRecuperado]:
        return []


class FaissCorpusSearcher:
    """Dense FAISS search + optional cross-encoder rerank, with sanitization.

    ``vectorstore`` is the legacy FAISS index (``conocimiento/indice/faiss_legado.py``) or anything with its
    ``similarity_search`` (the retrieval benchmark searches an in-memory matrix).
    """

    def __init__(
        self, vectorstore: AlmacenVectorial, *, reranker: Reordenador | None = None, first_stage_k: int = 30
    ) -> None:
        self._vectorstore = vectorstore
        self._reranker = reranker
        self._first_stage_k = first_stage_k
        # ONE lock for every caller (agent runs, researcher threads, the council): the embedder and the reranker
        # are not safe to call from two threads, and the searcher is shared by the whole process.
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls) -> CorpusSearcher:
        """The process's shared searcher for the configured index (loaded once; later calls reuse it)."""
        from asistente.plataforma import settings

        clave = (
            str(settings.INDICE_BACKEND),
            str(settings.FAISS_INDEX_PATH),
            str(settings.INDICE_SQLITE_PATH),
            bool(settings.RERANKER_ENABLED),
            str(settings.RERANKER_MODEL),
            int(settings.RERANKER_TOP_K),
            int(settings.RERANKER_FIRST_STAGE_K),
        )
        with _BUSCADORES_LOCK:
            buscador = _BUSCADORES.get(clave)
            if buscador is None:
                buscador = cls._cargar()
                if not isinstance(buscador, SinCorpus):  # a missing index is looked for again next time
                    _BUSCADORES[clave] = buscador
            return buscador

    @classmethod
    def _cargar(cls) -> CorpusSearcher:
        from asistente.conocimiento.indice.faiss_legado import AlmacenFaissLegado
        from asistente.conocimiento.llm.embeddings import create_embeddings
        from asistente.plataforma import settings

        if settings.INDICE_BACKEND == "sqlite":
            from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher

            buscador: CorpusSearcher = SqliteCorpusSearcher.from_settings()
            return buscador
        if not Path(settings.FAISS_INDEX_PATH).exists():
            # A missing index must not take the whole brain down: searches find nothing (the grounding gate
            # abstains on normative questions) and every other specialist stays available.
            logger.warning("No FAISS index at %s: searching without a corpus.", settings.FAISS_INDEX_PATH)
            return SinCorpus()

        if not settings.FAISS_TRUSTED_INDEX:
            raise RuntimeError(
                "Refusing to load FAISS index with dangerous deserialization. "
                "Set FAISS_TRUSTED_INDEX=true only for trusted local indexes."
            )
        vectorstore = AlmacenFaissLegado.cargar(settings.FAISS_INDEX_PATH, create_embeddings(), confiar=True)
        reranker: Reordenador | None = None
        if settings.RERANKER_ENABLED:
            from asistente.conocimiento.llm.reranker import MLXReranker

            reranker = MLXReranker(model_path=settings.RERANKER_MODEL, top_k=settings.RERANKER_TOP_K)
        return cls(vectorstore, reranker=reranker, first_stage_k=settings.RERANKER_FIRST_STAGE_K)

    def search(self, query: str, *, k: int, filters: SearchFilters) -> list[FragmentoRecuperado]:
        from asistente.conocimiento.llm.security import sanitize_context_documents

        first_stage_k = max(k, self._first_stage_k) if self._reranker is not None else k
        with self._lock:
            if filters.is_empty():
                docs = self._vectorstore.similarity_search(query, k=first_stage_k)
            else:
                # FAISS applies the filter AFTER fetching fetch_k neighbours; a
                # selective filter (e.g. only binding doctrine) needs a deep pool.
                docs = self._vectorstore.similarity_search(
                    query,
                    k=first_stage_k,
                    filter=build_filter_predicate(filters),
                    fetch_k=max(first_stage_k * 20, 500),
                )
            if self._reranker is not None and docs:
                docs = list(self._reranker.reordenar(docs, query))
        # Retrieved text is untrusted input to the model: redact injection bait.
        return sanitize_context_documents(list(docs[:k]))


_BUSCADORES: dict[tuple[Any, ...], CorpusSearcher] = {}
_BUSCADORES_LOCK = threading.Lock()

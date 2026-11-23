"""Buscador del corpus sobre ``IndiceHibrido`` con la misma interfaz que ``FaissCorpusSearcher``.

Se elige con ``INDICE_BACKEND=sqlite``. La búsqueda es densa (``sqlite-vec``) y, con ``BM25_ENABLED``,
híbrida: RRF entre el ranking denso y el de FTS5, con los pesos ``BM25_DENSE_WEIGHT`` / ``BM25_BM25_WEIGHT``.
Sustituye al ``EnsembleRetriever`` de LangChain con ``rank-bm25``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from asistente.conocimiento.indice.almacen import Acierto, IndiceHibrido

logger = logging.getLogger(__name__)

# Con filtros, el filtro de metadatos va después del KNN: hace falta una reserva honda (como fetch_k en FAISS).
PROFUNDIDAD_CON_FILTRO = 500


class SqliteCorpusSearcher:
    def __init__(
        self,
        indice: IndiceHibrido,
        embeddings: Any,
        *,
        reranker: Any | None = None,
        first_stage_k: int = 30,
        hibrido: bool = False,
        peso_denso: float = 1.0,
        peso_lexico: float = 1.0,
    ) -> None:
        self._indice = indice
        self._embeddings = embeddings
        self._reranker = reranker
        self._first_stage_k = first_stage_k
        self._hibrido = hibrido
        self._peso_denso = peso_denso
        self._peso_lexico = peso_lexico

    @classmethod
    def from_settings(cls) -> Any:
        from asistente.conocimiento.llm.embeddings import create_embeddings
        from asistente.nucleo.agent.search import SinCorpus
        from asistente.plataforma import settings

        ruta = Path(settings.INDICE_SQLITE_PATH)
        if not ruta.exists():
            logger.warning("No hay índice SQLite en %s: se busca sin corpus.", ruta)
            return SinCorpus()
        reranker = None
        if settings.RERANKER_ENABLED:
            from asistente.conocimiento.llm.reranker import MLXReranker

            reranker = MLXReranker(model_path=settings.RERANKER_MODEL, top_k=settings.RERANKER_TOP_K)
        return cls(
            IndiceHibrido(ruta),
            create_embeddings(),
            reranker=reranker,
            first_stage_k=settings.RERANKER_FIRST_STAGE_K,
            hibrido=settings.BM25_ENABLED,
            peso_denso=settings.BM25_DENSE_WEIGHT,
            peso_lexico=settings.BM25_BM25_WEIGHT,
        )

    def _recuperar(self, query: str, profundidad: int) -> list[Acierto]:
        vector = self._embeddings.embed_query(query)
        if self._hibrido:
            return self._indice.buscar_hibrido(
                query,
                vector,
                k=profundidad,
                profundidad=profundidad,
                peso_denso=self._peso_denso,
                peso_lexico=self._peso_lexico,
            )
        return self._indice.buscar_denso(vector, k=profundidad)

    def search(self, query: str, *, k: int, filters: Any) -> list[Document]:
        from asistente.conocimiento.llm.chain import _sanitize_context_documents
        from asistente.nucleo.agent.search import build_filter_predicate

        first_stage_k = max(k, self._first_stage_k) if self._reranker is not None else k
        profundidad = first_stage_k
        predicado = None
        if not filters.is_empty():
            predicado = build_filter_predicate(filters)
            profundidad = max(first_stage_k * 20, PROFUNDIDAD_CON_FILTRO)
        docs = [
            Document(page_content=a.fragmento.texto, metadata=a.fragmento.metadatos, id=a.fragmento.chunk_id)
            for a in self._recuperar(query, profundidad)
            if predicado is None or predicado(a.fragmento.metadatos)
        ][:first_stage_k]
        if self._reranker is not None and docs:
            docs = list(self._reranker.compress_documents(docs, query))
        # El texto recuperado es entrada no fiable para el modelo: se limpia igual que en el backend FAISS.
        return _sanitize_context_documents(list(docs[:k]))

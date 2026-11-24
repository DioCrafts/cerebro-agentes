"""El buscador del corpus que usa el agente, sobre ``IndiceHibrido`` (un fichero SQLite).

Dos etapas. La primera trae ``first_stage_k`` candidatos: con ``BM25_ENABLED`` es híbrida, una fusión RRF del
ranking denso (``sqlite-vec``) y del léxico (FTS5) con los pesos ``BM25_DENSE_WEIGHT`` / ``BM25_BM25_WEIGHT``;
sin él, solo densa. La segunda, si hay reranker, los reordena y se queda con los ``k`` primeros. El texto
recuperado se sanea antes de devolverlo, porque es entrada no fiable para el modelo.
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Any

from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.conocimiento.indice.almacen import Acierto, IndiceHibrido

logger = logging.getLogger(__name__)

# Con filtros, el filtro de metadatos va después del KNN: hace falta una reserva honda.
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
        # UN cerrojo para todos los que llaman (el agente, los hilos del investigador, el consejo): el embedder y
        # el reranker no admiten dos hilos a la vez, y el buscador es compartido por todo el proceso.
        self._cerrojo = threading.Lock()

    @property
    def hibrido(self) -> bool:
        return self._hibrido

    @classmethod
    def from_settings(cls) -> Any:
        """El buscador del índice configurado (``INDICE_SQLITE_PATH``), o ``SinCorpus`` si no existe."""
        from asistente.conocimiento.llm.embeddings import create_embeddings
        from asistente.nucleo.agent.search import SinCorpus
        from asistente.plataforma import settings

        ruta = Path(settings.INDICE_SQLITE_PATH)
        if not ruta.exists():
            # Sin índice no se cae el cerebro entero: las búsquedas no encuentran nada (la puerta de grounding se
            # abstiene en las preguntas de normativa) y el resto de especialistas sigue disponible.
            logger.warning("No hay índice del corpus en %s: se busca sin corpus.", ruta)
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

    def primera_etapa(self, query: str, profundidad: int) -> list[FragmentoRecuperado]:
        """Los ``profundidad`` primeros candidatos de la primera etapa, sin reranker ni filtros (diagnóstico)."""
        with self._cerrojo:
            return [_fragmento(a) for a in self._recuperar(query, profundidad)]

    def search(self, query: str, *, k: int, filters: Any) -> list[FragmentoRecuperado]:
        from asistente.conocimiento.llm.security import sanitize_context_documents
        from asistente.nucleo.agent.search import build_filter_predicate

        first_stage_k = max(k, self._first_stage_k) if self._reranker is not None else k
        profundidad = first_stage_k
        predicado = None
        if not filters.is_empty():
            predicado = build_filter_predicate(filters)
            profundidad = max(first_stage_k * 20, PROFUNDIDAD_CON_FILTRO)
        with self._cerrojo:
            docs = [
                _fragmento(a)
                for a in self._recuperar(query, profundidad)
                if predicado is None or predicado(a.fragmento.metadatos)
            ][:first_stage_k]
            if self._reranker is not None and docs:
                docs = list(self._reranker.reordenar(docs, query))
        # El texto recuperado es entrada no fiable para el modelo: se limpia el señuelo de inyección.
        return list(sanitize_context_documents(list(docs[:k])))


def _fragmento(acierto: Acierto) -> FragmentoRecuperado:
    f = acierto.fragmento
    return FragmentoRecuperado(texto=f.texto, metadatos=f.metadatos, id=f.chunk_id)

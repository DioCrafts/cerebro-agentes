"""Índice del corpus en un solo fichero SQLite: vectores con ``sqlite-vec`` y BM25 con FTS5.

Sustituye al par FAISS + pickle de LangChain + ``rank-bm25``. Ver ``almacen.IndiceHibrido``.
"""

from asistente.conocimiento.indice.almacen import Acierto, Fragmento, IndiceHibrido, consulta_fts, fusion_rrf

__all__ = ["Acierto", "Fragmento", "IndiceHibrido", "consulta_fts", "fusion_rrf"]

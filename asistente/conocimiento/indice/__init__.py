"""Índice del corpus en un solo fichero SQLite: vectores con ``sqlite-vec`` y BM25 con FTS5.

Ver ``almacen.IndiceHibrido`` (el almacén), ``buscador.SqliteCorpusSearcher`` (la búsqueda del agente) e
``ingesta`` (escritura y lectura completa).
"""

from asistente.conocimiento.indice.almacen import Acierto, Fragmento, IndiceHibrido, consulta_fts, fusion_rrf

__all__ = ["Acierto", "Fragmento", "IndiceHibrido", "consulta_fts", "fusion_rrf"]

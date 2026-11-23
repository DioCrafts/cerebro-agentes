"""Fragmento de texto del corpus con sus metadatos: lo que produce la ingesta y lo que devuelve la búsqueda.

Sustituye a ``langchain_core.documents.Document``. Es inmutable: el saneado, el reranker y el contextualizador
devuelven copias (``con``) en vez de tocar el fragmento que guarda el índice. Los metadatos sí son un ``dict``
y la ingesta los completa en el sitio (``chunk_id``, ``source_file``) antes de indexar; quien recibe un
fragmento de la búsqueda no debe modificarlos.

El único sitio donde un fragmento se convierte en un ``Document`` de LangChain, y viceversa, es el adaptador
del índice FAISS heredado (``conocimiento/indice/faiss_legado.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class FragmentoRecuperado:
    texto: str
    metadatos: dict[str, Any] = field(default_factory=dict)
    # Id del fragmento en el índice, cuando viene de él (el ``chunk_id`` de la ingesta).
    id: str | None = None

    def con(self, **cambios: Any) -> FragmentoRecuperado:
        """Copia con los campos indicados cambiados (``texto``, ``metadatos`` o ``id``)."""
        return replace(self, **cambios)

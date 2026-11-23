"""Escritura de la ingesta (``DocumentProcessor``) sobre ``IndiceHibrido``.

Expone las dos operaciones que la ingesta hace sobre el índice FAISS heredado (``delete`` y
``add_documents``), así que ``document_loader`` solo cambia al construir el destino. Cada llamada es una
transacción SQLite, de modo que no hay ``save_local`` ni ficheros temporales: el orden «índice primero,
manifiesto después» se conserva porque el manifiesto se escribe tras el ``commit``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import os
from pathlib import Path
from typing import TYPE_CHECKING

from asistente.conocimiento.fragmento import FragmentoRecuperado
from asistente.conocimiento.indice.almacen import Fragmento, IndiceHibrido

if TYPE_CHECKING:
    from asistente.conocimiento.llm.embeddings import Embedder

NOMBRE_FICHERO = "indice.sqlite"


def ruta_indice(output_dir: str | Path) -> Path:
    return Path(output_dir) / NOMBRE_FICHERO


def borrar_indice(output_dir: str | Path) -> None:
    """Reconstrucción completa: se empieza de un fichero vacío (también sin restos de WAL)."""
    ruta = ruta_indice(output_dir)
    for sufijo in ("", "-wal", "-shm"):
        candidato = Path(f"{ruta}{sufijo}")
        if candidato.exists():
            os.remove(candidato)


class EscritorSqlite:
    def __init__(self, output_dir: str | Path, embeddings: Embedder) -> None:
        self.indice = IndiceHibrido(ruta_indice(output_dir))
        self._embeddings = embeddings

    def delete(self, ids: Iterable[str]) -> int:
        return self.indice.borrar(ids)

    def add_documents(self, documents: Sequence[FragmentoRecuperado], ids: Sequence[str]) -> None:
        vectores = self._embeddings.embed_documents([d.texto for d in documents])
        fragmentos = [
            Fragmento(chunk_id=i, texto=d.texto, metadatos=dict(d.metadatos))
            for d, i in zip(documents, ids, strict=True)
        ]
        self.indice.anadir(fragmentos, vectores)

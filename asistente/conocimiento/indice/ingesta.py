"""El índice del corpus en disco: ``<directorio>/indice.sqlite``, escrito por la ingesta y leído entero por quien
necesita todos los fragmentos.

``EscritorSqlite`` da a la ingesta (``DocumentProcessor``) sus dos operaciones, ``delete`` y ``add_documents``.
Cada llamada es una transacción SQLite, así que el orden «índice primero, manifiesto después» se conserva: el
manifiesto se escribe tras el ``commit``. ``leer_fragmentos`` devuelve todos los fragmentos sin cargar el
embedder (el grafo de doctrina necesita el texto, no los vectores).
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


def leer_fragmentos(directorio: str | Path) -> list[FragmentoRecuperado]:
    """Todos los fragmentos de ``<directorio>/indice.sqlite``, en orden de alta, sin vectores.

    ``FileNotFoundError`` si no hay índice: abrirlo en solo lectura no crea un fichero vacío.
    """
    ruta = ruta_indice(directorio)
    if not ruta.exists():
        raise FileNotFoundError(ruta)
    with IndiceHibrido(ruta, solo_lectura=True) as indice:
        return [FragmentoRecuperado(texto=f.texto, metadatos=f.metadatos, id=f.chunk_id) for f, _ in indice.recorrer()]

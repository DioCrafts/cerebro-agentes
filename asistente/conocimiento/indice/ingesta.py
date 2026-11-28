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
from asistente.conocimiento.indice.almacen import Fragmento, IndiceHibrido, Precepto

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
        ids = list(ids)
        fuentes = self.indice.fuentes_de(ids)
        borrados = self.indice.borrar(ids)
        # A law whose fragments are all gone (the file left the corpus) takes its articles with it.
        for fuente in fuentes:
            if self.indice.fragmentos_de_fuente(fuente) == 0:
                self.indice.borrar_preceptos(fuente)
        return borrados

    def add_documents(self, documents: Sequence[FragmentoRecuperado], ids: Sequence[str]) -> None:
        vectores = self._embeddings.embed_documents([d.texto for d in documents])
        fragmentos = [
            Fragmento(chunk_id=i, texto=d.texto, metadatos=dict(d.metadatos))
            for d, i in zip(documents, ids, strict=True)
        ]
        self.indice.anadir(fragmentos, vectores)
        # A law that came by blocks also stores every version of its articles, to be read by identifier and date.
        from asistente.conocimiento.processor.strategies.boe_bloques import BoeBloquesStrategy

        for fuente in dict.fromkeys(
            str(d.metadatos.get("source_file"))
            for d in documents
            if d.metadatos.get("_strategy_id") == BoeBloquesStrategy.strategy_id and d.metadatos.get("source_file")
        ):
            guardar_preceptos_de(self.indice, fuente)


def preceptos_de(fuente: str | Path) -> list[Precepto]:
    """Every version of every article and provision of the law in ``fuente`` (a ``<ID>.boe.xml`` file)."""
    from asistente.conocimiento.boe.bloques import remisiones
    from asistente.conocimiento.processor.strategies.boe_bloques import cargar_norma

    norma = cargar_norma(fuente)
    return [
        Precepto(
            norma_id=norma.boe_id,
            bloque_id=bloque.bloque_id,
            titulo=bloque.titulo,
            rotulo=version.rotulo,
            texto=version.texto,
            vigente_desde=version.vigente_desde,
            vigente_hasta=version.vigente_hasta,
            numero=bloque.numero,
            sigla=norma.sigla,
            norma_titulo=norma.titulo,
            jerarquia=bloque.jerarquia,
            orden=bloque.orden,
            id_norma=version.id_norma,
            notas=version.notas,
            remisiones=tuple((r.articulo, r.norma) for r in remisiones(version.texto, propio=bloque.numero)),
            sin_contenido=version.sin_contenido,
        )
        for bloque in norma.preceptos
        for version in bloque.versiones
    ]


def guardar_preceptos_de(indice: IndiceHibrido, fuente: str | Path) -> int:
    """(Re)write the articles of ``fuente`` in ``indice``. Returns how many versions it stored; 0 when the file
    cannot be read (the fragments are indexed all the same: reading by identifier is what is lost)."""
    try:
        preceptos = preceptos_de(fuente)
    except OSError, ValueError:
        return 0
    indice.guardar_preceptos(str(fuente), preceptos)
    return len(preceptos)


def leer_fragmentos(directorio: str | Path) -> list[FragmentoRecuperado]:
    """Todos los fragmentos de ``<directorio>/indice.sqlite``, en orden de alta, sin vectores.

    ``FileNotFoundError`` si no hay índice: abrirlo en solo lectura no crea un fichero vacío.
    """
    ruta = ruta_indice(directorio)
    if not ruta.exists():
        raise FileNotFoundError(ruta)
    with IndiceHibrido(ruta, solo_lectura=True) as indice:
        return [FragmentoRecuperado(texto=f.texto, metadatos=f.metadatos, id=f.chunk_id) for f, _ in indice.recorrer()]

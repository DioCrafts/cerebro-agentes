"""Copia un índice FAISS de LangChain a ``IndiceHibrido`` sin recalcular embeddings.

Lee el índice a través del adaptador ``faiss_legado`` (el pickle del docstore se abre una sola vez y solo
con ``FAISS_TRUSTED_INDEX=true``). Los vectores salen de ``index.reconstruct_n``; los textos y metadatos, del
docstore; el ``chunk_id`` es el id que la ingesta le dio en FAISS. Se borra con el adaptador cuando el
índice SQLite sea el único.

Tras copiar comprueba que los recuentos cuadran y que, para una muestra de fragmentos, los vecinos que
devuelve FAISS y los de ``sqlite-vec`` coinciden.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path
import random
from typing import TYPE_CHECKING

from asistente.conocimiento.indice.almacen import Fragmento, IndiceHibrido

if TYPE_CHECKING:
    from asistente.conocimiento.indice.faiss_legado import AlmacenFaissLegado

logger = logging.getLogger(__name__)

LOTE = 1000


@dataclass
class InformeMigracion:
    origen: str
    destino: str
    vectores_faiss: int = 0
    documentos_docstore: int = 0
    fragmentos_sqlite: int = 0
    vectores_sqlite: int = 0
    dimension: int = 0
    muestra: int = 0
    vecinos_k: int = 0
    solapamiento_medio: float = 0.0
    primero_igual: float = 0.0
    problemas: list[str] = field(default_factory=list)

    @property
    def correcto(self) -> bool:
        return not self.problemas

    def texto(self) -> str:
        lineas = [
            f"Origen FAISS: {self.origen}",
            f"Destino SQLite: {self.destino}",
            f"Vectores FAISS: {self.vectores_faiss}  documentos en el docstore: {self.documentos_docstore}",
            f"Fragmentos SQLite: {self.fragmentos_sqlite}  vectores SQLite: {self.vectores_sqlite}"
            f"  dimensión: {self.dimension}",
            f"Muestra de vecinos: {self.muestra} consultas, k={self.vecinos_k}, "
            f"solapamiento medio {self.solapamiento_medio:.3f}, primer vecino igual {self.primero_igual:.3f}",
        ]
        lineas += [f"PROBLEMA: {p}" for p in self.problemas] or ["Migración verificada."]
        return "\n".join(lineas)


def cargar_faiss(directorio: str | Path, *, confiar: bool) -> AlmacenFaissLegado:
    """Abre el índice FAISS sin embedder: la migración nunca calcula vectores."""
    from asistente.conocimiento.indice.faiss_legado import AlmacenFaissLegado

    return AlmacenFaissLegado.cargar(directorio, confiar=confiar)


def migrar(
    vectorstore: AlmacenFaissLegado,
    destino: str | Path,
    *,
    origen: str = "",
    muestra: int = 50,
    vecinos_k: int = 10,
    solapamiento_minimo: float = 0.9,
    semilla: int = 0,
) -> InformeMigracion:
    """Copia ``vectorstore`` (índice FAISS ya abierto) a un fichero SQLite nuevo y lo verifica."""
    destino = Path(destino)
    if destino.exists():
        raise FileExistsError(f"{destino} ya existe: la migración escribe en un fichero nuevo.")

    indice_faiss = vectorstore.index
    mapa = vectorstore.index_to_docstore_id
    total = int(indice_faiss.ntotal)
    informe = InformeMigracion(origen=origen, destino=str(destino), vectores_faiss=total, dimension=indice_faiss.d)
    informe.documentos_docstore = vectorstore.num_documentos()

    with IndiceHibrido(destino) as indice:
        for inicio in range(0, total, LOTE):
            n = min(LOTE, total - inicio)
            vectores = indice_faiss.reconstruct_n(inicio, n)
            fragmentos: list[Fragmento] = []
            filas: list[Sequence[float]] = []
            for desplazamiento in range(n):
                posicion = inicio + desplazamiento
                chunk_id = mapa.get(posicion)
                documento = None if chunk_id is None else vectorstore.fragmento(chunk_id)
                if chunk_id is None or documento is None:
                    informe.problemas.append(f"El vector {posicion} no tiene documento en el docstore.")
                    continue
                metadatos = dict(documento.metadatos)
                metadatos.setdefault("chunk_id", chunk_id)
                fragmentos.append(Fragmento(chunk_id=chunk_id, texto=documento.texto, metadatos=metadatos))
                filas.append(vectores[desplazamiento])
            indice.anadir(fragmentos, filas)
            logger.info("Migrados %d/%d vectores.", inicio + n, total)

        informe.fragmentos_sqlite = len(indice)
        informe.vectores_sqlite = indice.num_vectores()
        if informe.fragmentos_sqlite != total or informe.vectores_sqlite != total:
            informe.problemas.append(
                f"Recuentos distintos: FAISS {total}, fragmentos SQLite {informe.fragmentos_sqlite}, "
                f"vectores SQLite {informe.vectores_sqlite}."
            )
        _verificar_vecinos(vectorstore, mapa, indice, informe, muestra, vecinos_k, solapamiento_minimo, semilla)
    return informe


def _verificar_vecinos(
    vectorstore: AlmacenFaissLegado,
    mapa: dict[int, str],
    indice: IndiceHibrido,
    informe: InformeMigracion,
    muestra: int,
    vecinos_k: int,
    solapamiento_minimo: float,
    semilla: int,
) -> None:
    import numpy as np

    total = int(vectorstore.index.ntotal)
    if total == 0 or muestra <= 0:
        return
    k = min(vecinos_k, total)
    posiciones = random.Random(semilla).sample(range(total), min(muestra, total))
    solapes: list[float] = []
    primeros = 0
    for posicion in posiciones:
        consulta = np.asarray(vectorstore.index.reconstruct(posicion), dtype="float32").reshape(1, -1)
        _, vecinos = vectorstore.index.search(consulta, k)
        ids_faiss = [mapa[int(i)] for i in vecinos[0] if i >= 0]
        ids_sqlite = [a.fragmento.chunk_id for a in indice.buscar_denso(consulta[0].tolist(), k=k)]
        solapes.append(len(set(ids_faiss) & set(ids_sqlite)) / max(len(ids_faiss), 1))
        primeros += int(bool(ids_faiss) and bool(ids_sqlite) and ids_faiss[0] == ids_sqlite[0])
    informe.muestra = len(posiciones)
    informe.vecinos_k = k
    informe.solapamiento_medio = sum(solapes) / len(solapes)
    informe.primero_igual = primeros / len(posiciones)
    if informe.solapamiento_medio < solapamiento_minimo:
        informe.problemas.append(
            f"Los vecinos de FAISS y sqlite-vec solapan {informe.solapamiento_medio:.3f} "
            f"(mínimo {solapamiento_minimo}). ¿Vectores sin normalizar o métrica distinta?"
        )

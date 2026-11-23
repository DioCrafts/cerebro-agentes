"""Adaptador del índice FAISS heredado (``index.faiss`` + ``index.pkl`` en el formato de LangChain).

Es el único fichero del proyecto que importa LangChain. Todo lo demás trabaja con
:class:`~asistente.conocimiento.fragmento.FragmentoRecuperado` y con el protocolo
:class:`~asistente.conocimiento.llm.embeddings.Embedder`; aquí se convierte en la frontera, en los dos sentidos.

Lo usan, mientras ``INDICE_BACKEND`` siga en ``faiss``:

* la búsqueda del agente (``nucleo/agent/search.py``): :meth:`AlmacenFaissLegado.cargar` y ``similarity_search``;
* la ingesta (``processor/document_loader.py``): ``desde_fragmentos``, ``add_documents``, ``delete`` y
  ``save_local``, la misma interfaz que ``indice.ingesta.EscritorSqlite``;
* la migración a SQLite (``indice/migracion.py``): el índice FAISS en bruto y el docstore;
* quien necesita los textos del corpus sin vectores (grafo de doctrina, benchmark): :func:`leer_fragmentos`.

Abrir el índice deserializa un pickle, que puede ejecutar código: solo se hace con ``confiar=True``, que los
llamantes sacan de ``FAISS_TRUSTED_INDEX`` (docs/seguridad.md, S-17).

Cuando el benchmark de recuperación valide ``INDICE_BACKEND=sqlite``, este fichero se borra junto con
``faiss-cpu`` y ``langchain-community`` (docs/cerebro/benchmark-recuperacion.md, «Retirar FAISS»).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
import pickle  # noqa: S403 - solo para índices que el operador declara suyos (confiar=True)
from typing import TYPE_CHECKING, Any

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from asistente.conocimiento.fragmento import FragmentoRecuperado

if TYPE_CHECKING:
    from asistente.conocimiento.llm.embeddings import Embedder

_AVISO_PICKLE = (
    "El índice FAISS de {directorio} se guarda con pickle y cargarlo puede ejecutar código. "
    "Pon FAISS_TRUSTED_INDEX=true solo si lo construiste tú."
)


class _EmbedderLangChain(Embeddings):
    """Un ``Embedder`` del proyecto visto como ``Embeddings`` de LangChain, que es lo que FAISS espera."""

    def __init__(self, embedder: Embedder | None) -> None:
        self._embedder = embedder

    def _propio(self) -> Embedder:
        if self._embedder is None:
            raise RuntimeError("Este índice FAISS se abrió sin embedder: no puede calcular vectores.")
        return self._embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._propio().embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._propio().embed_query(text)


def _a_fragmento(documento: Any, chunk_id: str | None = None) -> FragmentoRecuperado:
    metadatos = dict(getattr(documento, "metadata", None) or {})
    id_ = chunk_id or getattr(documento, "id", None) or metadatos.get("chunk_id")
    return FragmentoRecuperado(texto=documento.page_content, metadatos=metadatos, id=id_)


def _a_documento(fragmento: FragmentoRecuperado) -> Document:
    return Document(page_content=fragmento.texto, metadata=dict(fragmento.metadatos), id=fragmento.id)


def _exigir_confianza(directorio: str | Path, confiar: bool) -> None:
    if not confiar:
        raise RuntimeError(_AVISO_PICKLE.format(directorio=directorio))


class AlmacenFaissLegado:
    """Envoltorio de ``langchain_community.vectorstores.FAISS`` que habla en ``FragmentoRecuperado``."""

    def __init__(self, faiss: FAISS) -> None:
        self._faiss = faiss

    @classmethod
    def cargar(cls, directorio: str | Path, embedder: Embedder | None = None, *, confiar: bool) -> AlmacenFaissLegado:
        """Abre ``directorio``. Sin ``embedder`` se puede leer y migrar, pero no buscar por texto ni añadir."""
        _exigir_confianza(directorio, confiar)
        faiss = FAISS.load_local(str(directorio), _EmbedderLangChain(embedder), allow_dangerous_deserialization=True)
        return cls(faiss)

    @classmethod
    def desde_fragmentos(
        cls, fragmentos: Sequence[FragmentoRecuperado], embedder: Embedder, ids: Sequence[str]
    ) -> AlmacenFaissLegado:
        """Índice nuevo con ``fragmentos`` (calcula sus vectores con ``embedder``)."""
        documentos = [_a_documento(f) for f in fragmentos]
        return cls(FAISS.from_documents(documentos, _EmbedderLangChain(embedder), ids=list(ids)))

    # ------------------------------------------------------------- búsqueda

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        filter: Callable[[dict[str, Any]], bool] | None = None,  # noqa: A002 - nombre de la API de FAISS
        fetch_k: int = 20,
    ) -> list[FragmentoRecuperado]:
        """Los ``k`` vecinos de ``query``; con ``filter``, filtra los ``fetch_k`` más cercanos (como FAISS)."""
        documentos = self._faiss.similarity_search(query, k=k, filter=filter, fetch_k=fetch_k)
        return [_a_fragmento(d) for d in documentos]

    # --------------------------------------------------------------- ingesta

    def add_documents(self, fragmentos: Sequence[FragmentoRecuperado], ids: Sequence[str]) -> None:
        self._faiss.add_documents([_a_documento(f) for f in fragmentos], ids=list(ids))

    def delete(self, ids: Iterable[str]) -> None:
        self._faiss.delete(ids=list(ids))

    def save_local(self, directorio: str | Path) -> None:
        self._faiss.save_local(str(directorio))

    # ------------------------------------------------------------- migración

    @property
    def index(self) -> Any:
        """El índice de ``faiss`` en bruto (``ntotal``, ``d``, ``reconstruct_n``, ``search``)."""
        return self._faiss.index

    @property
    def index_to_docstore_id(self) -> dict[int, str]:
        return dict(self._faiss.index_to_docstore_id)

    def num_documentos(self) -> int:
        return len(getattr(self._faiss.docstore, "_dict", {}) or {})

    def fragmento(self, chunk_id: str) -> FragmentoRecuperado | None:
        documento = self._faiss.docstore.search(chunk_id)
        if documento is None or isinstance(documento, str):  # el docstore devuelve un str si no lo encuentra
            return None
        return _a_fragmento(documento, chunk_id)


def leer_fragmentos(directorio: str | Path, *, confiar: bool) -> list[FragmentoRecuperado]:
    """Los fragmentos del docstore (``index.pkl``), sin abrir el índice de vectores ni cargar el embedder."""
    _exigir_confianza(directorio, confiar)
    ruta = Path(directorio) / "index.pkl"
    with ruta.open("rb") as fichero:
        docstore, _ = pickle.load(fichero)  # noqa: S301 - confiar=True: índice del propio operador
    return [_a_fragmento(documento, str(chunk_id)) for chunk_id, documento in docstore._dict.items()]

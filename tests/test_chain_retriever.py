from unittest.mock import MagicMock

from langchain_core.retrievers import BaseRetriever
import pytest

from asistente.conocimiento.llm import chain


def test_create_retriever_raises_when_untrusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chain, "FAISS_TRUSTED_INDEX", False)

    with pytest.raises(RuntimeError, match="Refusing to load FAISS index"):
        chain._create_retriever(embeddings=MagicMock(), llm=MagicMock())


def test_create_retriever_builds_compressed_retriever(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_faiss = MagicMock()
    fake_base_retriever = MagicMock(name="base_retriever")
    fake_faiss.as_retriever.return_value = fake_base_retriever

    fake_multi_query_retriever = MagicMock(name="multi_query_retriever")
    fake_embeddings_filter = MagicMock(name="embeddings_filter")
    fake_compressed_retriever = MagicMock(name="compressed_retriever", spec=BaseRetriever)

    monkeypatch.setattr(chain, "FAISS_TRUSTED_INDEX", True)
    # This test pins the dense-only construction (k=TOP_K, a single
    # ContextualCompressionRetriever). Pin both feature flags so the default
    # reranker-on / BM25 path (which adds stages and widens k) doesn't change
    # what is asserted below.
    monkeypatch.setattr(chain, "RERANKER_ENABLED", False)
    monkeypatch.setattr(chain, "BM25_ENABLED", False)
    monkeypatch.setattr(chain.FAISS, "load_local", MagicMock(return_value=fake_faiss))
    monkeypatch.setattr(chain.MultiQueryRetriever, "from_llm", MagicMock(return_value=fake_multi_query_retriever))
    monkeypatch.setattr(chain, "EmbeddingsFilter", MagicMock(return_value=fake_embeddings_filter))
    monkeypatch.setattr(chain, "ContextualCompressionRetriever", MagicMock(return_value=fake_compressed_retriever))

    result = chain._create_retriever(embeddings=MagicMock(), llm=MagicMock())

    chain.FAISS.load_local.assert_called_once()
    fake_faiss.as_retriever.assert_called_once_with(search_kwargs={"k": chain.TOP_K})
    chain.MultiQueryRetriever.from_llm.assert_called_once()
    chain.EmbeddingsFilter.assert_called_once()
    chain.ContextualCompressionRetriever.assert_called_once_with(
        base_retriever=fake_multi_query_retriever,
        base_compressor=fake_embeddings_filter,
    )
    assert hasattr(result, "inner")
    assert result.inner is fake_compressed_retriever


@pytest.mark.parametrize("flag", ["pageindex", "hibrido"])
def test_create_retriever_aplica_flag_fiscal_recuperador(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    from langchain_core.documents import Document

    from asistente.conocimiento.llm import arbol

    fake_faiss = MagicMock()
    fake_faiss.docstore._dict = {
        "1": Document(page_content="x", metadata={"source_file": "boe/LIVA.xml", "headings": ["Artículo 99. Deducción"]})
    }
    fake_compressed = MagicMock(name="compressed_retriever", spec=BaseRetriever)
    monkeypatch.setattr(chain, "FAISS_TRUSTED_INDEX", True)
    monkeypatch.setattr(chain, "RERANKER_ENABLED", False)
    monkeypatch.setattr(chain, "BM25_ENABLED", False)
    monkeypatch.setattr(chain, "FISCAL_RECUPERADOR", flag)
    monkeypatch.setattr(chain.FAISS, "load_local", MagicMock(return_value=fake_faiss))
    monkeypatch.setattr(chain.MultiQueryRetriever, "from_llm", MagicMock())
    monkeypatch.setattr(chain, "EmbeddingsFilter", MagicMock())
    monkeypatch.setattr(chain, "ContextualCompressionRetriever", MagicMock(return_value=fake_compressed))
    envolver = MagicMock(return_value=fake_compressed)
    monkeypatch.setattr(arbol, "envolver_recuperador", envolver)

    chain._create_retriever(embeddings=MagicMock(), llm=MagicMock())

    envolver.assert_called_once()
    nombre, base, corpus, selector = envolver.call_args.args
    assert nombre == flag and base is fake_compressed and len(corpus) == 1
    assert isinstance(selector, arbol.SelectorLLM)


def test_create_retriever_por_defecto_no_envuelve(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.conocimiento.llm import arbol
    from asistente.plataforma import settings

    assert settings.FISCAL_RECUPERADOR == "faiss"
    monkeypatch.setattr(chain, "FAISS_TRUSTED_INDEX", True)
    monkeypatch.setattr(chain, "RERANKER_ENABLED", False)
    monkeypatch.setattr(chain, "BM25_ENABLED", False)
    monkeypatch.setattr(chain.FAISS, "load_local", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(chain.MultiQueryRetriever, "from_llm", MagicMock())
    monkeypatch.setattr(chain, "EmbeddingsFilter", MagicMock())
    monkeypatch.setattr(chain, "ContextualCompressionRetriever", MagicMock(return_value=MagicMock(spec=BaseRetriever)))
    envolver = MagicMock()
    monkeypatch.setattr(arbol, "envolver_recuperador", envolver)
    chain._create_retriever(embeddings=MagicMock(), llm=MagicMock())
    envolver.assert_not_called()

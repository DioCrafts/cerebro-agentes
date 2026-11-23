"""IndiceHibrido (sqlite-vec + FTS5) y conversor FAISS → SQLite, con índices diminutos y vectores aleatorios."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sqlite_vec")

from asistente.conocimiento.indice import Fragmento, IndiceHibrido, consulta_fts, fusion_rrf  # noqa: E402

DIM = 8


def _unit(rng, n: int = 1):
    v = rng.standard_normal((n, DIM)).astype("float32")
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture
def indice(tmp_path):
    with IndiceHibrido(tmp_path / "indice.sqlite") as ind:
        yield ind


def _frags():
    return [
        Fragmento("a-0", "Modelo 721. Declaración de monedas virtuales en el extranjero.", {"source_file": "a.md"}),
        Fragmento("a-1", "El RÉGIMEN de impatriados del artículo 93 LIRPF.", {"source_file": "a.md"}),
        Fragmento(
            "b-0", "Ley 7/2012 de prevención del fraude fiscal.", {"source_file": "b.md", "covered_tributos": ["IRPF"]}
        ),
    ]


def test_alta_busqueda_densa_y_metricas(indice):
    rng = np.random.default_rng(0)
    vectores = _unit(rng, 3)
    indice.anadir(_frags(), vectores)
    assert len(indice) == 3 and indice.num_vectores() == 3 and indice.dimension == DIM
    aciertos = indice.buscar_denso(vectores[1], k=2)
    assert aciertos[0].fragmento.chunk_id == "a-1"
    assert aciertos[0].puntuacion == pytest.approx(0.0, abs=1e-5)  # distancia coseno consigo mismo
    assert aciertos[0].fragmento.metadatos == {"source_file": "a.md"}
    assert indice.vector("a-1") == pytest.approx(vectores[1].tolist(), abs=1e-6)


def test_lexico_separa_puntuacion_y_quita_acentos(indice):
    indice.anadir(_frags(), _unit(np.random.default_rng(1), 3))
    assert [a.fragmento.chunk_id for a in indice.buscar_lexico("modelo 721", k=3)][0] == "a-0"
    assert [a.fragmento.chunk_id for a in indice.buscar_lexico("regimen", k=3)] == ["a-1"]
    assert [a.fragmento.chunk_id for a in indice.buscar_lexico("7/2012", k=3)][0] == "b-0"
    # Los operadores de FTS5 y las comillas del usuario no rompen la consulta.
    assert indice.buscar_lexico('NEAR("721" AND *', k=3)[0].fragmento.chunk_id == "a-0"
    assert indice.buscar_lexico("...", k=3) == []


def test_consulta_fts_y_rrf():
    assert consulta_fts('Modelo 721. "AND"') == '"modelo" OR "721" OR "and"'
    assert consulta_fts("¿?") is None
    assert [i for i, _ in fusion_rrf([["x", "y"], ["y", "z"]])] == ["y", "x", "z"]


def test_hibrido_rescata_coincidencia_exacta(indice):
    rng = np.random.default_rng(2)
    vectores = _unit(rng, 3)
    indice.anadir(_frags(), vectores)
    # El vector apunta a b-0, pero el texto nombra el modelo 721: el RRF debe traer los dos arriba.
    ids = [a.fragmento.chunk_id for a in indice.buscar_hibrido("modelo 721", vectores[2], k=2)]
    assert set(ids) == {"a-0", "b-0"}


def test_reemplazo_bajas_y_transaccion(indice):
    rng = np.random.default_rng(3)
    indice.anadir(_frags(), _unit(rng, 3))
    # Reemplazar a.md deja solo sus fragmentos nuevos, en FTS5 y en vec0.
    indice.reemplazar_fuente("a.md", [Fragmento("a-0", "Texto nuevo sobre el modelo 720.")], _unit(rng, 1))
    assert len(indice) == 2 and indice.num_vectores() == 2
    assert indice.buscar_lexico("721", k=5) == []
    assert indice.buscar_lexico("720", k=5)[0].fragmento.chunk_id == "a-0"
    # Un chunk_id repetido reemplaza, no duplica.
    indice.anadir([Fragmento("b-0", "Ley 7/2012 revisada.", {"source_file": "b.md"})], _unit(rng, 1))
    assert len(indice) == 2
    assert indice.borrar(["b-0", "inexistente"]) == 1
    assert len(indice) == 1 and indice.num_vectores() == 1 and indice.buscar_lexico("ley", k=5) == []
    # Una dimensión distinta aborta la transacción entera: nada a medias.
    with pytest.raises(ValueError, match="dimensión"):
        indice.aplicar(borrar=["a-0"], fragmentos=[Fragmento("c-0", "x")], vectores=[[1.0, 0.0]])
    assert len(indice) == 1
    assert indice.borrar_fuente("a.md") == 1 and len(indice) == 0


def test_persistencia_y_lectura_concurrente(tmp_path):
    ruta = tmp_path / "indice.sqlite"
    vectores = _unit(np.random.default_rng(4), 3)
    with IndiceHibrido(ruta) as ind:
        ind.anadir(_frags(), vectores)
    with IndiceHibrido(ruta, solo_lectura=True) as ind:
        assert ind.buscar_denso(vectores[0], k=1)[0].fragmento.chunk_id == "a-0"
        assert ind._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_escritor_de_ingesta(tmp_path):
    from asistente.conocimiento.indice.ingesta import EscritorSqlite, borrar_indice, ruta_indice

    class _Doc:
        def __init__(self, texto, meta):
            self.page_content, self.metadata = texto, meta

    class _Emb:
        def embed_documents(self, textos):
            return _unit(np.random.default_rng(len(textos)), len(textos)).tolist()

    escritor = EscritorSqlite(tmp_path, _Emb())
    escritor.add_documents([_Doc("uno", {"source_file": "x.md"}), _Doc("dos", {})], ids=["x-0", "x-1"])
    assert escritor.delete(ids=["x-0"]) == 1
    assert len(escritor.indice) == 1
    escritor.indice.cerrar()
    borrar_indice(tmp_path)
    assert not ruta_indice(tmp_path).exists()


# --------------------------------------------------------------- conversor


@pytest.fixture
def faiss_diminuto(tmp_path):
    faiss = pytest.importorskip("faiss")
    pytest.importorskip("langchain_community")
    from langchain_community.docstore.in_memory import InMemoryDocstore
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    from asistente.conocimiento.indice.migracion import _SinEmbedder

    n = 60
    vectores = _unit(np.random.default_rng(5), n)
    index = faiss.IndexFlatL2(DIM)
    index.add(vectores)
    ids = [f"f{i // 10}-{i % 10}" for i in range(n)]
    docstore = InMemoryDocstore(
        {
            cid: Document(
                page_content=f"Fragmento {i} del artículo {i}.",
                metadata={"source_file": f"f{i // 10}.md", "chunk_id": cid},
            )
            for i, cid in enumerate(ids)
        }
    )
    store = FAISS(_SinEmbedder(), index, docstore, dict(enumerate(ids)))
    directorio = tmp_path / "faiss"
    store.save_local(str(directorio))
    return directorio, vectores, ids


def test_migracion_copia_y_verifica(faiss_diminuto, tmp_path):
    from asistente.conocimiento.indice.migracion import cargar_faiss, migrar

    directorio, vectores, ids = faiss_diminuto
    with pytest.raises(RuntimeError, match="FAISS_TRUSTED_INDEX"):
        cargar_faiss(directorio, confiar=False)
    destino = tmp_path / "indice.sqlite"
    informe = migrar(cargar_faiss(directorio, confiar=True), destino, origen=str(directorio), muestra=20, vecinos_k=5)
    assert informe.correcto, informe.texto()
    assert informe.fragmentos_sqlite == informe.vectores_sqlite == informe.vectores_faiss == 60
    assert informe.solapamiento_medio == pytest.approx(1.0) and informe.primero_igual == pytest.approx(1.0)
    assert "Migración verificada." in informe.texto()
    with IndiceHibrido(destino) as ind:
        assert ind.vector(ids[7]) == pytest.approx(vectores[7].tolist(), abs=1e-6)
        assert ind.buscar_lexico("artículo 37", k=1)[0].fragmento.chunk_id == ids[37]
        assert ind.buscar_lexico("artículo 37", k=1)[0].fragmento.metadatos["source_file"] == "f3.md"
    with pytest.raises(FileExistsError):
        migrar(cargar_faiss(directorio, confiar=True), destino)


def test_cli_migrar(faiss_diminuto, tmp_path, monkeypatch, capsys):
    from asistente.plataforma import settings
    from asistente.plataforma.cli import indice as cli

    directorio, _, _ = faiss_diminuto
    destino = tmp_path / "cli.sqlite"
    monkeypatch.setattr(settings, "FAISS_TRUSTED_INDEX", True)
    assert cli.main(["migrar", "--desde", str(directorio), "--a", str(destino), "--muestra", "5"]) == 0
    assert "Fragmentos SQLite: 60" in capsys.readouterr().out


# ------------------------------------------------------------- buscador


def test_buscador_sqlite_filtros_e_hibrido(tmp_path, monkeypatch):
    from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher
    from asistente.conocimiento.llm import embeddings as emb
    from asistente.nucleo.agent.search import FaissCorpusSearcher, SearchFilters, SinCorpus
    from asistente.plataforma import settings

    vectores = _unit(np.random.default_rng(6), 3)

    class _Emb:
        def embed_query(self, texto):
            return vectores[2].tolist()  # siempre apunta a b-0

    ruta = tmp_path / "indice.sqlite"
    monkeypatch.setattr(settings, "INDICE_BACKEND", "sqlite")
    monkeypatch.setattr(settings, "INDICE_SQLITE_PATH", str(ruta))
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(settings, "BM25_ENABLED", True)
    monkeypatch.setattr(emb, "create_embeddings", lambda: _Emb())
    assert isinstance(FaissCorpusSearcher.from_settings(), SinCorpus)

    with IndiceHibrido(ruta) as ind:
        ind.anadir(_frags(), vectores)
    buscador = FaissCorpusSearcher.from_settings()
    assert isinstance(buscador, SqliteCorpusSearcher)
    docs = buscador.search("modelo 721", k=2, filters=SearchFilters())
    assert {d.metadata["source_file"] for d in docs} == {"a.md", "b.md"}
    assert docs[0].id in {"a-0", "b-0"}
    filtrados = buscador.search("modelo 721", k=3, filters=SearchFilters(tributo="IVA"))
    assert "b-0" not in {d.id for d in filtrados}  # b-0 declara IRPF

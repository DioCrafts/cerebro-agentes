"""El índice por artículos: versiones con su vigencia, raíces léxicas, actualización del esquema y la búsqueda
en una fecha. Con una ley inventada (``tests/fixtures/boe_bloques``) y vectores deterministas."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3

import pytest

pytest.importorskip("sqlite_vec")
pytest.importorskip("lxml")

from asistente.conocimiento.fragmento import FragmentoRecuperado  # noqa: E402
from asistente.conocimiento.indice import Fragmento, IndiceHibrido, consulta_fts  # noqa: E402
from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher  # noqa: E402
from asistente.conocimiento.indice.ingesta import EscritorSqlite  # noqa: E402
from asistente.conocimiento.indice.lexico import lematizar, terminos  # noqa: E402
from asistente.conocimiento.processor.document_loader import descubrir_ficheros  # noqa: E402
from asistente.conocimiento.processor.strategies.boe_bloques import cargar_norma, fragmentos_de  # noqa: E402
from asistente.nucleo.agent.search import SearchFilters  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "boe_bloques"
DIM = 16


class EmbeddingsDePrueba:
    """Bolsa de raíces por hashing: dos textos con las mismas palabras quedan cerca. Determinista."""

    def _uno(self, texto: str) -> list[float]:
        v = [0.0] * DIM
        for t in terminos(texto):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % DIM] += 1.0
        norma = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norma for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._uno(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._uno(text)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    destino = tmp_path / "corpus"
    shutil.copytree(FIXTURES, destino)
    return destino


@pytest.fixture
def escritor(tmp_path: Path, corpus: Path) -> EscritorSqlite:
    fuente = str(corpus / "BOE-A-2099-1.boe.xml")
    docs = fragmentos_de(cargar_norma(fuente), fuente)
    escritor = EscritorSqlite(tmp_path / "indice", EmbeddingsDePrueba())
    escritor.add_documents(docs, ids=[d.metadatos["chunk_id"] for d in docs])
    return escritor


@pytest.fixture
def buscador(escritor: EscritorSqlite) -> SqliteCorpusSearcher:
    return SqliteCorpusSearcher(escritor.indice, EmbeddingsDePrueba(), hibrido=True)


# --------------------------------------------------------------------------- raíces léxicas


def test_la_busqueda_lexica_casa_plurales_y_derivados(tmp_path: Path) -> None:
    assert lematizar("deducciones por arrendamientos") == lematizar("deducción por arrendamiento")
    assert lematizar("Modelo 303, artículo 68").split()[1] == "303"  # numbers stay exact
    with IndiceHibrido(tmp_path / "i.sqlite") as indice:
        indice.anadir(
            [
                Fragmento("a", "La deducción por arrendamiento de vivienda habitual.", {}),
                Fragmento("b", "El devengo del impuesto se produce el 31 de diciembre.", {}),
            ],
            EmbeddingsDePrueba().embed_documents(["a", "b"]),
        )
        assert [a.fragmento.chunk_id for a in indice.buscar_lexico("deducciones arrendamientos", k=5)] == ["a"]
        assert [a.fragmento.chunk_id for a in indice.buscar_lexico("¿cuándo se devengan los impuestos?", k=5)] == ["b"]
    assert consulta_fts("deducciones", lematizado=True) == '"deduccion"'
    assert consulta_fts("deducciones") == '"deducciones"'


def test_un_indice_del_esquema_1_se_actualiza_al_abrirlo_para_escribir(tmp_path: Path) -> None:
    import sqlite_vec

    ruta = tmp_path / "viejo.sqlite"
    conn = sqlite3.connect(ruta)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.executescript(
        """
        CREATE TABLE indice_meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
        INSERT INTO indice_meta VALUES ('esquema', '1'), ('metrica', 'cosine');
        CREATE TABLE fragmentos (rowid INTEGER PRIMARY KEY, chunk_id TEXT NOT NULL UNIQUE, fuente TEXT,
                                 texto TEXT NOT NULL, metadatos TEXT NOT NULL DEFAULT '{}');
        CREATE VIRTUAL TABLE fts_fragmentos USING fts5(texto, content='fragmentos', content_rowid='rowid',
                                                       tokenize='unicode61 remove_diacritics 2');
        INSERT INTO fragmentos(rowid, chunk_id, texto) VALUES (1, 'v-0', 'Las deducciones autonómicas por alquiler.');
        INSERT INTO fts_fragmentos(rowid, texto) VALUES (1, 'Las deducciones autonómicas por alquiler.');
        """
    )
    conn.commit()
    conn.close()

    # Read-only: still schema 1, queried by words as before (a stem would not match).
    with IndiceHibrido(ruta, solo_lectura=True) as viejo:
        assert [a.fragmento.chunk_id for a in viejo.buscar_lexico("deducciones", k=3)] == ["v-0"]
        assert viejo.buscar_lexico("deducción", k=3) == []
        assert viejo.normas() == []  # no articles table yet: empty, not an error

    with IndiceHibrido(ruta) as nuevo:
        assert nuevo._meta("esquema") == "2"
        assert [a.fragmento.chunk_id for a in nuevo.buscar_lexico("deducción autonómica", k=3)] == ["v-0"]
        # Deleting still works on the rebuilt lexical table.
        assert nuevo.borrar(["v-0"]) == 1 and nuevo.buscar_lexico("deducciones", k=3) == []


# --------------------------------------------------------------------------- preceptos


def test_la_ingesta_guarda_todas_las_versiones_de_cada_articulo(escritor: EscritorSqlite) -> None:
    indice = escritor.indice
    (norma,) = indice.normas()
    assert (norma.norma_id, norma.sigla, norma.preceptos) == ("BOE-A-2099-1", "LDP", 4)
    for referencia in ("LDP", "ldp", "BOE-A-2099-1", "Ley 1/2099"):
        assert indice.resolver_norma(referencia) == "BOE-A-2099-1"
    assert indice.resolver_norma("LIRPF") is None

    vigente = indice.precepto("BOE-A-2099-1", numero="1")
    assert vigente is not None and "15 por ciento" in vigente.texto and vigente.vigente
    assert vigente.remisiones == (("3 bis", None), ("20", "Ley 37/1992"))
    assert vigente.notas and vigente.jerarquia == ("TÍTULO I. Deducciones", "CAPÍTULO I. Vivienda")

    antes = indice.precepto("BOE-A-2099-1", numero="1", fecha="2022-12-31")
    assert antes is not None and "10 por ciento" in antes.texto and antes.vigente_hasta == "2023-01-01"
    assert indice.precepto("BOE-A-2099-1", numero="1", fecha="2019-06-01") is None
    assert indice.precepto("BOE-A-2099-1", numero="3 bis", fecha="2023-06-01") is None  # added in 2024
    assert indice.precepto("BOE-A-2099-1", numero="99") is None

    derogado = indice.precepto("BOE-A-2099-1", numero="2")
    assert derogado is not None and derogado.sin_contenido
    por_titulo = indice.precepto("BOE-A-2099-1", titulo="disposicion adicional PRIMERA")
    assert por_titulo is not None and por_titulo.bloque_id == "daprimera"
    assert [p.bloque_id for p in indice.indice_norma("BOE-A-2099-1")] == ["a1", "a2", "a3bis", "daprimera"]


def test_reindexar_no_duplica_y_borrar_la_norma_se_lleva_sus_articulos(escritor: EscritorSqlite, corpus: Path) -> None:
    fuente = str(corpus / "BOE-A-2099-1.boe.xml")
    docs = fragmentos_de(cargar_norma(fuente), fuente)
    antes = len(escritor.indice)
    escritor.add_documents(docs, ids=[d.metadatos["chunk_id"] for d in docs])
    assert len(escritor.indice) == antes
    assert len(escritor.indice.versiones("BOE-A-2099-1", numero="1")) == 2

    escritor.delete([d.metadatos["chunk_id"] for d in docs])
    assert len(escritor.indice) == 0 and escritor.indice.normas() == []


def test_si_estan_la_pagina_y_los_bloques_solo_se_indexan_los_bloques(corpus: Path) -> None:
    (corpus / "BOE-A-2099-1.html").write_text("<html>la misma ley, como página</html>", encoding="utf-8")
    (corpus / "otra.html").write_text("<html>otra cosa</html>", encoding="utf-8")
    (corpus / "suelto.xml").write_text("<x/>", encoding="utf-8")  # not a blocks file: ignored
    nombres = [Path(f).name for f in descubrir_ficheros(corpus)]
    assert nombres == ["BOE-A-2099-1.boe.xml", "otra.html"]


# --------------------------------------------------------------------------- búsqueda y vigencia


def _bloques(docs: list[FragmentoRecuperado]) -> list[str]:
    return [str(d.metadatos.get("bloque_id")) for d in docs]


def test_sin_fecha_solo_devuelve_lo_que_rige_hoy(buscador: SqliteCorpusSearcher) -> None:
    docs = buscador.search("deducción por compra de vivienda", k=10, filters=SearchFilters())
    assert "a2" not in _bloques(docs)  # repealed in 2023
    assert all("vigente_hasta" not in d.metadatos for d in docs)
    alquiler = next(d for d in docs if d.metadatos["bloque_id"] == "a1")
    assert "15 por ciento" in alquiler.texto


def test_con_fecha_devuelve_la_ley_como_estaba_ese_dia(buscador: SqliteCorpusSearcher) -> None:
    docs = buscador.search("deducción alquiler vivienda habitual", k=10, filters=SearchFilters(vigente_en="2022-12-31"))
    por_bloque = {d.metadatos["bloque_id"]: d for d in docs}
    # Article 1 as it was before the 2023 reform, whole and dated.
    assert "10 por ciento" in por_bloque["a1"].texto and "15 por ciento" not in por_bloque["a1"].texto
    assert por_bloque["a1"].metadatos["vigente_hasta"] == "2023-01-01"
    assert _bloques(docs).count("a1") == 1  # its fragments merge into one version
    # The article repealed in 2023 was in force then; the one added in 2024 did not exist.
    assert "5 por ciento" in por_bloque["a2"].texto
    assert "a3bis" not in por_bloque


def test_lectura_por_identificador_desde_el_buscador(buscador: SqliteCorpusSearcher) -> None:
    assert buscador.leer_precepto("LDP", articulo="1").vigente_desde == "2023-01-01"
    assert buscador.leer_precepto("LDP", articulo="1", fecha="2021-01-01").vigente_desde == "2020-01-02"
    assert buscador.leer_precepto("LDP", disposicion="Disposición adicional primera").bloque_id == "daprimera"
    assert buscador.leer_precepto("NO-EXISTE", articulo="1") is None
    assert len(buscador.versiones_de("LDP", articulo="2")) == 2
    assert [p.numero for p in buscador.indice_de_norma("LDP")] == ["1", "2", "3 bis", None]


def test_un_solo_fragmento_por_articulo_entre_los_resultados(escritor: EscritorSqlite, corpus: Path) -> None:
    from asistente.conocimiento.boe.bloques import trocear

    # The same law cut very small: article 1 becomes several fragments that all match the query.
    fuente = str(corpus / "BOE-A-2099-1.boe.xml")
    norma = cargar_norma(fuente)
    a1 = next(b for b in norma.preceptos if b.bloque_id == "a1")
    trozos = trocear(norma, a1, a1.vigente, maximo=120)
    assert len(trozos) >= 3
    extra = [
        FragmentoRecuperado(
            texto=t.texto,
            metadatos={
                "chunk_id": f"BOE-A-2099-1:a1:2023-01-01:x{i}",
                "source_file": fuente,
                "norma_id": "BOE-A-2099-1",
                "bloque_id": "a1",
                "vigente_desde": "2023-01-01",
            },
        )
        for i, t in enumerate(trozos)
    ]
    escritor.add_documents(extra, ids=[d.metadatos["chunk_id"] for d in extra])

    consulta = "deducción por alquiler de la vivienda habitual"
    uno = SqliteCorpusSearcher(escritor.indice, EmbeddingsDePrueba(), hibrido=True)
    docs = uno.search(consulta, k=4, filters=SearchFilters())
    assert _bloques(docs).count("a1") == 1 and len(set(_bloques(docs))) == len(docs)
    assert len(docs) == 3  # the three articles in force: the quota is filled with other articles, not repeats
    assert _bloques(uno.primera_etapa(consulta, 4)).count("a1") == 1

    sin_tope = SqliteCorpusSearcher(escritor.indice, EmbeddingsDePrueba(), hibrido=True, max_por_articulo=0)
    assert _bloques(sin_tope.search(consulta, k=4, filters=SearchFilters())).count("a1") > 1

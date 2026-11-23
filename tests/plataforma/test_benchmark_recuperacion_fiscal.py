"""Benchmark de recuperadores fiscales: reproducible, sin red, con LLM simulado."""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path

from click.testing import CliRunner
from langchain_core.documents import Document
import pytest

from asistente.plataforma.cli import benchmark_recuperacion_fiscal as bench

MANIFIESTO = bench.RAIZ / "eval_data" / "recuperacion" / "citas_indice.json"


def test_consultas_cubren_citas_de_los_evals_fiscales_y_abstenciones() -> None:
    consultas = bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO)
    evals = [json.loads(x) for x in (bench.RAIZ / "eval_data/modulos/fiscal.jsonl").read_text().splitlines() if x]
    ids_evals = {e["id"] for e in evals}
    origenes = {q["origen"].split("#")[1] for q in consultas if "origen" in q}
    assert origenes <= ids_evals and len(origenes) >= 5
    assert len({q["id"] for q in consultas}) == len(consultas)
    estilos = collections.Counter(q.get("estilo", "original") for q in consultas)
    assert len(consultas) >= 100
    assert sum(q["tipo"] == "abstencion" for q in consultas) >= 20
    assert estilos["cuerpo"] >= 30 and estilos["aeat"] >= 15 and estilos["original"] == 32


def test_las_32_originales_tienen_su_cita_en_el_corpus_de_juguete() -> None:
    corpus = bench.cargar_corpus(bench.CORPUS_POR_DEFECTO)
    for q in bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO):
        if q["tipo"] == "cita" and "estilo" not in q:
            assert any(bench.es_cita_correcta(d, q["espera"]) for d in corpus), q["id"]


def test_toda_cita_esperada_existe_en_el_indice_real() -> None:
    """Contra el manifiesto versionado de ``data/faiss`` (si no, el benchmark mediría ruido)."""
    manifiesto = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    faltan = [
        q["id"] for q in bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO)
        if q["tipo"] == "cita" and not bench.cita_en_manifiesto(q["espera"], manifiesto)
    ]
    assert faltan == []


@pytest.mark.skipif(not (bench.RAIZ / "data/faiss/index.pkl").exists() and not os.environ.get("FAISS_INDEX_PATH"),
                    reason="sin índice local")
def test_manifiesto_coincide_con_el_indice_local() -> None:
    ruta = bench.RAIZ / "data/faiss"
    if not (ruta / "index.pkl").exists():
        ruta = Path(os.environ["FAISS_INDEX_PATH"])
    real = bench.manifiesto_de_corpus(bench.cargar_corpus_de_indice(ruta))
    guardado = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    for q in bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO):
        if q["tipo"] == "cita":
            assert bench.cita_en_manifiesto(q["espera"], real), q["id"]
    assert set(guardado["aeat"]) <= set(real["aeat"])


def _doc(source: str, headings: list[str], norma: str) -> Document:
    return Document(page_content=headings[-1], metadata={"source_file": source, "norma": norma, "headings": headings})


def test_puntuacion_de_paginas_aeat_y_de_normas() -> None:
    h = "7991f475f1019abe0f193dc325ae99bf"
    pagina = _doc(f"data/html/2026-11-22/{h}.html", ["Quiero un certificado de mi declaración de renta"], "AEAT")
    otra = _doc("data/html/2026-11-22/" + "0" * 32 + ".html", ["Otra página"], "AEAT")
    assert bench.pagina_aeat(pagina) == h
    assert bench.es_cita_correcta(pagina, {"fuente": "AEAT", "pagina": h})
    assert not bench.es_cita_correcta(otra, {"fuente": "AEAT", "pagina": h})
    # Una página AEAT no responde a una cita de artículo, ni "LIS" acierta con un artículo de la LISD.
    lisd = _doc("BOE-A-1987-28141.html", ["Capítulo V", "Artículo 21. Tarifa."], "LISD")
    assert not bench.es_cita_correcta(lisd, {"norma": "LIS", "articulo": "21"})
    assert bench.es_cita_correcta(lisd, {"norma": "LISD", "articulo": "21"})
    assert bench.es_cita_correcta(lisd, {"norma": "LIS", "articulo": "9", "alternativas": [{"norma": "LISD", "articulo": "21"}]})
    m = bench.manifiesto_de_corpus([pagina, lisd])
    assert m == {"normas": {"LISD": ["21"]}, "aeat": {h: "Quiero un certificado de mi declaración de renta"}}


def test_la_variante_faiss_es_el_buscador_del_agente() -> None:
    """La base es FaissCorpusSearcher: los k vecinos más parecidos, saneados y sin abstención propia."""
    from asistente.nucleo.agent.search import FaissCorpusSearcher

    corpus = bench.cargar_corpus(bench.CORPUS_POR_DEFECTO)
    base = bench.construir_base(corpus, bench.EmbeddingsHash(), k=3, primera_etapa=5)
    assert isinstance(base.buscador, FaissCorpusSearcher)
    consulta = "¿Qué tipo de IVA se aplica al pan?"
    sims = base.buscador._vectorstore.similitudes(consulta)
    docs = base.invoke(consulta)
    assert len(docs) == 3
    assert [sims[corpus.index(d)] for d in docs] == sorted(sims, reverse=True)[:3]


def test_la_variante_faiss_pasa_por_el_reranker() -> None:
    class _Invierte:
        def compress_documents(self, docs, query):
            return list(reversed(docs))

    corpus = bench.cargar_corpus(bench.CORPUS_POR_DEFECTO)
    sin = bench.construir_base(corpus, bench.EmbeddingsHash(), k=5, primera_etapa=5)
    con = bench.construir_base(corpus, bench.EmbeddingsHash(), k=5, primera_etapa=5, reranker=_Invierte())
    consulta = "¿Qué tipo de IVA se aplica al pan?"
    assert con.invoke(consulta) == list(reversed(sin.invoke(consulta)))


def test_wilson() -> None:
    assert bench.wilson(0, 0) == (0.0, 0.0)
    bajo, alto = bench.wilson(6, 6)
    assert alto == 1.0 and 0.5 < bajo < 0.65
    bajo, alto = bench.wilson(50, 100)
    assert bajo < 0.5 < alto


def test_ejecutar_es_reproducible_y_mide_todas_las_metricas() -> None:
    a = bench.ejecutar()
    b = bench.ejecutar()
    resumen = lambda r: {v["variante"]: {k: x for k, x in v["resumen"].items() if "latencia" not in k} for v in r["variantes"]}  # noqa: E731
    assert resumen(a) == resumen(b)
    assert [v["variante"] for v in a["variantes"]] == ["faiss", "pageindex"]
    faiss, pageindex = (v["resumen"] for v in a["variantes"])
    assert faiss["llamadas_llm_por_consulta"] == 0 and faiss["coste_por_consulta"] == 0
    assert pageindex["llamadas_llm_por_consulta"] > 0
    assert set(faiss["por_estilo"]) == {"original", "cuerpo", "aeat"}
    for r in (faiss, pageindex):
        assert 0 <= r["cita_top1"] <= r["cita_en_k"] <= 1
    assert faiss["falsa_abstencion"] == 0  # the agent's searcher never abstains by itself


def test_cli_imprime_tabla_y_guarda_json(tmp_path) -> None:
    salida = tmp_path / "r.json"
    res = CliRunner().invoke(bench.cli, ["--variantes", "faiss,pageindex", "--salida", str(salida)])
    assert res.exit_code == 0, res.output
    assert "| pageindex |" in res.output and "| faiss |" in res.output
    datos = json.loads(salida.read_text())
    assert datos["modo"] == "simulado" and len(datos["variantes"]) == 2 and datos["reranker"] is False

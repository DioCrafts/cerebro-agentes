"""Benchmark de recuperadores fiscales: reproducible, sin red, con LLM simulado."""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from asistente.conocimiento.fragmento import FragmentoRecuperado
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
    # Fiscal questions whose answer is in a norm the corpus does not hold: the hard abstentions.
    casi = [q for q in consultas if q.get("estilo") == "casi"]
    assert len(casi) >= 10 and all(q["tipo"] == "abstencion" and "espera" not in q for q in casi)


def test_las_32_originales_tienen_su_cita_en_el_corpus_de_juguete() -> None:
    corpus = bench.cargar_corpus(bench.CORPUS_POR_DEFECTO)
    for q in bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO):
        if q["tipo"] == "cita" and "estilo" not in q:
            assert any(bench.es_cita_correcta(d, q["espera"]) for d in corpus), q["id"]


def test_toda_cita_esperada_existe_en_el_indice_real() -> None:
    """Contra el manifiesto versionado del índice (si no, el benchmark mediría ruido)."""
    manifiesto = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    faltan = [
        q["id"]
        for q in bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO)
        if q["tipo"] == "cita" and not bench.cita_en_manifiesto(q["espera"], manifiesto)
    ]
    assert faltan == []


INDICE_LOCAL = Path(os.environ.get("INDICE_DIR") or bench.RAIZ / "data/indice")


@pytest.mark.skipif(not (INDICE_LOCAL / "indice.sqlite").exists(), reason="sin índice local")
def test_manifiesto_coincide_con_el_indice_local() -> None:
    real = bench.manifiesto_de_corpus(bench.cargar_corpus_de_indice(INDICE_LOCAL))
    guardado = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    for q in bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO):
        if q["tipo"] == "cita":
            assert bench.cita_en_manifiesto(q["espera"], real), q["id"]
    assert set(guardado["aeat"]) <= set(real["aeat"])


def _doc(source: str, headings: list[str], norma: str) -> FragmentoRecuperado:
    return FragmentoRecuperado(
        texto=headings[-1], metadatos={"source_file": source, "norma": norma, "headings": headings}
    )


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
    assert bench.es_cita_correcta(
        lisd, {"norma": "LIS", "articulo": "9", "alternativas": [{"norma": "LISD", "articulo": "21"}]}
    )
    m = bench.manifiesto_de_corpus([pagina, lisd])
    assert m == {"normas": {"LISD": ["21"]}, "aeat": {h: "Quiero un certificado de mi declaración de renta"}}


def _buscador(*, k: int = 3, hibrido: bool = False, reranker=None):
    corpus = bench.cargar_corpus(bench.CORPUS_POR_DEFECTO)
    emb = bench.EmbeddingsConCache(bench.EmbeddingsHash())
    indice = bench.construir_indice(corpus, emb)
    return (
        corpus,
        indice,
        bench.construir_buscador(indice, emb, k=k, primera_etapa=5, hibrido=hibrido, reranker=reranker),
    )


def test_la_variante_densa_es_el_buscador_del_agente() -> None:
    """SqliteCorpusSearcher sobre el índice en memoria: los k vecinos más parecidos, saneados, sin abstención."""
    from asistente.conocimiento.indice.buscador import SqliteCorpusSearcher

    _, indice, base = _buscador()
    assert isinstance(base.buscador, SqliteCorpusSearcher) and not base.buscador.hibrido
    consulta = "¿Qué tipo de IVA se aplica al pan?"
    docs = base.invoke(consulta)
    vecinos = indice.buscar_denso(bench.EmbeddingsHash().embed_query(consulta), k=3)
    assert [d.id for d in docs] == [a.fragmento.chunk_id for a in vecinos]


def test_la_variante_rrf_fusiona_el_ranking_lexico() -> None:
    _, indice, rrf = _buscador(hibrido=True)
    assert rrf.buscador.hibrido
    consulta = "residencia habitual 183 días"
    from asistente.conocimiento.indice.buscador import PROFUNDIDAD_FUSION

    # The fusion reads each ranking down to PROFUNDIDAD_FUSION, however few results are asked for.
    hondo = PROFUNDIDAD_FUSION
    lexico = [a.fragmento.chunk_id for a in indice.buscar_lexico(consulta, k=hondo)]
    denso = [a.fragmento.chunk_id for a in indice.buscar_denso(bench.EmbeddingsHash().embed_query(consulta), k=hondo)]
    fusion = [d.id for d in rrf.primera_etapa(consulta, 5)]
    assert lexico and denso
    # RRF: the first of each ranking is among the fused candidates, and nothing outside both rankings is.
    assert {lexico[0], denso[0]} <= set(fusion) <= set(lexico) | set(denso)


def test_las_variantes_pasan_por_el_reranker() -> None:
    class _Invierte:
        def reordenar(self, docs, consulta):
            return list(reversed(docs))

    _, _, sin = _buscador(k=5)
    _, _, con = _buscador(k=5, reranker=_Invierte())
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

    def resumen(r: dict) -> dict:
        return {v["variante"]: {k: x for k, x in v["resumen"].items() if "latencia" not in k} for v in r["variantes"]}

    assert resumen(a) == resumen(b)
    assert [v["variante"] for v in a["variantes"]] == ["denso", "rrf", "pageindex"]
    denso, rrf, pageindex = (v["resumen"] for v in a["variantes"])
    assert denso["llamadas_llm_por_consulta"] == 0 and denso["coste_por_consulta"] == 0
    assert pageindex["llamadas_llm_por_consulta"] > 0
    assert set(denso["por_estilo"]) == {"original", "cuerpo", "aeat"}
    for r in (denso, rrf, pageindex):
        assert 0 <= r["cita_top1"] <= r["cita_en_k"] <= 1
    assert denso["falsa_abstencion"] == rrf["falsa_abstencion"] == 0  # the agent's searcher never abstains


def test_auroc() -> None:
    assert bench.auroc([], [0.1]) is None
    assert bench.auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert bench.auroc([0.1], [0.9]) == 0.0
    assert bench.auroc([0.5, 0.5], [0.5]) == 0.5


def test_rango_es_la_posicion_de_la_primera_cita_correcta() -> None:
    corpus, _, base = _buscador()
    consulta = "¿Qué tipo de IVA se aplica al pan?"
    ranking = base.primera_etapa(consulta, len(corpus))
    objetivo = next(i for i, d in enumerate(ranking) if bench.cita_de(d)[1])
    espera = {"norma": str(ranking[objetivo].metadatos["norma"]), "articulo": bench.cita_de(ranking[objetivo])[1]}
    rango = bench.rango_primera_etapa(base, consulta, espera, profundidad=len(corpus))
    assert rango is not None and rango <= objetivo + 1
    assert bench.es_cita_correcta(ranking[rango - 1], espera)
    assert bench.rango_primera_etapa(base, consulta, {"norma": "LIVA", "articulo": "99999"}) is None


def test_diagnostico_de_la_primera_etapa() -> None:
    r = bench.ejecutar(variantes=["denso", "rrf", "pageindex"])
    assert "primera_etapa" not in r["variantes"][2]  # the tree has no first stage
    for variante in r["variantes"][:2]:
        diag = variante["primera_etapa"]
        res = diag["resumen"]
        recall = list(res["recall"].values())
        assert list(res["recall"]) == ["@1", "@3", "@10", "@30", "@50"]
        assert recall == sorted(recall) and all(0 <= x <= 1 for x in recall)
        fallos = sum(not f["en_k"] for f in variante["filas"] if f["tipo"] == "cita")
        assert res["fallos_primera_etapa"] + res["fallos_reordenacion"] == fallos
        for f in diag["filas"]:
            if f.get("fallo") == "reordenacion":
                assert f["rango"] is not None and f["rango"] <= diag["primera_etapa"]
        sep = res["separabilidad_abstencion"]
        assert {"casi", "fuera_de_corpus"} <= set(sep)
        assert sep["casi"]["n"] >= 10 and 0 <= sep["casi"]["denso"] <= 1
        assert sep["casi"]["reranker"] is None  # no reranker in simulated mode
    with pytest.raises(ValueError, match="faiss"):
        bench.ejecutar(variantes=["faiss"])


def test_corpus_del_indice_sqlite_con_sus_vectores(tmp_path) -> None:
    """``--indice`` lee indice.sqlite: solo las normas citadas (y la AEAT), con los vectores guardados."""
    from asistente.conocimiento.indice import Fragmento, IndiceHibrido
    from asistente.conocimiento.indice.ingesta import ruta_indice

    h = "7991f475f1019abe0f193dc325ae99bf"
    filas = [
        Fragmento(
            "l-0",
            "Artículo 9. Contribuyentes.",
            {"headings": ["Artículo 9. Contribuyentes"]},
            "x/BOE-A-2006-20764.html",
        ),
        Fragmento("o-0", "Otra ley.", {"headings": ["Artículo 1. Objeto"]}, "x/BOE-A-1999-1.html"),
        Fragmento("p-0", "Certificado.", {"headings": ["Certificados"]}, f"x/{h}.html"),
    ]
    with IndiceHibrido(ruta_indice(tmp_path)) as ind:
        ind.anadir(filas, [[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]])
    pares = bench.leer_corpus_de_indice(tmp_path)
    assert [(d.id, d.metadatos["norma"]) for d, _ in pares] == [("l-0", "LIRPF"), ("p-0", "AEAT")]
    assert pares[0][1] == pytest.approx([1.0, 0.0])
    assert [d.id for d in bench.cargar_corpus_de_indice(ruta_indice(tmp_path), aeat=False)] == ["l-0"]


def test_cli_imprime_tabla_y_guarda_json(tmp_path) -> None:
    salida = tmp_path / "r.json"
    res = CliRunner().invoke(bench.cli, ["--variantes", "denso,rrf,pageindex", "--salida", str(salida)])
    assert res.exit_code == 0, res.output
    assert "| pageindex |" in res.output and "| denso |" in res.output and "| rrf |" in res.output
    datos = json.loads(salida.read_text())
    assert datos["modo"] == "simulado" and len(datos["variantes"]) == 3 and datos["reranker"] is False
    assert "Primera etapa de `rrf`" in res.output and "| casi |" in res.output
    assert datos["variantes"][0]["primera_etapa"]["profundidad"] == 50
    assert datos["pesos_rrf"] == {"denso": 0.9, "lexico": 0.1}


def test_recalcular_vectores_mide_otro_modelo_y_lo_compara() -> None:
    """``--modelo-embeddings`` recalcula los vectores del corpus con cada modelo y acaba en una tabla comparativa."""
    a = bench.ejecutar(variantes=["denso"], recalcular_vectores=True, modelo_embeddings="data/models/a")
    b = bench.ejecutar(variantes=["denso"], recalcular_vectores=True, modelo_embeddings="data/models/b")
    assert a["vectores_recalculados"] and a["modelo_embeddings"] == "data/models/a"
    assert a["segundos_indice"] >= 0
    tabla = bench.tabla_comparativa([a, b]).splitlines()
    assert tabla[0].startswith("| modelo | variante | cita top-1 |") and "recall denso @30" in tabla[0]
    assert tabla[2].startswith("| a | denso |") and tabla[3].startswith("| b | denso |")
    assert len(tabla) == 4


def test_modelo_embeddings_exige_real(tmp_path) -> None:
    res = CliRunner().invoke(bench.cli, ["--modelo-embeddings", str(tmp_path)])
    assert res.exit_code != 0 and "--real" in res.output

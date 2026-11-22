"""Benchmark de recuperadores fiscales: reproducible, sin red, con LLM simulado."""

from __future__ import annotations

import json

from click.testing import CliRunner
import pytest

pytest.importorskip("faiss")
pytest.importorskip("rank_bm25")

from asistente.plataforma.cli import benchmark_recuperacion_fiscal as bench  # noqa: E402


def test_consultas_cubren_citas_de_los_evals_fiscales_y_abstenciones() -> None:
    consultas = bench.cargar_consultas(bench.CONSULTAS_POR_DEFECTO)
    evals = [json.loads(x) for x in (bench.RAIZ / "eval_data/modulos/fiscal.jsonl").read_text().splitlines() if x]
    ids_evals = {e["id"] for e in evals}
    origenes = {q["origen"].split("#")[1] for q in consultas if "origen" in q}
    assert origenes <= ids_evals and len(origenes) >= 5
    assert sum(q["tipo"] == "abstencion" for q in consultas) >= 5
    corpus = bench.cargar_corpus(bench.CORPUS_POR_DEFECTO)
    # Toda cita esperada existe en el corpus (si no, el benchmark mediría ruido).
    for q in consultas:
        if q["tipo"] == "cita":
            assert any(bench.es_cita_correcta(d, q["espera"]) for d in corpus), q["id"]


def test_ejecutar_es_reproducible_y_mide_todas_las_metricas() -> None:
    a = bench.ejecutar()
    b = bench.ejecutar()
    resumen = lambda r: {v["variante"]: {k: x for k, x in v["resumen"].items() if "latencia_medida" not in k} for v in r["variantes"]}  # noqa: E731
    sin_lat = lambda r: {n: {k: x for k, x in s.items() if "latencia" not in k} for n, s in resumen(r).items()}  # noqa: E731
    assert sin_lat(a) == sin_lat(b)
    assert [v["variante"] for v in a["variantes"]] == ["faiss", "pageindex", "hibrido"]
    faiss, pageindex, hibrido = (v["resumen"] for v in a["variantes"])
    assert faiss["llamadas_llm_por_consulta"] == 0 and faiss["coste_por_consulta"] == 0
    assert pageindex["llamadas_llm_por_consulta"] > hibrido["llamadas_llm_por_consulta"] > 0
    assert hibrido["abstencion_correcta"] == faiss["abstencion_correcta"] == 1.0
    for r in (faiss, pageindex, hibrido):
        assert 0 <= r["cita_top1"] <= r["cita_en_k"] <= 1


def test_cli_imprime_tabla_y_guarda_json(tmp_path) -> None:
    salida = tmp_path / "r.json"
    res = CliRunner().invoke(bench.cli, ["--variantes", "faiss,hibrido", "--salida", str(salida)])
    assert res.exit_code == 0, res.output
    assert "| hibrido |" in res.output
    datos = json.loads(salida.read_text())
    assert datos["modo"] == "simulado" and len(datos["variantes"]) == 2

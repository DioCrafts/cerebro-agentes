"""The promotion gate's statistics on synthetic paired results: honest, reproducible, conservative."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from asistente.nucleo.evaluacion import suite as s
from asistente.nucleo.evaluacion.estadistica import bootstrap_pareado, emparejar, mcnemar_exacto
from asistente.nucleo.evaluacion.gate import Umbrales, evaluar_promocion, guardar_informe, resumen_es
from asistente.nucleo.evaluacion.modelos import (
    Ejecucion,
    InformeEvaluacion,
    ResultadoCaso,
    ResumenInforme,
)

CONFIG = s.cargar_config()


def _informe(
    variante: str,
    resultados: dict[str, bool],
    *,
    criticos: frozenset[str] = frozenset(),
    coste: str = "0.01",
    latencia: float = 1.0,
    omitidos: frozenset[str] = frozenset(),
) -> InformeEvaluacion:
    casos = [
        ResultadoCaso(
            id=caso_id,
            version=1,
            categoria="intencion" if caso_id in criticos else "modulo",
            critica=caso_id in criticos,
            aprobado=aprobado,
            repeticiones_aprobadas=int(aprobado),
            omitido=caso_id in omitidos,
            ejecucion=Ejecucion(respuesta="x", modo="verified", coste_usd=Decimal(coste), latencia_s=latencia),
        )
        for caso_id, aprobado in resultados.items()
    ]
    total = len(casos)
    aprobados = sum(c.aprobado for c in casos)
    return InformeEvaluacion(
        variante=variante,
        proveedor="p",
        modelo="m",
        modo="modelo",
        suite_version="v",
        suite_huella="h",
        generado_el=datetime.now(UTC),
        resumen=ResumenInforme(total=total, aprobados=aprobados, tasa=aprobados / total, por_categoria={}),
        casos=casos,
    )


def _ids(n: int) -> list[str]:
    return [f"caso-{i:03d}" for i in range(n)]


def test_a_clear_improvement_is_promoted() -> None:
    ids = _ids(60)
    base = _informe("base", {i: n >= 12 for n, i in enumerate(ids)})  # fails the first 12
    candidata = _informe("cand", {i: n >= 2 for n, i in enumerate(ids)})  # fixes 10 of them, breaks nothing
    informe = evaluar_promocion(base, candidata, CONFIG, Umbrales())
    assert informe.decision == "promovida", informe.motivos
    e = informe.estadisticos
    assert e.n_pares == 60 and e.mejoras == 10 and e.regresiones == 0
    assert e.ic_inferior > 0 and e.media_delta == pytest.approx(10 / 60, abs=1e-6)
    assert e.p_mcnemar_bilateral < 0.01


def test_a_regression_is_rejected_even_with_as_many_improvements() -> None:
    ids = _ids(60)
    base = _informe("base", {i: n >= 6 for n, i in enumerate(ids)})
    # Fixes the 6 the active one failed, breaks 6 others: the same rate, but the interval reaches below 0.
    candidata = _informe("cand", {i: (n < 6) or n >= 12 for n, i in enumerate(ids)})
    informe = evaluar_promocion(base, candidata, CONFIG, Umbrales())
    assert informe.decision == "rechazada"
    assert informe.estadisticos.ic_inferior < 0 and any("Puede empeorar" in m for m in informe.motivos)
    assert sorted(informe.regresiones) == ids[6:12]


def test_one_uncompensated_regression_is_enough_to_refuse() -> None:
    ids = _ids(40)
    base = _informe("base", dict.fromkeys(ids, True))
    candidata = _informe("cand", {i: i != ids[7] for i in ids})
    informe = evaluar_promocion(base, candidata, CONFIG, Umbrales())
    assert informe.decision == "rechazada" and informe.estadisticos.ic_inferior < 0


def test_a_small_sample_is_insufficient_evidence_never_a_promotion() -> None:
    ids = _ids(12)
    base = _informe("base", {i: n >= 6 for n, i in enumerate(ids)})
    candidata = _informe("cand", dict.fromkeys(ids, True))  # looks much better…
    informe = evaluar_promocion(base, candidata, CONFIG, Umbrales())
    assert informe.decision == "evidencia_insuficiente" and not informe.promovida
    assert any("12 casos comparables" in m for m in informe.motivos)


def test_a_critical_regression_is_rejected_despite_a_better_average() -> None:
    ids = _ids(60)
    critico = ids[30]
    base = _informe("base", {i: n >= 15 for n, i in enumerate(ids)}, criticos=frozenset({critico}))
    candidata = _informe("cand", {i: i != critico for i in ids}, criticos=frozenset({critico}))
    informe = evaluar_promocion(base, candidata, CONFIG, Umbrales())
    assert informe.estadisticos.media_delta > 0.2 and informe.estadisticos.ic_inferior > 0
    assert informe.decision == "rechazada" and informe.regresiones_criticas == [critico]
    assert any("categorías críticas" in m for m in informe.motivos)


def test_equal_behaviour_with_enough_cases_is_promoted() -> None:
    ids = _ids(35)
    resultados = {i: n % 5 != 0 for n, i in enumerate(ids)}
    informe = evaluar_promocion(_informe("base", resultados), _informe("cand", resultados), CONFIG, Umbrales())
    assert informe.decision == "promovida"
    assert (informe.estadisticos.ic_inferior, informe.estadisticos.ic_superior) == (0.0, 0.0)


def test_cost_and_latency_budgets() -> None:
    ids = _ids(40)
    resultados = dict.fromkeys(ids, True)
    base = _informe("base", resultados, coste="0.010", latencia=2.0)
    cara = _informe("cand", resultados, coste="0.012", latencia=2.0)
    informe = evaluar_promocion(base, cara, CONFIG, Umbrales())
    assert informe.decision == "rechazada" and any("Cuesta 1.20 veces" in m for m in informe.motivos)
    lenta = _informe("cand", resultados, coste="0.010", latencia=4.0)
    informe = evaluar_promocion(base, lenta, CONFIG, Umbrales())
    assert informe.decision == "rechazada" and any("más lenta" in m for m in informe.motivos)
    # Tiny absolute differences are noise, not a reason.
    rapida = _informe("cand", resultados, coste="0.010", latencia=2.3)
    assert evaluar_promocion(base, rapida, CONFIG, Umbrales()).decision == "promovida"
    barata = _informe("cand", resultados, coste="0.005", latencia=1.0)
    assert evaluar_promocion(base, barata, CONFIG, Umbrales()).decision == "promovida"


def test_omitted_or_unpaired_cases_are_not_compared() -> None:
    ids = _ids(32)
    base = _informe("base", dict.fromkeys(ids, True), omitidos=frozenset({ids[0]}))
    candidata = _informe("cand", dict.fromkeys(ids[:-1], True))
    pares, avisos = emparejar(base, candidata)
    assert len(pares) == 30 and any(ids[-1] in a for a in avisos)


def test_the_statistics_themselves() -> None:
    # Deterministic for a seed; percentile interval of the mean.
    a = bootstrap_pareado([1.0, 0.0, 0.0, -1.0, 1.0] * 8, semilla=7)
    assert a == bootstrap_pareado([1.0, 0.0, 0.0, -1.0, 1.0] * 8, semilla=7)
    assert a.inferior <= a.media <= a.superior and a.media == pytest.approx(0.2)
    assert bootstrap_pareado([]).media == 0.0
    # Exact McNemar: 10 improvements vs 0 regressions is significant; 3 vs 2 is not.
    fuerte = mcnemar_exacto(0, 10)
    assert fuerte.p_bilateral == pytest.approx(2 / 1024, abs=1e-6) and fuerte.p_empeora == 1.0
    assert mcnemar_exacto(2, 3).p_bilateral == 1.0
    assert mcnemar_exacto(8, 0).p_empeora == pytest.approx(1 / 256, abs=1e-6)
    assert mcnemar_exacto(0, 0).p_bilateral == 1.0


def test_the_decision_is_written_as_an_auditable_report(tmp_path: Path) -> None:
    ids = _ids(40)
    base = _informe("base", {i: n >= 5 for n, i in enumerate(ids)})
    candidata = _informe("cand", dict.fromkeys(ids, True))
    informe = evaluar_promocion(base, candidata, CONFIG, Umbrales())
    json_path, texto_path = guardar_informe(informe, tmp_path, marca="t")
    assert json_path.name == "promote-t-decision.json" and texto_path.read_text(encoding="utf-8").startswith(
        "Candidata «cand» frente a «base»: PROMOVIDA"
    )
    resumen = resumen_es(informe)
    assert "IC 95% bootstrap pareado" in resumen and "McNemar exacto" in resumen and "Mejoras:" in resumen
    from asistente.nucleo.evaluacion.gate import InformePromocion

    assert InformePromocion.model_validate_json(json_path.read_text(encoding="utf-8")) == informe

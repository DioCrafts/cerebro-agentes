"""Calibration of the council's confidence and whether disagreement predicts mistakes — on synthetic data."""

from __future__ import annotations

import random

import pytest

from asistente.nucleo.evaluacion import calibracion as cal


def test_a_perfectly_calibrated_council_has_zero_ece() -> None:
    # 10 rulings at 0.8 confidence, 8 right; 10 at 0.3, 3 right.
    confianzas = [0.8] * 10 + [0.3] * 10
    aciertos = [True] * 8 + [False] * 2 + [True] * 3 + [False] * 7
    assert cal.ece(confianzas, aciertos) == pytest.approx(0.0)
    bins = cal.bins_fiabilidad(confianzas, aciertos)
    assert len(bins) == 10 and bins[8].n == 10 and bins[8].tasa_acierto == 0.8 and bins[3].tasa_acierto == 0.3


def test_an_overconfident_council_has_a_large_ece_and_brier() -> None:
    confianzas = [0.95] * 20
    aciertos = [True] * 12 + [False] * 8
    assert cal.ece(confianzas, aciertos) == pytest.approx(0.35)
    assert cal.brier(confianzas, aciertos) == pytest.approx((12 * 0.05**2 + 8 * 0.95**2) / 20)


def test_ece_edges() -> None:
    assert cal.ece([], []) == 0.0
    assert cal.bins_fiabilidad([1.0], [True])[-1].n == 1  # 1.0 falls in the last, closed bin
    with pytest.raises(ValueError):
        cal.ece([1.2], [True])
    with pytest.raises(ValueError):
        cal.ece([0.5, 0.6], [True])


def test_auroc() -> None:
    assert cal.auroc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert cal.auroc([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == 0.0
    assert cal.auroc([0.5, 0.5, 0.5, 0.5], [True, False, True, False]) == 0.5
    assert cal.auroc([0.3, 0.7], [True, True]) is None
    # Mann–Whitney with ties: one tie between classes counts one half.
    assert cal.auroc([0.9, 0.5, 0.5, 0.1], [True, True, False, False]) == pytest.approx(0.875)


def test_kendall_w() -> None:
    assert cal.kendall_w([["a", "b", "c"]] * 4) == 1.0
    assert cal.kendall_w([["a", "b", "c"], ["c", "b", "a"]]) == 0.0
    assert cal.kendall_w([["a", "b"]]) is None
    # Incomplete rankings (each member ranks the OTHERS' proposals): agreement on the shared pairs.
    incompletos = [["b", "c", "d"], ["a", "c", "d"], ["a", "b", "d"], ["a", "b", "c"]]
    assert cal.kendall_w(incompletos) == 1.0
    desacuerdo = [["d", "c", "b"], ["a", "c", "d"], ["d", "b", "a"], ["a", "b", "c"]]
    assert cal.kendall_w(desacuerdo) < 0.5


def test_calibrar_on_synthetic_rulings_where_disagreement_predicts_errors() -> None:
    rng = random.Random(5)
    puntos = []
    for i in range(120):
        acuerdo = rng.random()
        # Low agreement → more mistakes; confidence tracks the true rate.
        p_acierto = 0.4 + 0.5 * acuerdo
        puntos.append(
            cal.PuntoCalibracion(
                caso=f"c{i}", confianza=round(p_acierto, 2), acierto=rng.random() < p_acierto, acuerdo=acuerdo
            )
        )
    resultado = cal.calibrar(puntos, remuestras=300)
    assert resultado.n == 120 and not resultado.avisos
    assert resultado.ece.valor is not None and resultado.ece.valor < 0.12
    assert resultado.ece.inferior is not None and resultado.ece.inferior <= resultado.ece.valor <= (
        resultado.ece.superior or 1
    )
    assert resultado.auroc_desacuerdo.valor is not None and resultado.auroc_desacuerdo.valor > 0.6
    assert resultado.auroc_confianza.valor is not None and resultado.auroc_confianza.valor > 0.6
    lineas = cal.resumen_calibracion(resultado)
    assert lineas[0].startswith("Calibración del consejo (120 dictámenes)")


def test_calibrar_says_what_it_cannot_measure() -> None:
    puntos = [cal.PuntoCalibracion(caso=f"c{i}", confianza=0.8, acierto=True) for i in range(5)]
    resultado = cal.calibrar(puntos, remuestras=100)
    assert resultado.auroc_confianza.valor is None and resultado.auroc_desacuerdo.valor is None
    assert any("orientativa" in a for a in resultado.avisos)
    assert any("Kendall" in a for a in resultado.avisos)
    assert cal.calibrar([]).n == 0
    assert "sin dictámenes" in cal.resumen_calibracion(cal.calibrar([]), quien="el cerebro solo")[0]

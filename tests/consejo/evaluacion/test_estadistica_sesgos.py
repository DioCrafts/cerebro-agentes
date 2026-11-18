"""The harness's statistics on synthetic data with KNOWN effects: permutation tests, intervals, the verdict rule."""

from __future__ import annotations

import math
import random
import statistics

import pytest

from asistente.nucleo.evaluacion import sesgos as s

CONFIG = s.ConfigSesgos(remuestras=2000, permutaciones=2000)


def test_paired_effect_without_any_difference_is_exactly_zero_and_passes() -> None:
    est = s.efecto_pareado([0.0] * 20, CONFIG)
    assert (est.efecto, est.inferior, est.superior) == (0.0, 0.0, 0.0)
    assert est.p_bilateral == 1.0 and est.p_mayor == 1.0
    assert s.decidir(est, 0.25, CONFIG, lado="bilateral") == "sin_sesgo"


def test_exact_sign_flip_test_matches_its_definition() -> None:
    # Six positive differences of 1: only the all-positive and all-negative patterns are as extreme (2 / 64).
    est = s.efecto_pareado([1.0] * 6, CONFIG)
    assert est.p_bilateral == pytest.approx(2 / 64) and est.p_mayor == pytest.approx(1 / 64)
    assert est.inferior == est.superior == 1.0


def test_monte_carlo_and_exact_agree_and_are_deterministic() -> None:
    rng = random.Random(3)
    datos = [rng.gauss(0.3, 1.0) for _ in range(14)]
    exacto = s.efecto_pareado(datos, CONFIG)
    # 15+ values switch to Monte Carlo: duplicate the sample (same mean) and compare the order of magnitude.
    monte = s.efecto_pareado(datos + datos, CONFIG)
    assert monte.efecto == pytest.approx(exacto.efecto)
    assert s.efecto_pareado(datos + datos, CONFIG) == monte  # same seed, same answer
    assert monte.p_bilateral <= exacto.p_bilateral + 0.05


def test_a_known_effect_is_recovered_with_an_interval_that_covers_it() -> None:
    rng = random.Random(11)
    datos = [rng.gauss(0.6, 0.5) for _ in range(60)]
    est = s.efecto_pareado(datos, CONFIG)
    assert est.inferior < 0.6 < est.superior
    assert est.p_mayor < 0.001
    assert s.decidir(est, 0.25, CONFIG, lado="mayor") == "sesgo"


def test_null_data_is_rarely_significant() -> None:
    """Under H0 the permutation p-value is (close to) uniform: about 5 % of null samples fall below 0.05."""
    significativos = 0
    for semilla in range(60):
        rng = random.Random(1000 + semilla)
        datos = [rng.gauss(0.0, 1.0) for _ in range(25)]
        config = s.ConfigSesgos(remuestras=200, permutaciones=400, semilla=semilla)
        significativos += s.efecto_pareado(datos, config).p_bilateral < 0.05
    assert significativos <= 8  # 5 % of 60 is 3; generous bound for a seeded check


def test_small_noisy_samples_are_inconclusive_not_acquitted() -> None:
    est = s.efecto_pareado([0.4, -0.3, 0.5, -0.2, 0.1], CONFIG)
    assert s.decidir(est, 0.25, CONFIG, lado="bilateral") == "no_concluyente"


def test_additive_residuals_remove_quality_and_leniency_exactly() -> None:
    observaciones = [
        s.Observacion(bloque=f"r{r}", unidad=f"p{p}", puntuacion=1 + p * 0.7 + r * 0.3, grupo=0)
        for r in range(4)
        for p in range(5)
    ]
    assert all(abs(x) < 1e-9 for x in s.residuos_aditivos(observaciones))


def _cuadrado_latino(efecto_primera: float) -> list[s.Observacion]:
    """Four reviewers see the same 3 proposals in every cyclic rotation; the first shown gets ``efecto_primera``."""
    observaciones: list[s.Observacion] = []
    for revisor in range(4):
        for r, orden in enumerate(s.rotaciones(3)):
            for posicion, propuesta in enumerate(orden):
                puntos = 2.0 + propuesta * 0.8 + revisor * 0.2 + (efecto_primera if posicion == 0 else 0.0)
                observaciones.append(
                    s.Observacion(
                        bloque=f"{revisor}|{r}",
                        unidad=f"{revisor}|{propuesta}",
                        puntuacion=puntos,
                        grupo=1 if posicion == 0 else (0 if posicion == 2 else -1),
                        posicion=posicion,
                    )
                )
    return observaciones


@pytest.mark.parametrize("efecto", [0.0, 0.5, -0.6])
def test_the_residual_contrast_recovers_a_planted_position_effect(efecto: float) -> None:
    observaciones = _cuadrado_latino(efecto)
    est = s.contraste_residual(observaciones, CONFIG, permutar=s._permutar_dentro(observaciones), etiqueta="t")
    assert est is not None
    assert est.efecto == pytest.approx(efecto, abs=1e-6)
    if efecto == 0.0:
        assert est.inferior == est.superior == 0.0
    else:
        assert est.p_bilateral < 0.01


def test_wilson_and_binomial_tail() -> None:
    inferior, superior = s.wilson(50, 100)
    assert inferior == pytest.approx(0.4038, abs=1e-3) and superior == pytest.approx(0.5962, abs=1e-3)
    assert s.wilson(0, 0) == (0.0, 1.0)
    assert s.cola_binomial(0, 10, 0.5) == 1.0
    assert s.cola_binomial(10, 10, 0.5) == pytest.approx(1 / 1024)
    assert s.cola_binomial(6, 10, 0.5) == pytest.approx(sum(math.comb(10, k) for k in range(6, 11)) / 1024)


def test_the_verdict_rule() -> None:
    def est(efecto: float, inferior: float, superior: float, p: float) -> s.Estimacion:
        return s.Estimacion(efecto, inferior, superior, p, p, 50, 50)

    assert s.decidir(est(0.05, -0.1, 0.2, 0.4), 0.25, CONFIG, lado="bilateral") == "sin_sesgo"
    assert s.decidir(est(0.6, 0.4, 0.8, 0.001), 0.25, CONFIG, lado="bilateral") == "sesgo"
    # Significant but tiny: not a bias worth the name, but not shown negligible either.
    assert s.decidir(est(0.1, 0.05, 0.3, 0.001), 0.25, CONFIG, lado="bilateral") == "no_concluyente"
    # One-sided: a large NEGATIVE effect (e.g. penalising padding) is not the bias.
    assert s.decidir(est(-0.6, -0.8, -0.4, 0.001), 0.25, CONFIG, lado="mayor") == "sin_sesgo"


def test_effect_and_interval_are_reproducible() -> None:
    datos = [statistics.fmean([i % 3, i % 5]) for i in range(30)]
    assert s.efecto_pareado(datos, CONFIG) == s.efecto_pareado(datos, CONFIG)

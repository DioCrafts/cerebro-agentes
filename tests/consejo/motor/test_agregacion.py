"""Rank aggregation, agreement and votes: textbook examples and the properties the council relies on."""

from __future__ import annotations

from itertools import permutations
import random

import pytest

from asistente.nucleo.consejo.agregacion import (
    MAX_KEMENY_EXACTO,
    acuerdo_compuesto,
    acuerdo_kendall,
    agregar,
    agregar_detallado,
    borda,
    caminos_schulze,
    clave_desempate,
    confianza_del_consejo,
    consenso_de,
    copeland,
    distancia_kendall,
    kemeny,
    matriz_preferencias,
    niveles,
    puntuacion_kemeny,
    rangos_medios,
    revisores_atipicos,
    schulze,
)
from asistente.nucleo.consejo.contrato import Ranking

# Wikipedia's Kemeny–Young example: where should Tennessee's capital be? (Memphis, Nashville, Chattanooga, Knoxville)
TENNESSEE = (
    [["M", "N", "C", "K"]] * 42
    + [["N", "C", "K", "M"]] * 26
    + [["C", "K", "N", "M"]] * 15
    + [["K", "C", "N", "M"]] * 17
)
# Wikipedia's Schulze example: 45 voters, 5 candidates; the Schulze ranking is E > A > C > B > D.
SCHULZE_45 = (
    [list("ACBED")] * 5
    + [list("ADECB")] * 5
    + [list("BEDAC")] * 8
    + [list("CABED")] * 3
    + [list("CAEBD")] * 7
    + [list("CBADE")] * 2
    + [list("DCEBA")] * 7
    + [list("EBADC")] * 8
)


def todos_menos_uno(consenso: list[str]) -> dict[str, list[str]]:
    """The council's design: each reviewer ranks every proposal but its own, here all agreeing with ``consenso``."""
    return {s: [x for x in consenso if x != s] for s in consenso}


# -- normalisation ---------------------------------------------------------------------------------------------


def test_niveles_accepts_ties_rankings_and_rejects_repeats() -> None:
    assert niveles(["a", ["c", "b"], "d"]) == [["a"], ["b", "c"], ["d"]]
    assert niveles(Ranking(evaluador_id="prudente", orden=["B", "A"])) == [["B"], ["A"]]
    with pytest.raises(ValueError):
        niveles(["a", "b", "a"])
    with pytest.raises(ValueError):
        niveles("abc")


def test_mid_ranks_share_the_positions_of_a_tie() -> None:
    assert rangos_medios([["a"], ["b", "c"], ["d"]]) == {"a": 1, "b": 2.5, "c": 2.5, "d": 4}
    assert rangos_medios([["a", "b", "c"]]) == {"a": 2, "b": 2, "c": 2}


# -- Kemeny–Young ----------------------------------------------------------------------------------------------


def test_kemeny_tennessee_textbook() -> None:
    resultado = kemeny(TENNESSEE)
    assert resultado.orden == ["N", "C", "K", "M"]
    assert resultado.optimos == 1
    assert not resultado.empate_en_cabeza
    # The textbook score of the winning order: 42+26... agreements with the voters' pairwise preferences.
    assert resultado.puntuacion == 393


def test_kemeny_is_the_brute_force_optimum() -> None:
    rng = random.Random(7)
    items = list("abcde")
    rankings = [rng.sample(items, len(items)) for _ in range(9)]
    matriz = matriz_preferencias(rankings)
    mejor = max(puntuacion_kemeny(p, matriz) for p in permutations(items))
    assert puntuacion_kemeny(kemeny(rankings).orden, matriz) == mejor


def test_kemeny_refuses_more_than_six_candidates() -> None:
    with pytest.raises(ValueError):
        kemeny([list("abcdefg")])
    assert MAX_KEMENY_EXACTO == 6


def test_kemeny_ties_break_towards_borda_then_the_key_and_report_a_tied_top() -> None:
    # A Condorcet cycle: every rotation is optimal, the top is tied.
    ciclo = [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]]
    resultado = kemeny(ciclo)
    assert resultado.optimos == 3
    assert resultado.empate_en_cabeza
    assert resultado.orden == ["a", "b", "c"]  # all Borda-equal: the id decides, deterministically
    assert kemeny(ciclo).orden == resultado.orden


def test_seeded_tie_break_does_not_favour_an_id_for_life() -> None:
    ciclo = [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]]
    ganadores = {kemeny(ciclo, semilla=s).orden[0] for s in range(40)}
    assert ganadores == {"a", "b", "c"}
    assert clave_desempate("a") == "a"
    assert clave_desempate("a", 1) != clave_desempate("a", 2)


# -- Borda, Copeland, Schulze ----------------------------------------------------------------------------------


def test_borda_tennessee_misses_the_condorcet_winner_order() -> None:
    puntos = borda(TENNESSEE)
    assert sorted(puntos, key=lambda x: -puntos[x]) == ["N", "C", "M", "K"]
    # Classical Borda points (3, 2, 1, 0) are 194, 173, 126, 107: normalised by 3 and averaged over 100 voters.
    assert puntos["N"] == pytest.approx(194 / 300)
    assert puntos["K"] == pytest.approx(107 / 300)


def test_borda_with_ties_and_partial_rankings_does_not_punish_absence() -> None:
    puntos = borda([["a", ["b", "c"]], ["b", "a"]])
    assert puntos["a"] == pytest.approx((1.0 + 0.0) / 2)
    assert puntos["b"] == pytest.approx((0.25 + 1.0) / 2)
    assert puntos["c"] == pytest.approx(0.25)  # ranked by one reviewer only: its mean, not a sum


def test_copeland_tennessee() -> None:
    assert copeland(TENNESSEE) == {"N": 3, "C": 1, "K": -1, "M": -3}


def test_schulze_textbook_example() -> None:
    assert schulze(SCHULZE_45) == ["E", "A", "C", "B", "D"]
    p = caminos_schulze(SCHULZE_45)
    # Strongest paths, as in the textbook's table (row A: –, 28, 28, 30, 24; row E: 25, 28, 28, 31, –).
    assert [p[("A", x)] for x in "BCDE"] == [28, 28, 30, 24]
    assert [p[("E", x)] for x in "ABCD"] == [25, 28, 28, 31]
    assert [p[("C", x)] for x in "ABDE"] == [25, 29, 29, 24]


def test_schulze_tennessee_is_the_condorcet_order() -> None:
    assert schulze(TENNESSEE) == ["N", "C", "K", "M"]


def test_agregar_uses_kemeny_up_to_six_and_schulze_above() -> None:
    detalle = agregar_detallado(TENNESSEE)
    assert detalle.metodo == "kemeny" and detalle.orden == ["N", "C", "K", "M"]
    siete = [list("abcdefg"), list("abcdegf"), list("bacdefg")]
    detalle7 = agregar_detallado(siete)
    assert detalle7.metodo == "schulze"
    assert detalle7.orden[:2] == ["a", "b"]
    assert agregar([["x"]]) == ["x"]
    assert agregar_detallado([]).orden == []


def test_agregar_in_the_council_design_recovers_the_consensus() -> None:
    consenso = ["c", "a", "d", "b", "e"]
    assert agregar(todos_menos_uno(consenso)) == consenso


def test_distancia_kendall() -> None:
    assert distancia_kendall(list("abc"), list("abc")) == 0
    assert distancia_kendall(list("abc"), list("cba")) == 3


# -- Kendall's W -----------------------------------------------------------------------------------------------


def test_w_full_agreement_is_one_and_reversed_is_zero() -> None:
    assert acuerdo_kendall([list("abcd")] * 3) == 1.0
    assert acuerdo_kendall([list("abcd"), list("dcba"), list("abcd"), list("dcba")]) == 0.0


def test_w_textbook_value() -> None:
    # Rank sums 4, 6, 8, 12 (m=3, n=4): S = 35, W = 12·35 / (9·60) = 0.7778.
    assert acuerdo_kendall([list("abcd"), ["a", "c", "b", "d"], ["b", "a", "c", "d"]]) == pytest.approx(0.777778)


def test_w_tie_correction() -> None:
    # One reviewer ties b and c (T = 2³ − 2 = 6): W = 12·42.5 / (9·60 − 3·6) = 0.97701; uncorrected it would be 0.944.
    w = acuerdo_kendall([["a", ["b", "c"], "d"], list("abcd"), list("abcd")])
    assert w == pytest.approx(510 / 522, abs=1e-6)
    assert w > 510 / 540


def test_w_needs_three_rankings_and_two_items() -> None:
    assert acuerdo_kendall([list("abc"), list("abc")]) is None
    assert acuerdo_kendall([["a"], ["a"], ["a"]]) is None
    assert acuerdo_kendall([[["a", "b"]], [["a", "b"]], [["a", "b"]]]) is None  # all tied: nothing to agree on


@pytest.mark.parametrize("n", [3, 4, 5, 6, 7])
def test_w_incomplete_council_design_perfect_agreement_is_one(n: int) -> None:
    consenso = [f"s{i}" for i in range(n)]
    random.Random(n).shuffle(consenso)
    assert acuerdo_kendall(list(todos_menos_uno(consenso).values())) == 1.0


def test_w_incomplete_disagreement_is_low_and_partial_agreement_in_between() -> None:
    consenso = list("abcde")
    acuerdo = todos_menos_uno(consenso)
    contrario = todos_menos_uno(list(reversed(consenso)))
    mitad = {**{s: acuerdo[s] for s in "abc"}, **{s: contrario[s] for s in "de"}}
    w_mitad = acuerdo_kendall(list(mitad.values()))
    uno_fuera = {**acuerdo, "e": contrario["e"]}
    w_uno = acuerdo_kendall(list(uno_fuera.values()))
    assert w_mitad is not None and w_uno is not None
    assert w_mitad < 0.3
    assert w_mitad < w_uno < 1.0


def test_w_position_driven_reviewers_in_a_balanced_design_agree_on_nothing() -> None:
    from asistente.nucleo.consejo.anonimato import diseno_equilibrado

    diseno = diseno_equilibrado(list("abcde"), 3, "sesion")
    # Every reviewer ranks by the position it was shown: each proposal gets every position once.
    assert acuerdo_kendall(list(diseno.values())) == 0.0


def test_w_complete_formula_and_incomplete_formula_agree_on_complete_rankings() -> None:
    from asistente.nucleo.consejo import agregacion

    rng = random.Random(3)
    rankings = [rng.sample(list("abcde"), 5) for _ in range(6)]
    preparados = [agregacion.niveles(r) for r in rankings]
    rangos = [agregacion.rangos_medios(r) for r in preparados]
    general = agregacion._acuerdo_incompleto(preparados, rangos, sorted("abcde"))
    assert general == pytest.approx(acuerdo_kendall(rankings), abs=1e-6)


def test_w_is_label_invariant() -> None:
    rankings = [list("abcd"), ["b", "a", "c", "d"], ["a", "c", "b", "d"], ["a", "b", "d", "c"]]
    renombrar = {"a": "z", "b": "y", "c": "x", "d": "w"}
    renombrados = [[renombrar[i] for i in r] for r in rankings]
    assert acuerdo_kendall(rankings) == acuerdo_kendall(renombrados)


# -- outliers --------------------------------------------------------------------------------------------------


def test_one_reversed_reviewer_is_flagged_not_dropped() -> None:
    consenso = list("abcde")
    rankings = {**todos_menos_uno(consenso), "e": [x for x in reversed(consenso) if x != "e"]}
    atipicos = revisores_atipicos(rankings)
    assert [a.evaluador_id for a in atipicos] == ["e"]
    assert atipicos[0].distancia == 1.0 and atipicos[0].pares >= 2
    detalle = agregar_detallado(rankings)
    assert [a.evaluador_id for a in detalle.atipicos] == ["e"]
    assert detalle.n_rankings == 5  # still counted


def test_nobody_is_an_outlier_when_everyone_disagrees() -> None:
    from asistente.nucleo.consejo.anonimato import diseno_equilibrado

    assert revisores_atipicos(diseno_equilibrado(list("abcd"), 1, "s")) == []
    assert revisores_atipicos(todos_menos_uno(list("abcd"))) == []


# -- votes and confidence --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("votos", "consenso", "recomendacion"),
    [
        (["o1", "o1", "o1", "o1"], "unanime", "o1"),
        (["o1", "o1", "o1", "o2"], "mayoria", "o1"),
        (["o2", "o2", "o1"], "mayoria", "o2"),
        (["o1", "o1", "o2", "o2"], "dividido", "depende"),
        (["o1", "o2", "o3"], "dividido", "depende"),
        (["o1", "o1", "o2", "o3", "o3"], "dividido", "depende"),
        (["depende", "depende", "depende"], "unanime", "depende"),
        (["o1", "o1"], "insuficiente", "depende"),
        ([], "insuficiente", "depende"),
    ],
)
def test_consensus_label(votos: list[str], consenso: str, recomendacion: str) -> None:
    votacion = consenso_de(votos)
    assert votacion.consenso == consenso
    assert votacion.recomendacion == recomendacion


def test_consensus_is_order_independent() -> None:
    votos = ["o2", "o1", "o1", "o3", "o1"]
    assert all(consenso_de(list(p)) == consenso_de(votos) for p in permutations(votos))


def test_confidence_formula_and_monotonicity() -> None:
    assert confianza_del_consejo([0.8, 0.6], 1.0, 1.0) == pytest.approx(0.7)
    assert confianza_del_consejo([0.8], 0.5, None) == pytest.approx(0.4)
    assert acuerdo_compuesto(0.75, 0.0) == pytest.approx(0.5)
    assert confianza_del_consejo([], 1.0, 1.0) == 0.0
    previa = -1.0
    for w in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        actual = confianza_del_consejo([0.7, 0.9], 0.75, w)
        assert actual >= previa
        previa = actual
    assert confianza_del_consejo([0.7], 0.5, 0.5) <= confianza_del_consejo([0.7], 0.75, 0.5)

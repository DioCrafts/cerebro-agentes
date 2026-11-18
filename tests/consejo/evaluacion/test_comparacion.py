"""«¿Merece la pena el consejo?»: the council against the brain in one pass, fed to the existing promotion gate with
a cost budget that is RELATIVE (more quality buys more cost), and its Spanish report."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from asistente.nucleo.evaluacion import consejo_eval as ce
from asistente.nucleo.evaluacion import suite
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, InformeEvaluacion, ResultadoCaso
from asistente.nucleo.evaluacion.runner import Entorno
from asistente.nucleo.evaluacion.variantes import VARIANTE_BASE
from asistente.plataforma.ops.consumo import ConsumoStore

PRESUPUESTO = ce.PresupuestoConsejo(
    coste_relativo_sin_mejora=3.0, coste_relativo_por_punto=0.5, coste_relativo_max=12.0, min_casos=30
)


@pytest.fixture(scope="module")
def casos() -> list[CasoEvaluacion]:
    return [c for c in suite.cargar_casos() if "consejo" in c.requiere]


@pytest.fixture(scope="module")
def corridas(
    casos: list[CasoEvaluacion], tmp_path_factory: pytest.TempPathFactory
) -> tuple[InformeEvaluacion, InformeEvaluacion]:
    directorio = tmp_path_factory.mktemp("comparacion")
    entorno = Entorno(
        proveedor=ce.proveedor_oraculo,
        consumo=ConsumoStore(str(directorio / "c.sqlite3")),
        modo="modelo",
        fabrica_consejo=ce.fabrica_oraculo,
    )
    informe, base, candidata = ce.ejecutar_comparacion(
        casos, entorno, VARIANTE_BASE, config=suite.cargar_config(), suite_huella="huella", presupuesto=PRESUPUESTO
    )
    assert informe.veredicto == "mantener"  # identical quality at no cost: nothing against it
    return base, candidata


def _con(informe: InformeEvaluacion, cambios: dict[str, ResultadoCaso], coste: str | None = None) -> InformeEvaluacion:
    casos = []
    for r in informe.casos:
        r = cambios.get(r.id, r)
        if coste is not None:
            r = r.model_copy(update={"ejecucion": r.ejecucion.model_copy(update={"coste_usd": Decimal(coste)})})
        casos.append(r)
    return informe.model_copy(update={"casos": casos})


def _sin_riesgos(caso: CasoEvaluacion, resultado: ResultadoCaso) -> ResultadoCaso:
    """The same case answered without its key risks (a worse ruling): fails the rubric."""
    malo = dict(ce.controles_negativos(caso))["riesgos"]
    salida = ce._salida_de(caso, malo.model_copy(update={"sistema": resultado.ejecucion.consejo.respuesta.sistema}))  # type: ignore[union-attr]
    ejecucion = resultado.ejecucion.model_copy(update={"consejo": salida, "respuesta": salida.respuesta.texto()})
    return resultado.model_copy(update={"ejecucion": ejecucion, "aprobado": False, "repeticiones_aprobadas": 0})


def _peores(casos: list[CasoEvaluacion], informe: InformeEvaluacion, n: int) -> dict[str, ResultadoCaso]:
    por_id = {r.id: r for r in informe.casos}
    elegidos = [c for c in casos if c.categoria == "decision" and ce.controles_negativos(c)][:n]
    return {c.id: _sin_riesgos(c, por_id[c.id]) for c in elegidos}


def test_better_quality_buys_more_cost(
    casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]
) -> None:
    base, consejo = corridas
    cerebro = _con(base, _peores(casos, base, 15), coste="0.01")
    caro = _con(consejo, {}, coste="0.04")  # 4× the brain: above the 3× allowed without a gain
    informe = ce.comparar(cerebro, caro, casos, suite.cargar_config(), presupuesto=PRESUPUESTO)
    assert informe.ganancia > 0 and informe.ganancia_ic[0] > 0
    assert informe.coste_relativo == pytest.approx(4.0)
    # 3× for «not worse» + 0.5× per percentage point of gain (the lower bound of its interval).
    assert informe.coste_permitido == pytest.approx(3.0 + 0.5 * 100 * informe.ganancia_ic[0], abs=0.01)
    assert informe.coste_permitido > 4.0
    assert informe.veredicto == "mantener", informe.motivos
    # The same gain does not buy 5×.
    mas_caro = ce.comparar(
        cerebro, _con(consejo, {}, coste="0.05"), casos, suite.cargar_config(), presupuesto=PRESUPUESTO
    )
    assert mas_caro.veredicto == "no_mantener"
    riesgos = next(c for c in informe.criterios if c.criterio == "riesgos")
    assert riesgos.consejo == 1.0 and riesgos.cerebro < 1.0


def test_too_expensive_for_what_it_gains(
    casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]
) -> None:
    base, consejo = corridas
    cerebro = _con(base, _peores(casos, base, 3), coste="0.01")
    carisimo = _con(consejo, {}, coste="0.20")  # 20×: above the ceiling whatever the gain
    informe = ce.comparar(cerebro, carisimo, casos, suite.cargar_config(), presupuesto=PRESUPUESTO)
    assert informe.veredicto == "no_mantener"
    assert any("Cuesta" in m for m in informe.motivos)


def test_same_quality_more_cost_is_allowed_only_up_to_the_no_gain_budget(
    casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]
) -> None:
    base, consejo = corridas
    barato = ce.comparar(
        _con(base, {}, "0.01"), _con(consejo, {}, "0.025"), casos, suite.cargar_config(), presupuesto=PRESUPUESTO
    )
    caro = ce.comparar(
        _con(base, {}, "0.01"), _con(consejo, {}, "0.04"), casos, suite.cargar_config(), presupuesto=PRESUPUESTO
    )
    assert barato.coste_permitido == caro.coste_permitido == 3.0
    assert barato.veredicto == "mantener" and caro.veredicto == "no_mantener"


def test_a_worse_council_on_a_fairness_case_is_never_kept(
    casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]
) -> None:
    base, consejo = corridas
    caso = next(c for c in casos if c.categoria == "sesgo")
    resultado = next(r for r in consejo.casos if r.id == caso.id)
    salida = resultado.ejecucion.consejo
    assert salida is not None
    variantes = [
        (
            v.model_copy(update={"recomendacion": "o2" if v.recomendacion == "o1" else "o1"})
            if v.id not in ("base", "placebo")
            else v
        )
        for v in salida.variantes
    ]
    roto = resultado.model_copy(
        update={
            "aprobado": False,
            "repeticiones_aprobadas": 0,
            "ejecucion": resultado.ejecucion.model_copy(
                update={"consejo": salida.model_copy(update={"variantes": variantes})}
            ),
        }
    )
    mejorado = _con(consejo, {caso.id: roto})
    informe = ce.comparar(
        _con(base, _peores(casos, base, 10)), mejorado, casos, suite.cargar_config(), presupuesto=PRESUPUESTO
    )
    assert informe.veredicto == "no_mantener"
    assert caso.id in informe.promocion["regresiones_criticas"]
    equidad = {r.prueba: r for r in informe.contrafactual["consejo"]}
    assert equidad["contrafactual_recomendacion"].efecto > 0


def test_few_cases_are_insufficient_evidence(
    casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]
) -> None:
    base, consejo = corridas
    pocos = {c.id for c in casos[:10]}
    recorta = lambda i: i.model_copy(update={"casos": [r for r in i.casos if r.id in pocos]})  # noqa: E731
    informe = ce.comparar(recorta(base), recorta(consejo), casos, suite.cargar_config(), presupuesto=PRESUPUESTO)
    assert informe.veredicto == "evidencia_insuficiente"


def test_the_relative_budget_rule() -> None:
    p = ce.PresupuestoConsejo(coste_relativo_sin_mejora=2.0, coste_relativo_por_punto=0.25, coste_relativo_max=6.0)
    assert p.coste_permitido(-5) == 2.0
    assert p.coste_permitido(0) == 2.0
    assert p.coste_permitido(8) == 4.0
    assert p.coste_permitido(100) == 6.0


def test_the_spanish_report(casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]) -> None:
    base, consejo = corridas
    informe = ce.comparar(
        _con(base, _peores(casos, base, 15), "0.01"),
        _con(consejo, {}, "0.04"),
        casos,
        suite.cargar_config(),
        presupuesto=PRESUPUESTO,
    )
    texto = ce.resumen_merece_la_pena(informe)
    assert texto.startswith("¿Merece la pena el consejo?")
    assert "Veredicto: SÍ" in texto
    for fragmento in (
        "Calidad (rúbrica determinista",
        "Coste:",
        "por punto de ganancia",
        "Calibración del consejo",
        "Equidad (consejo)",
        "Por criterio",
    ):
        assert fragmento in texto, fragmento
    assert InformeEvaluacion  # noqa: B018 - imported for the type of the fixtures


def test_the_command_line(tmp_path: Path) -> None:
    assert ce.main(["validar-sesgos"]) == 0
    assert ce.main(["guiones", "--consumo-dir", str(tmp_path / "g")]) == 0
    assert ce.main(["sesgos", "--falsos", "--out", str(tmp_path / "sesgos.json")]) == 0
    assert (tmp_path / "sesgos.json").exists() and (tmp_path / "sesgos.txt").read_text(encoding="utf-8").startswith(
        "Sesgos"
    )
    assert ce.main(["comparar", "--guionizado", "--out", str(tmp_path / "comparar")]) == 0
    textos = list((tmp_path / "comparar").glob("consejo-*-merece-la-pena.txt"))
    assert textos and textos[0].read_text(encoding="utf-8").startswith("¿Merece la pena el consejo?")
    # Spending money needs an explicit «--yes».
    assert ce.main(["comparar", "--out", str(tmp_path / "real")]) == 2


def test_the_command_line_calibration_and_bias_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ce.main(["sesgos", "--falsos", "--out", str(tmp_path / "sesgos.json")]) == 0
    salida = tmp_path / "comparar"
    assert (
        ce.main(["comparar", "--guionizado", "--out", str(salida), "--sesgos-desde", str(tmp_path / "sesgos.json")])
        == 0
    )
    decision = next(salida.glob("consejo-*-decision.json"))
    assert ce.InformeMereceLaPena.model_validate_json(decision.read_text(encoding="utf-8")).sesgos is not None
    capsys.readouterr()
    assert ce.main(["calibrar", str(next(salida.glob("consejo-*-consejo.json")))]) == 0
    texto = capsys.readouterr().out
    assert texto.startswith("Calibración de «consejo» (70 dictámenes)") and "dictámenes, confianza media" in texto


def test_partial_rulings_are_left_out_of_the_comparison(
    casos: list[CasoEvaluacion], corridas: tuple[InformeEvaluacion, InformeEvaluacion]
) -> None:
    base, consejo = corridas
    caso = next(c for c in casos if c.categoria == "decision")
    resultado = next(r for r in consejo.casos if r.id == caso.id)
    salida = resultado.ejecucion.consejo
    assert salida is not None
    parcial = resultado.model_copy(
        update={
            "aprobado": False,
            "ejecucion": resultado.ejecucion.model_copy(
                update={
                    "consejo": salida.model_copy(
                        update={"respuesta": salida.respuesta.model_copy(update={"parcial": True})}
                    )
                }
            ),
        }
    )
    informe = ce.comparar(
        base, _con(consejo, {caso.id: parcial}), casos, suite.cargar_config(), presupuesto=PRESUPUESTO
    )
    assert informe.n_casos == len(casos) - 1
    assert informe.veredicto == "mantener"  # not counted as the council's failure, not as a pass either
    assert any("dictamen parcial" in a and caso.id in a for a in informe.avisos)
    assert informe.calibracion_consejo.n == len(casos) - 1

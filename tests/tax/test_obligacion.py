"""«¿Estoy obligado a declarar el IRPF?» (art. 96 LIRPF), at every threshold the law prints.

The figures come from the law as quoted in ``tax/obligacion.py`` and checked against the extracts under
``tests/fixtures/boe_obligacion`` (see ``test_obligacion_datos.py``):

* art. 96.2.a) «con el límite de 22.000 euros anuales» → 22.000 is still within the limit;
* art. 96.3 «será de 15.876 euros» and 96.3.a) 1.º «no supera en su conjunto la cantidad de 1.500 euros» → a
  second and further payers' 1.500 keeps the 22.000 limit, 1.500,01 lowers it;
* art. 96.2.b) «con el límite conjunto de 1.600 euros anuales»;
* art. 96.2 «con el límite conjunto de 1.000 euros anuales y pérdidas patrimoniales de cuantía inferior a 500
  euros» → 500 of losses is no longer «inferior».
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from hacienda_gpt.tax.irpf import IRPFInput, calcular_irpf
from hacienda_gpt.tax.obligacion import ResultadoObligacion, obligado_a_declarar
from hacienda_gpt.tax.params import load_params

D = Decimal
EJERCICIOS = (2025, 2026)
# Every circumstance of art. 96.3 answered «no»: only the payers decide the work-income limit.
SIN_SUPUESTOS_96_3: dict[str, Any] = {
    "pension_compensatoria_o_anualidades": False,
    "pagador_no_obligado_a_retener": False,
    "trabajo_tipo_fijo_retencion": False,
}


def _obligacion(ejercicio: int = 2026, **datos: Any) -> ResultadoObligacion:
    return obligado_a_declarar(IRPFInput(ejercicio=ejercicio, **datos), load_params(ejercicio))


def _un_pagador(trabajo: str, ejercicio: int = 2026, **otros: Any) -> ResultadoObligacion:
    return _obligacion(
        ejercicio, rendimiento_trabajo_bruto=D(trabajo), num_pagadores_trabajo=1, **SIN_SUPUESTOS_96_3, **otros
    )


def _faltan(r: ResultadoObligacion) -> set[str | None]:
    return {d.campo for d in r.datos_que_faltan}


# --------------------------------------------------------------------------- #
# Work income (art. 96.2.a and 96.3)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ejercicio", EJERCICIOS)
@pytest.mark.parametrize(("trabajo", "obligado"), [("21999.99", "no"), ("22000", "no"), ("22000.01", "si")])
def test_one_payer_up_to_22000_euros_does_not_have_to_file(ejercicio: int, trabajo: str, obligado: str) -> None:
    r = _un_pagador(trabajo, ejercicio)
    assert r.obligado == obligado, r.respuesta
    assert r.regla is not None and r.regla.referencia == "art. 96.2.a) LIRPF"
    assert "22.000 euros" in r.regla.cita
    assert r.datos_que_faltan == []


@pytest.mark.parametrize("ejercicio", EJERCICIOS)
@pytest.mark.parametrize(
    ("segundo", "obligado", "referencia"),
    [("1500", "no", "art. 96.3.a) 1.º LIRPF"), ("1500.01", "si", "art. 96.3.a) 1.º LIRPF")],
)
def test_the_second_payers_decide_between_22000_and_15876(
    ejercicio: int, segundo: str, obligado: str, referencia: str
) -> None:
    r = _obligacion(
        ejercicio,
        rendimiento_trabajo_bruto=D(20000),
        num_pagadores_trabajo=2,
        importe_segundo_y_restantes_pagadores=D(segundo),
        pensiones_procedimiento_especial=False,
        **SIN_SUPUESTOS_96_3,
    )
    assert r.obligado == obligado, r.respuesta
    assert r.regla is not None and r.regla.referencia == referencia
    assert "1.500 euros" in r.regla.cita and "15.876 euros" in r.regla.cita
    limite = next(linea for linea in r.lineas if linea.concepto.startswith("Límite de rendimientos del trabajo"))
    assert limite.importe == (D(22000) if obligado == "no" else D(15876))


@pytest.mark.parametrize(("trabajo", "obligado"), [("15876", "no"), ("15876.01", "si")])
def test_with_several_payers_the_limit_is_15876(trabajo: str, obligado: str) -> None:
    r = _obligacion(
        rendimiento_trabajo_bruto=D(trabajo),
        importe_segundo_y_restantes_pagadores=D(6000),
        pensiones_procedimiento_especial=False,
        **SIN_SUPUESTOS_96_3,
    )
    assert r.obligado == obligado, r.respuesta


def test_below_15876_nothing_else_about_the_work_income_matters() -> None:
    # Neither the payers nor the cases of art. 96.3 are asked: both limits are met.
    r = _obligacion(rendimiento_trabajo_bruto=D(15876))
    assert r.obligado == "no" and r.datos_que_faltan == []
    assert "15.876 €" in r.respuesta


def test_pensions_withheld_by_the_special_procedure_keep_the_22000_limit() -> None:
    datos: dict[str, Any] = {
        "rendimiento_trabajo_bruto": D(21000),
        "num_pagadores_trabajo": 2,
        "importe_segundo_y_restantes_pagadores": D(7000),
        **SIN_SUPUESTOS_96_3,
    }
    r = _obligacion(**datos, pensiones_procedimiento_especial=True)
    assert r.obligado == "no" and r.regla is not None and r.regla.referencia == "art. 96.3.a) 2.º LIRPF"
    assert _obligacion(**datos, pensiones_procedimiento_especial=False).obligado == "si"
    sin_saber = _obligacion(**datos)
    assert sin_saber.obligado == "no_se_sabe" and _faltan(sin_saber) == {"pensiones_procedimiento_especial"}


@pytest.mark.parametrize(
    ("supuesto", "referencia"),
    [
        ("pension_compensatoria_o_anualidades", "art. 96.3.b) LIRPF"),
        ("pagador_no_obligado_a_retener", "art. 96.3.c) LIRPF"),
        ("trabajo_tipo_fijo_retencion", "art. 96.3.d) LIRPF"),
    ],
)
def test_each_case_of_art_96_3_lowers_the_limit_to_15876(supuesto: str, referencia: str) -> None:
    datos = {**SIN_SUPUESTOS_96_3, supuesto: True}
    encima = _obligacion(rendimiento_trabajo_bruto=D("15876.01"), num_pagadores_trabajo=1, **datos)
    assert encima.obligado == "si" and encima.regla is not None and encima.regla.referencia == referencia
    assert "15.876 euros" in encima.regla.cita
    assert _obligacion(rendimiento_trabajo_bruto=D(15876), num_pagadores_trabajo=1, **datos).obligado == "no"


def test_between_the_two_limits_without_the_facts_it_cannot_be_known_and_says_what_to_ask() -> None:
    r = _obligacion(rendimiento_trabajo_bruto=D(18000), retenciones=D(2500))
    assert r.obligado == "no_se_sabe"
    assert _faltan(r) == {
        "num_pagadores_trabajo",
        "importe_segundo_y_restantes_pagadores",
        "pension_compensatoria_o_anualidades",
        "pagador_no_obligado_a_retener",
        "trabajo_tipo_fijo_retencion",
    }
    assert all(d.pregunta.startswith("¿") for d in r.datos_que_faltan)
    # One payer known: only the other cases are left.
    uno = _obligacion(rendimiento_trabajo_bruto=D(18000), num_pagadores_trabajo=1)
    assert _faltan(uno) == set(SIN_SUPUESTOS_96_3)
    # Two payers but not how much the second paid.
    dos = _obligacion(rendimiento_trabajo_bruto=D(18000), num_pagadores_trabajo=2, **SIN_SUPUESTOS_96_3)
    assert _faltan(dos) == {"importe_segundo_y_restantes_pagadores"}
    # A case that lowers the limit decides even with the payers unknown.
    assert _obligacion(rendimiento_trabajo_bruto=D(18000), trabajo_tipo_fijo_retencion=True).obligado == "si"


# --------------------------------------------------------------------------- #
# Capital income and gains (art. 96.2.b) and the 1.000-euro rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("capital", "obligado"), [("1600", "no"), ("1600.01", "si")])
def test_capital_income_subject_to_withholding_up_to_1600(capital: str, obligado: str) -> None:
    r = _obligacion(rendimiento_capital_mobiliario=D(capital))
    assert r.obligado == obligado
    assert r.regla is not None and r.regla.referencia == "art. 96.2.b) LIRPF" and "1.600 euros" in r.regla.cita


@pytest.mark.parametrize(("ganancia", "obligado"), [("600", "no"), ("600.01", "si")])
def test_withheld_gains_count_with_capital_income_towards_1600(ganancia: str, obligado: str) -> None:
    r = _obligacion(
        rendimiento_capital_mobiliario=D(1000),
        ganancias_patrimoniales=D(ganancia),
        ganancias_sometidas_a_retencion=True,
    )
    assert r.obligado == obligado
    # The investment-fund exception of the same letter is said.
    assert any("fondos de inversión" in aviso for aviso in r.avisos)


def test_gains_not_withheld_only_within_the_1000_euro_rule() -> None:
    hasta = _obligacion(ganancias_patrimoniales=D(1000), ganancias_sometidas_a_retencion=False)
    assert hasta.obligado == "no" and hasta.regla is not None
    assert hasta.regla.referencia == "art. 96.2 LIRPF (penúltimo párrafo)" and "1.000 euros" in hasta.regla.cita
    encima = _obligacion(ganancias_patrimoniales=D("1000.01"), ganancias_sometidas_a_retencion=False)
    assert encima.obligado == "si"
    sin_saber = _obligacion(ganancias_patrimoniales=D(1200))
    assert sin_saber.obligado == "no_se_sabe" and _faltan(sin_saber) == {"ganancias_sometidas_a_retencion"}


@pytest.mark.parametrize(("perdida", "obligado"), [("-499.99", "no"), ("-500", "no_se_sabe")])
def test_losses_below_500_euros(perdida: str, obligado: str) -> None:
    r = _obligacion(rendimiento_capital_mobiliario=D(1000), ganancias_patrimoniales=D(perdida))
    assert r.obligado == obligado, r.respuesta
    # Filing lets the losses be offset later: always said.
    assert r.recomendacion is not None and "compensarlos" in r.recomendacion


@pytest.mark.parametrize(("neto", "obligado"), [("1000.01", "si"), ("1000", "no_se_sabe")])
def test_business_and_rental_income_only_under_the_1000_euro_rule(neto: str, obligado: str) -> None:
    actividad = _obligacion(rendimiento_actividad_neto=D(neto), alta_autonomo_en_el_ejercicio=False)
    alquiler = _obligacion(rendimiento_alquiler_neto=D(neto))
    assert actividad.obligado == alquiler.obligado == obligado
    if obligado == "no_se_sabe":
        # The rule counts gross income; the calculator only has the net figure.
        assert None in _faltan(actividad) and None in _faltan(alquiler)


def test_business_income_asks_whether_the_taxpayer_was_registered_as_self_employed() -> None:
    r = _obligacion(rendimiento_actividad_neto=D(600))
    assert r.obligado == "no_se_sabe" and "alta_autonomo_en_el_ejercicio" in _faltan(r)
    assert r.regla is not None and r.regla.referencia == "art. 96.2 LIRPF (último párrafo)"


# --------------------------------------------------------------------------- #
# Cases that oblige to file whatever the income
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("datos", "referencia"),
    [
        ({"alta_autonomo_en_el_ejercicio": True}, "art. 96.2 LIRPF (último párrafo)"),
        ({"aportacion_plan_pensiones": D(1000)}, "art. 96.4 LIRPF y art. 61.1 RIRPF"),
        ({"contribucion_empresa_plan_pensiones": D(500)}, "art. 96.4 LIRPF y art. 61.1 RIRPF"),
        ({"aportaciones_patrimonio_protegido": True}, "art. 96.4 LIRPF y art. 61.1 RIRPF"),
        ({"deduccion_doble_imposicion_internacional": True}, "art. 96.4 LIRPF y art. 61.1 RIRPF"),
        ({"pagos_vivienda_habitual_pre2013": D(6000)}, "disposición transitoria 18.ª.3 LIRPF"),
        (
            {"beneficiario_ingreso_minimo_vital": True},
            "art. 36.1.f) y 36.2.c) de la Ley 19/2021, del ingreso mínimo vital",
        ),
    ],
)
def test_must_file_anyway(datos: dict[str, Any], referencia: str) -> None:
    # 12.000 euros from one payer would not oblige by themselves.
    assert _un_pagador("12000").obligado == "no"
    r = _un_pagador("12000", **datos)
    assert r.obligado == "si" and r.regla is not None and r.regla.referencia == referencia
    assert r.respuesta.startswith("Sí, estás obligado")


def test_contributions_that_reduce_nothing_do_not_oblige() -> None:
    # Without income there is no base to reduce: art. 96.4 only covers contributions «que reduzcan la base».
    r = _obligacion(aportacion_plan_pensiones=D(1000))
    assert r.obligado == "no"
    assert any("no reducen la base imponible" in aviso for aviso in r.avisos)


def test_several_reasons_to_file_are_all_listed() -> None:
    r = _un_pagador("30000", alta_autonomo_en_el_ejercicio=True, beneficiario_ingreso_minimo_vital=True)
    assert r.obligado == "si"
    assert [x.referencia for x in [r.regla, *r.otras_reglas] if x is not None] == [
        "art. 96.2 LIRPF (último párrafo)",
        "art. 36.1.f) y 36.2.c) de la Ley 19/2021, del ingreso mínimo vital",
    ]


# --------------------------------------------------------------------------- #
# Recommendation, joint returns and the calculator's result
# --------------------------------------------------------------------------- #


def test_not_obliged_but_withholdings_exceed_the_tax_recommends_filing_with_the_refund() -> None:
    r = _un_pagador("12000", retenciones=D(500))
    resultado = calcular_irpf(IRPFInput(ejercicio=2026, rendimiento_trabajo_bruto=D(12000), retenciones=D(500)))
    assert r.obligado == "no"
    assert resultado.resultado < 0 and r.resultado_si_declara == resultado.resultado
    assert r.devolucion_estimada == -resultado.resultado
    assert r.recomendacion is not None and r.recomendacion.startswith("Aunque no estás obligado, te conviene declarar")
    sin_retenciones = _un_pagador("12000")
    assert sin_retenciones.devolucion_estimada is None
    assert sin_retenciones.recomendacion is not None and "No te hace falta" in sin_retenciones.recomendacion


def test_obliged_says_what_the_return_would_give() -> None:
    r = _un_pagador("30000", retenciones=D(9000))
    assert r.obligado == "si" and r.devolucion_estimada is not None and r.devolucion_estimada > 0
    assert r.recomendacion is not None and "a devolver" in r.recomendacion


def test_a_joint_return_is_not_answered_with_summed_figures() -> None:
    r = _obligacion(tributacion="conjunta_biparental", rendimiento_trabajo_bruto=D(12000))
    assert r.obligado == "no_se_sabe" and "persona a persona" in r.respuesta
    resultado = calcular_irpf(
        IRPFInput(ejercicio=2026, tributacion="conjunta_biparental", rendimiento_trabajo_bruto=D(1))
    )
    assert resultado.obligacion_declarar is None


def test_the_calculator_carries_the_same_answer() -> None:
    inp = IRPFInput(ejercicio=2026, rendimiento_trabajo_bruto=D(18000), num_pagadores_trabajo=1, **SIN_SUPUESTOS_96_3)
    resultado = calcular_irpf(inp)
    assert resultado.obligacion_declarar == obligado_a_declarar(inp)
    assert resultado.obligacion_declarar is not None and resultado.obligacion_declarar.obligado == "no"


def test_what_is_not_modelled_is_said() -> None:
    r = _un_pagador("12000")
    assert any("Letras del Tesoro" in aviso and "1.000 €" in aviso for aviso in r.avisos)
    assert any("autónomo" in aviso for aviso in r.avisos)
    adelantos = _un_pagador("12000", hijos_deduccion_maternidad=1, num_descendientes=1, num_descendientes_menores_3=1)
    assert any("por anticipado" in aviso for aviso in adelantos.avisos)
    assert r.parametros_no_verificados == [] and "BOE-A-2006-20764" in r.fuente

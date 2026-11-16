"""The planner's tax oracle is the optimiser's evaluator: same figures as calcular_irpf on the same scenario."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hacienda_gpt.planificador.fiscal import OraculoFiscal
from hacienda_gpt.tax.irpf import IRPFInput, calcular_irpf
from hacienda_gpt.tax.optimizador import Restricciones, _Punto
from hacienda_gpt.tax.params import load_params

D = Decimal
NOV = date(2026, 11, 16)
P = load_params(2026)
CAP = P.vivienda_habitual_transitoria.base_maxima
TIPO = P.vivienda_habitual_transitoria.tipo


def _inp(**kw: object) -> IRPFInput:
    datos: dict[str, object] = {
        "ejercicio": 2026,
        "comunidad_autonoma": "Madrid",
        "rendimiento_trabajo_bruto": D(42000),
        "cotizaciones_seguridad_social": D(2667),
        "retenciones": D(6500),
    }
    datos.update(kw)
    return IRPFInput.model_validate(datos)


def _oraculo(
    contribuyente: IRPFInput, conyuge: IRPFInput | None = None, *, pagos: Decimal = D(0), parte: Decimal = D(0)
):
    return OraculoFiscal(
        contribuyente,
        conyuge,
        conocidos=frozenset({"rendimiento_trabajo_bruto", "cotizaciones_seguridad_social", "estado_civil"}),
        conocidos_conyuge=frozenset({"rendimiento_trabajo_bruto"}),
        restricciones=Restricciones(aportacion_maxima_pensiones=D(5000), acepta_iliquidez=True),
        fecha=NOV,
        estado_civil="casado" if conyuge is not None else "soltero",
        params=P,
        pagos_vivienda=pagos,
        parte_conyuge=parte,
    )


def test_the_housing_deduction_base_is_capped_where_the_oracle_caches_it() -> None:
    # The oracle caches returns by min(payments, cap): the calculator must give the same tax above the cap.
    en_tope = calcular_irpf(_inp(pagos_vivienda_habitual_pre2013=CAP), P)
    encima = calcular_irpf(_inp(pagos_vivienda_habitual_pre2013=CAP + 1000), P)
    debajo = calcular_irpf(_inp(pagos_vivienda_habitual_pre2013=CAP - 100), P)
    assert en_tope.resultado == encima.resultado
    assert debajo.resultado - en_tope.resultado == (100 * TIPO).quantize(D("0.01"))


def test_a_pension_contribution_saves_what_the_calculator_says() -> None:
    oraculo = _oraculo(_inp())
    sin = oraculo.impuesto(D(0), (D(0), D(0)))
    con = oraculo.impuesto(D(0), (D(1500), D(0)))
    directo_sin = calcular_irpf(_inp(), P).resultado
    directo_con = calcular_irpf(_inp(aportacion_plan_pensiones=D(1500)), P).resultado
    assert (sin.total, con.total) == (directo_sin, directo_con)
    assert sin.total - con.total > 0
    # The base imponible does not move: the reduction is applied on it (art. 50.1 LIRPF).
    assert sin.base_imponible == con.base_imponible


def test_a_joint_return_depends_on_the_total_contribution_only() -> None:
    oraculo = _oraculo(_inp(), _inp(rendimiento_trabajo_bruto=D(6000), cotizaciones_seguridad_social=D(381)))
    opt = oraculo.base
    assert True in opt.modos
    m0, m1 = opt.max_pension
    total = min(m0 + m1, D(1800))
    totales = {
        opt.evaluar(_Punto(conjunta=True, pension=(a, total - a))).total
        for a in (max(D(0), total - m1), total / 2, min(m0, total))
        if a <= m0 and total - a <= m1
    }
    assert len(totales) == 1


def test_the_couple_shares_the_housing_deduction_in_separate_returns() -> None:
    oraculo = _oraculo(
        _inp(),
        _inp(rendimiento_trabajo_bruto=D(30000), cotizaciones_seguridad_social=D(1905)),
        pagos=D(8000),
        parte=D("0.5"),
    )
    opt = oraculo._opt(D(8000))
    ev = opt.evaluar(_Punto(conjunta=False))
    for _nombre, entrada, resultado in ev.declaraciones:
        assert entrada.pagos_vivienda_habitual_pre2013 == D(4000)
        linea = [x for x in resultado.lineas if x.concepto.startswith("Deducción por inversión en vivienda habitual")]
        assert linea and linea[0].importe == -(D(4000) * TIPO).quantize(D("0.01"))

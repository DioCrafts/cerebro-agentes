"""The mortgage lever: its net position is exactly what the two schedules say, and its breakpoints are where
the mortgage module drops an instalment."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from hacienda_gpt.modulos.hipoteca.calculo import euros
from hacienda_gpt.modulos.hipoteca.comisiones import comision_maxima
from hacienda_gpt.modulos.hipoteca.modelo import Prestamo
from hacienda_gpt.planificador.hipoteca import Crecimiento, PalancaHipoteca, meses_entre

D = Decimal
NOV = date(2026, 11, 16)
FIJA = Prestamo(
    capital_inicial=D(180000),
    fecha_firma=date(2015, 1, 10),
    plazo_meses=300,
    tipo="fijo",
    tipo_inicial=D(3),
    comisiones_pactadas=[],
)


def _palanca(prestamo: Prestamo = FIJA, rentabilidad: str = "2", anos: int = 5) -> PalancaHipoteca:
    horizonte = date(NOV.year + anos, NOV.month, NOV.day)
    return PalancaHipoteca(
        prestamo, hoy=NOV, fecha_ejecucion=NOV, crecimiento=Crecimiento.anual(D(rentabilidad), horizonte)
    )


@pytest.mark.parametrize("modalidad", ["plazo", "cuota"])
@pytest.mark.parametrize("anos", [1, 5, 20])
def test_the_net_position_is_the_debt_and_cash_difference_at_the_horizon(modalidad: str, anos: int) -> None:
    palanca = _palanca(anos=anos)
    horizonte = palanca.crecimiento.horizonte
    efecto = palanca.evaluar(D(10000), modalidad)
    base, plan = palanca.base, efecto.cuadro
    deuda = base.capital_pendiente(horizonte) - plan.capital_pendiente(horizonte)
    cuotas_plan = {f.fecha: f.cuota for f in plan.filas}
    liquido = -(efecto.importe + efecto.comision.importe) * palanca.crecimiento.factor(NOV)
    for fila in base.filas:
        if NOV < fila.fecha <= horizonte:
            liquido += (fila.cuota - cuotas_plan.get(fila.fecha, D(0))) * palanca.crecimiento.factor(fila.fecha)
    assert abs(efecto.valor - (deuda + liquido)) < D("1e-12")


def test_interest_of_the_first_instalment_by_hand() -> None:
    palanca = _palanca()
    efecto = palanca.evaluar(D(10000), "plazo")
    siguiente_base = palanca.base.proxima(NOV)
    siguiente_plan = efecto.cuadro.proxima(NOV)
    assert siguiente_base is not None and siguiente_plan is not None
    capital = palanca.capital_pendiente
    # 3 % a year, monthly: interest = capital × 0.0025, rounded to the cent (the module's convention).
    assert siguiente_base.intereses == euros(capital * D("0.0025"))
    assert siguiente_plan.intereses == euros((capital - 10000) * D("0.0025"))
    # «Reduce term» keeps the instalment (recomputed on the rounded-up count: never higher).
    assert siguiente_plan.cuota <= siguiente_base.cuota


def test_without_return_and_fee_the_value_is_the_interest_saved() -> None:
    palanca = _palanca(rentabilidad="0")
    efecto = palanca.evaluar(D(5000), "cuota")
    assert efecto.comision.importe == 0
    assert efecto.valor == efecto.intereses_ahorrados_horizonte > 0
    assert efecto.intereses_ahorrados_total > efecto.intereses_ahorrados_horizonte


def test_each_breakpoint_removes_one_instalment() -> None:
    palanca = _palanca()
    saltos = palanca.saltos_plazo(D(0), D(15000))
    assert len(saltos) >= 10
    for salto in saltos:
        con = len(palanca.evaluar(salto, "plazo").cuadro.filas)
        sin = len(palanca.evaluar(salto - D("0.01"), "plazo").cuadro.filas)
        assert con == sin - 1, salto


def test_the_fee_is_the_mortgage_module_cap() -> None:
    reciente = Prestamo(
        capital_inicial=D(150000), fecha_firma=date(2021, 3, 1), plazo_meses=300, tipo="fijo", tipo_inicial=D(2)
    )
    palanca = _palanca(reciente)
    efecto = palanca.evaluar(D(10000), "plazo")
    esperado = comision_maxima(reciente, palanca.normativa, NOV, D(10000))
    assert efecto.comision.importe == esperado.importe > 0
    assert efecto.comision.es_maximo
    # What a repayment can take with a budget: amount + fee within it.
    tope = palanca.maximo_con(D(10000))
    assert tope + palanca.comision(tope).importe <= 10000 < tope + D("0.01") + palanca.comision(tope + D("0.01")).importe


def test_growth_is_monthly_compounding_of_the_annual_rate() -> None:
    c = Crecimiento.anual(D(6), date(2031, 11, 16))
    assert meses_entre(NOV, c.horizonte) == 60
    assert abs(c.factor(NOV) - D("1.06") ** 5) < D("1e-20")
    assert c.factor(c.horizonte) == 1

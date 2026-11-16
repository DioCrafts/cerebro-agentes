"""The planner and the bridge to the tax module count the same base for the pre-2013 housing deduction.

Both read it from the mortgage module (``modulos/hipoteca/deduccion.py``: ``pagos_del_ano`` and
``base_deducible``): capital and interest of the instalments, the early repayments and their fees.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from asistente.ambitos.hipoteca.deduccion import base_deducible, cargar_fundamento, pagos_del_ano
from asistente.ambitos.hipoteca.modelo import Prestamo
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.modulos.puentes import _hipoteca
from asistente.nucleo.planificador.hipoteca import Crecimiento, PalancaHipoteca

D = Decimal
HOY = date(2026, 6, 1)
# A fixed-rate loan (no index needed) for a home bought in 2010, with an early repayment and its fee in 2026,
# and a deed fee so that a new repayment pays one too.
PRESTAMO = {
    "capital_inicial": "150000",
    "fecha_firma": "2010-03-15",
    "plazo_meses": 360,
    "tipo": "fijo",
    "tipo_inicial": "4",
    "vivienda_habitual": True,
    "adquisicion_antes_2013": True,
    "comisiones_pactadas": [{"porcentaje": "0.5"}],
    "amortizaciones_realizadas": [{"fecha": "2026-03-10", "importe": "3000", "comision_pagada": "15"}],
}
CASO = CaseState(case_id="c1", user_id="u1", jurisdiction="ES", tax_period="2026", facts=[])


def _puente(prestamo: dict) -> Decimal:
    propuesta = _hipoteca(CASO, {"hipoteca_prestamo": prestamo}, HOY)
    assert propuesta is not None
    return D(str(propuesta.valor))


def _palanca() -> PalancaHipoteca:
    return PalancaHipoteca(
        Prestamo.model_validate(PRESTAMO),
        hoy=HOY,
        fecha_ejecucion=HOY,
        crecimiento=Crecimiento.anual(D(2), date(2030, 6, 1)),
    )


def test_every_concept_the_law_counts_and_the_module_knows_is_in_the_shared_base() -> None:
    claves = {c.clave for c in cargar_fundamento().incluidos()}
    pagos = {"capital": D(1), "intereses": D(2), "amortizaciones_anticipadas": D(4)}
    pagos["comisiones_amortizacion_anticipada"] = D(8)
    assert claves == set(pagos)
    assert base_deducible(pagos) == D(15)
    # A concept the law counts but the module does not know is never added, whatever the caller passes.
    assert base_deducible({**pagos, "gastos_adquisicion": D(1000)}) == D(15)


def test_the_planner_and_the_bridge_give_the_same_base_fee_included() -> None:
    palanca = _palanca()
    sin_amortizar = palanca.evaluar(D(0), None).pagos_por_ano[2026]
    assert sin_amortizar == _puente(PRESTAMO)
    # The fee of the repayment already made is in both.
    prestamo = Prestamo.model_validate(PRESTAMO)
    pagos = pagos_del_ano(palanca.base, prestamo.amortizaciones_realizadas, 2026)
    assert pagos["comisiones_amortizacion_anticipada"] == D(15)
    assert sin_amortizar == sum(pagos.values(), D(0))


def test_a_repayment_the_planner_weighs_counts_what_the_bridge_would_propose_once_made() -> None:
    palanca = _palanca()
    importe = D(10000)
    efecto = palanca.evaluar(importe, "plazo")
    assert efecto.comision.importe > 0
    hecho = {
        **PRESTAMO,
        "amortizaciones_realizadas": [
            *PRESTAMO["amortizaciones_realizadas"],  # type: ignore[list-item]
            {
                "fecha": HOY.isoformat(),
                "importe": str(importe),
                "modalidad": "plazo",
                "comision_pagada": str(efecto.comision.importe),
            },
        ],
    }
    assert efecto.pagos_por_ano[2026] == _puente(hecho)
    # The amount and its fee both enter the year they are paid, beside the instalments of the new schedule.
    base = palanca.evaluar(D(0), None).pagos_por_ano[2026]
    cuotas = sum(efecto.cuadro.pagos_del_ano(2026), D(0)) - sum(palanca.base.pagos_del_ano(2026), D(0))
    assert efecto.pagos_por_ano[2026] - base == importe + efecto.comision.importe + cuotas

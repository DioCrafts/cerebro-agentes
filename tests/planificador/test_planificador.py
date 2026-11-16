"""The household planner on hand-checked households: every figure is re-derived here from the modules'
calculators (calcular_irpf, the mortgage schedule, the aid evaluators), never from the planner itself."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from asistente.ambitos.ayudas.parametros import load_prestaciones
from asistente.ambitos.fiscal.calculo.irpf import IRPFInput, calcular_irpf
from asistente.ambitos.fiscal.calculo.params import load_params
from asistente.ambitos.fiscal.profile import DatosInvalidosError
from asistente.ambitos.hipoteca import pagos_del_ejercicio
from asistente.ambitos.hipoteca.modelo import Prestamo
from asistente.nucleo.modulos.contrato import ContextoModulo
from asistente.nucleo.planificador import Asignacion, EntradaPlan, Planificador, planificar
from asistente.nucleo.planificador.hipoteca import Crecimiento, PalancaHipoteca

D = Decimal
HOY = date(2026, 11, 16)
CENT = D("0.01")
P2026 = load_params(2026)

HIPOTECA_FIJA = {
    "capital_inicial": 200000,
    "fecha_firma": "2018-03-15",
    "plazo_meses": 360,
    "tipo": "fijo",
    "tipo_inicial": 2.5,
    "comisiones_pactadas": [],
}
# A salaried parent of two in Madrid with a mortgage taken in 2018.
FAMILIA = {
    "rendimiento_trabajo_bruto": 45000,
    "cotizaciones_seguridad_social": 2860,
    "retenciones": 7000,
    "comunidad_autonoma": "Madrid",
    "num_descendientes": 2,
    "estado_civil": "soltero",
    "hipoteca_prestamo": HIPOTECA_FIJA,
}
ACTIVOS = {"fiscal", "hipoteca", "ayudas"}


def _irpf(**cambios: object) -> Decimal:
    datos: dict[str, object] = {
        "ejercicio": 2026,
        "rendimiento_trabajo_bruto": D(45000),
        "cotizaciones_seguridad_social": D(2860),
        "retenciones": D(7000),
        "comunidad_autonoma": "Madrid",
        "num_descendientes": 2,
    }
    datos.update(cambios)
    return calcular_irpf(IRPFInput.model_validate(datos), P2026).resultado


def _plan(entrada: dict, facts: dict = FAMILIA, activos: set[str] = ACTIVOS):
    return planificar(entrada, facts=facts, modulos_activos=activos, hoy=HOY)


# --------------------------------------------------------------------------- #
# Mortgage + salary + children
# --------------------------------------------------------------------------- #

FAMILIA_ENTRADA = {
    "importe_disponible": 20000,
    "horizonte_anos": 5,
    "rentabilidad_alternativa_pct": 2,
    "gastos_mensuales": 2000,
    "liquidez_actual": 6000,
}


@pytest.fixture(scope="module")
def plan_familia():
    return _plan(FAMILIA_ENTRADA)


def test_the_cushion_is_kept(plan_familia) -> None:
    # Six months of 2.000 € = 12.000 €; with 6.000 € already saved, at most 14.000 € can be used.
    assert plan_familia.colchon_objetivo == 12000
    assert plan_familia.valoracion.usado <= 14000
    assert plan_familia.liquidez_despues >= 12000
    assert any("como mucho 14.000,00 €" in s for s in plan_familia.supuestos)


def test_the_components_are_the_modules_figures_by_hand(plan_familia) -> None:
    v = plan_familia.valoracion
    a = v.asignacion
    assert a.plan_pensiones_contribuyente == 1500  # the art. 52 cap in the data, all of it useful here
    horizonte = date(2031, 11, 16)
    crecimiento = Crecimiento.anual(D(2), horizonte)
    # IRPF: what calcular_irpf says with and without the contribution, at the filing date (30/06/2027).
    ahorro = _irpf() - _irpf(aportacion_plan_pensiones=D(1500))
    componentes = {c.concepto.split(":")[0] if c.concepto.startswith("IRPF") else c.concepto: c for c in v.componentes}
    irpf = componentes["IRPF 2026"]
    assert irpf.naturaleza == "diferimiento"
    assert irpf.importe == (ahorro * crecimiento.factor(date(2027, 6, 30))).quantize(CENT)
    # Mortgage: the interest of the two schedules within the horizon.
    palanca = PalancaHipoteca(
        Prestamo.model_validate(HIPOTECA_FIJA), hoy=HOY, fecha_ejecucion=HOY, crecimiento=crecimiento
    )
    efecto = palanca.evaluar(a.amortizar_hipoteca, a.modalidad)
    base = sum((f.intereses for f in palanca.base.filas if HOY < f.fecha <= horizonte), D(0))
    plan = sum((f.intereses for f in efecto.cuadro.filas if HOY < f.fecha <= horizonte), D(0))
    assert componentes[f"Intereses de la hipoteca que te ahorras hasta el {horizonte:%d/%m/%Y}"].importe == base - plan
    # The objective (deferral view) is the sum of its lines, the redemption tax only in the definitive view.
    lineas = sum((c.importe for c in v.componentes if not c.concepto.startswith("Impuesto al rescatar")), D(0))
    assert abs(lineas - v.total_diferimiento) <= D("0.05")
    assert v.total == v.total_diferimiento > v.total_definitivo


def test_the_year_tax_is_the_optimiser_plan_for_the_same_allocation(plan_familia) -> None:
    fiscal = plan_familia.plan_fiscal
    assert fiscal is not None
    assert fiscal.optimo.resultado_total == plan_familia.valoracion.impuesto_ejercicio
    assert fiscal.optimo.resultado_total == _irpf(aportacion_plan_pensiones=D(1500))


def test_the_plan_beats_every_simple_alternative(plan_familia) -> None:
    total = plan_familia.valoracion.total
    for escenario in plan_familia.escenarios:
        if escenario.factible:
            assert escenario.total <= total, escenario.nombre
    nombres = [e.nombre for e in plan_familia.escenarios]
    assert nombres[0].startswith("No hacer nada") and nombres[-1] == "Plan óptimo"


def test_inside_the_plan_and_alone(plan_familia) -> None:
    por_id = {p.id: p for p in plan_familia.palancas}
    pension, hipoteca = por_id["plan_pensiones_contribuyente"], por_id["amortizar_hipoteca"]
    # No tax coupling here (no pre-2013 deduction): the pension is worth the same in the plan and alone.
    assert pension.valor_marginal == pension.valor_aislado
    # The repayment alone would take all the usable money; in the plan it shares it with the pension.
    assert hipoteca.importe_aislado > hipoteca.importe
    assert plan_familia.suma_aislados != plan_familia.valoracion.total
    assert any("el plan solo puede usar 14.000,00 €" in x for x in plan_familia.explicacion_interaccion)


def test_a_better_alternative_return_keeps_the_money() -> None:
    plan = _plan({**FAMILIA_ENTRADA, "rentabilidad_alternativa_pct": 6})
    assert plan.valoracion.asignacion.amortizar_hipoteca == 0
    assert {n.id: n.causa for n in plan.no_usadas}["amortizar_hipoteca"] == "no_compensa"


def test_without_return_everything_usable_goes_to_the_mortgage() -> None:
    plan = _plan(
        {
            **FAMILIA_ENTRADA,
            "rentabilidad_alternativa_pct": 0,
            "restricciones": {
                "excluidas": ["plan_pensiones_contribuyente"],
                "motivos": {"plan_pensiones_contribuyente": "No quiero"},
            },
        }
    )
    a = plan.valoracion.asignacion
    assert a.plan_pensiones_contribuyente == 0
    # «Reduce term» drops whole instalments: the best repayment is at most one instalment below all the money.
    assert D(13000) <= a.amortizar_hipoteca <= D(14000)
    no_usada = {n.id: n for n in plan.no_usadas}["plan_pensiones_contribuyente"]
    assert no_usada.causa == "restricciones" and "No quiero" in no_usada.detalle


def test_restrictions_cap_and_modality() -> None:
    plan = _plan(
        {
            **FAMILIA_ENTRADA,
            "restricciones": {
                "maximos": {"amortizar_hipoteca": 2000},
                "modalidades_excluidas": ["plazo"],
                "origen": "memoria",
            },
        }
    )
    a = plan.valoracion.asignacion
    assert a.amortizar_hipoteca <= 2000
    assert a.modalidad in (None, "cuota")


def test_inactive_modules_are_not_read() -> None:
    plan = _plan(FAMILIA_ENTRADA, activos={"fiscal"})
    assert plan.valoracion.asignacion.amortizar_hipoteca == 0
    assert {n.id: n.causa for n in plan.no_usadas}["amortizar_hipoteca"] == "modulo_inactivo"
    assert plan.ayudas == []
    assert any("módulo de ayudas no está activado" in x for x in plan.no_modelado)


# --------------------------------------------------------------------------- #
# A couple with a mortgage on a home bought before 2013
# --------------------------------------------------------------------------- #

PAREJA = {
    "rendimiento_trabajo_bruto": 32000,
    "cotizaciones_seguridad_social": 2030,
    "retenciones": 4500,
    "comunidad_autonoma": "Castilla y León",
    "estado_civil": "casado",
    "num_descendientes": 1,
    "hipoteca_prestamo": {
        "capital_inicial": 150000,
        "fecha_firma": "2009-06-10",
        "plazo_meses": 360,
        "tipo": "fijo",
        "tipo_inicial": 4,
        "comisiones_pactadas": [],
        "adquisicion_antes_2013": True,
        "vivienda_habitual": True,
    },
}
CONYUGE = {"rendimiento_trabajo_bruto": 21000, "cotizaciones_seguridad_social": 1333, "retenciones": 2100}
PAREJA_ENTRADA = {
    "importe_disponible": 8000,
    "horizonte_anos": 5,
    "rentabilidad_alternativa_pct": 1.5,
    "conyuge": CONYUGE,
    "parte_hipoteca_conyuge": 0.5,
}


@pytest.fixture(scope="module")
def plan_pareja():
    return _plan(PAREJA_ENTRADA, facts=PAREJA)


def test_the_pre_2013_payments_are_the_bridge_figure_plus_the_repayment(plan_pareja) -> None:
    perfil = {"hipoteca_prestamo": PAREJA["hipoteca_prestamo"]}
    pagos = pagos_del_ejercicio(ContextoModulo(perfil=perfil, hoy=HOY), 2026)
    assert pagos is not None
    puente = pagos["capital"] + pagos["intereses"] + pagos["amortizaciones_anticipadas"]
    assert any(f"según el cuadro ({_eur(puente)})" in s for s in plan_pareja.supuestos)
    a = plan_pareja.valoracion.asignacion
    assert a.amortizar_hipoteca > 0
    # Each spouse's separate return deducts on half of what the household pays (instalments + repayment).
    fiscal = plan_pareja.plan_fiscal
    assert fiscal is not None
    total = D(0)
    planif = Planificador(EntradaPlan.model_validate(PAREJA_ENTRADA), facts=PAREJA, modulos_activos=ACTIVOS, hoy=HOY)
    pagos_plan = planif._pagos(planif._efecto(a), 2026)
    mitad = (pagos_plan * D("0.5")).quantize(CENT)
    assert abs(pagos_plan - puente - a.amortizar_hipoteca) < 1  # in «reduce term» the instalments stay
    for datos, pension, parte in (
        (
            {k: v for k, v in PAREJA.items() if k not in ("hipoteca_prestamo", "estado_civil")},
            a.plan_pensiones_contribuyente,
            pagos_plan - mitad,
        ),
        ({**CONYUGE, "comunidad_autonoma": "Castilla y León", "num_descendientes": 1}, a.plan_pensiones_conyuge, mitad),
    ):
        entrada = IRPFInput.model_validate(
            {
                **datos,
                "ejercicio": 2026,
                "aportacion_plan_pensiones": pension,
                "pagos_vivienda_habitual_pre2013": parte,
                # The child is common to both separate returns: each applies half the minimum (art. 61.1.ª).
                "descendientes_compartidos": True,
            }
        )
        res = calcular_irpf(entrada, P2026)
        total += res.resultado
        assert any(x.concepto.startswith("Deducción por inversión en vivienda habitual") for x in res.lineas)
    if plan_pareja.valoracion.tributacion == "individual":
        assert plan_pareja.valoracion.impuesto_ejercicio == total


def test_the_housing_deduction_conditions_are_said(plan_pareja) -> None:
    # Rate and cap are verified against the 2012 wording (tax/data/irpf_*.json): no "not verified" warning.
    assert not any("Deducción por vivienda" in x and "no se ha contrastado" in x for x in plan_pareja.avisos)
    assert any("disposición transitoria 18.ª" in x for x in plan_pareja.avisos)


def test_both_views_of_the_pension(plan_pareja) -> None:
    v = plan_pareja.valoracion
    pension = v.asignacion.plan_pensiones_contribuyente + v.asignacion.plan_pensiones_conyuge
    if pension:
        rescate = [c for c in v.componentes if c.concepto.startswith("Impuesto al rescatar")]
        assert rescate and v.total_definitivo == v.total_diferimiento + rescate[0].importe
    else:
        assert v.total_definitivo == v.total_diferimiento


def test_a_redemption_rate_switches_the_objective_to_the_definitive_view() -> None:
    plan = _plan({**PAREJA_ENTRADA, "tipo_rescate_pct": 30}, facts=PAREJA)
    assert plan.vista == "definitivo" and plan.valoracion.total == plan.valoracion.total_definitivo
    with pytest.raises(DatosInvalidosError, match="tipo_rescate_pct"):
        _plan({**PAREJA_ENTRADA, "valorar_pensiones": "definitivo"}, facts=PAREJA)


# --------------------------------------------------------------------------- #
# A low-income household near an aid's income limit
# --------------------------------------------------------------------------- #

HOGAR_AYUDAS = {
    "rendimiento_trabajo_bruto": 30000,
    "cotizaciones_seguridad_social": 1905,
    "retenciones": 2500,
    "comunidad_autonoma": "Andalucía",
    "estado_civil": "casado",
    "descendientes": '[{"edad": 6}, {"edad": 9}]',
    "datos_conyuge": '{"rendimiento_trabajo_bruto": 18000, "cotizaciones_seguridad_social": 1143}',
    "ayudas_adultos_hogar": 2,
    "ayudas_menores_hogar": 2,
    "ayudas_ingresos_hogar": 49800,
    "ayudas_base_imponible_hogar": 44000,
    "ayudas_patrimonio_neto_hogar": 15000,
}


def _limite_capi() -> Decimal:
    # Art. 11.6 Ley 19/2021: 300 % of the annex I threshold (guaranteed income × scale × 12) for 2 adults, 2 minors.
    p = load_prestaciones(2026)
    umbral = (p.imv.renta_garantizada_individual * p.imv.escala(2, 2) * 12).quantize(CENT)
    return (umbral * p.capi.umbral_ingresos).quantize(CENT)


def test_a_pension_contribution_would_cross_the_child_benefit_limit() -> None:
    margen = _limite_capi() - D(49800)
    assert 0 < margen < 1000
    entrada = {"importe_disponible": 3000, "horizonte_anos": 1}
    libre = _plan({**entrada, "proteger_ayudas": False}, facts=HOGAR_AYUDAS)
    capi = {e.id: e for e in libre.ayudas}["capi"]
    # Unprotected, the contributions lower the IRPF accrued by more than the margin: the CAPI income test fails.
    assert capi.cambio is not None and capi.cambio >= margen
    assert (capi.estado_antes, capi.estado_despues) == ("cumple", "no_cumple")
    protegido = _plan(entrada, facts=HOGAR_AYUDAS)
    capi = {e.id: e for e in protegido.ayudas}["capi"]
    assert capi.estado_despues == capi.estado_antes == "cumple"
    assert capi.cambio is not None and capi.cambio < margen
    assert any(f"{_eur(margen)} o más" in x for x in protegido.explicacion_interaccion)
    # The protected plan is worth less (it gives up tax saving), the unprotected one says what it costs.
    assert protegido.valoracion.total < libre.valoracion.total


def test_the_electricity_social_bonus_base_does_not_move_with_a_pension() -> None:
    plan = _plan({"importe_disponible": 3000, "horizonte_anos": 1}, facts=HOGAR_AYUDAS)
    bono = {e.id: e for e in plan.ayudas}["bono_social_electrico"]
    assert bono.cambio == 0
    assert "base liquidable" in bono.explicacion
    assert "artículos 48 y 49" in bono.cita and "art. 50.1 LIRPF" in bono.cita
    assert plan.valoracion.asignacion.plan_pensiones_contribuyente > 0


# --------------------------------------------------------------------------- #
# Determinism and a brute-force check
# --------------------------------------------------------------------------- #


def test_same_input_same_plan(plan_familia) -> None:
    otra = _plan(FAMILIA_ENTRADA)
    assert otra.model_dump_json() == plan_familia.model_dump_json()


def test_same_plan_with_any_hash_seed() -> None:
    raiz = Path(__file__).resolve().parents[2]
    codigo = (
        "import hashlib, sys; from datetime import date; from tests.planificador.test_planificador import "
        "FAMILIA_ENTRADA, FAMILIA, ACTIVOS; from asistente.nucleo.planificador import planificar; "
        "p = planificar(FAMILIA_ENTRADA, facts=FAMILIA, modulos_activos=ACTIVOS, hoy=date(2026, 11, 16)); "
        "print(hashlib.sha256(p.model_dump_json().encode()).hexdigest())"
    )
    huellas = set()
    for semilla in ("1", "4242"):
        salida = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=raiz,
            env={**os.environ, "PYTHONHASHSEED": semilla},
            capture_output=True,
            text=True,
            check=True,
        )
        huellas.add(salida.stdout.strip().splitlines()[-1])
    assert len(huellas) == 1


def test_brute_force_on_a_small_grid() -> None:
    entrada = EntradaPlan.model_validate(
        {"importe_disponible": 4000, "horizonte_anos": 3, "rentabilidad_alternativa_pct": 1, "resolucion": 500}
    )
    planif = Planificador(entrada, facts=FAMILIA, modulos_activos={"fiscal", "hipoteca"}, hoy=HOY)
    plan = planificar(entrada, facts=FAMILIA, modulos_activos={"fiscal", "hipoteca"}, hoy=HOY)
    assert planif.hipoteca is not None
    mejor = None
    for p0 in range(0, 1501, 25):
        resto = D(4000) - p0
        for h in {*range(0, int(resto) + 1, 250), int(planif.hipoteca.maximo_con(resto))}:
            for modo in ("plazo", "cuota") if h else (None,):
                a = Asignacion(amortizar_hipoteca=D(h), modalidad=modo, plan_pensiones_contribuyente=D(p0))
                r = planif.rapida(a)
                if r.factible and (mejor is None or r.total > mejor):
                    mejor = r.total
    assert mejor is not None
    assert plan.valoracion.total >= mejor.quantize(CENT)
    assert plan.busqueda.metodo == "exhaustiva" and plan.busqueda.resolucion == 500


def _eur(importe: Decimal) -> str:
    texto = f"{abs(importe):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if importe < 0 else ''}{texto} €"


def test_bad_input_is_a_spanish_error() -> None:
    with pytest.raises(DatosInvalidosError, match="Datos del plan no válidos"):
        _plan({"importe_disponible": -1})
    with pytest.raises(DatosInvalidosError, match="presupuesto"):
        _plan({"importe_disponible": 100, "donativo_previsto": 50, "presupuesto_donativos": 10})


def test_the_plan_serialises_to_json(plan_familia) -> None:
    datos = json.loads(plan_familia.model_dump_json())
    assert datos["busqueda"]["metodo"] == "exhaustiva"
    assert hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()

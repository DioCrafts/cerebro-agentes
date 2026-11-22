"""The deterministic calculator: amounts and fixed-rate loans, and its figures through the figures gate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from asistente.ambitos.hipoteca import MODULO as HIPOTECA
from asistente.ambitos.suscripciones import MODULO as SUSCRIPCIONES
from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.calculo.herramientas import (
    IMPORTES,
    PRESTAMO,
    ejecutar_importes,
    ejecutar_prestamo,
    herramientas_para_cerebro,
)
from asistente.nucleo.calculo.operaciones import (
    CalculoError,
    Importe,
    calcular_importes,
    calcular_prestamo,
    leer_decimal,
)
from asistente.nucleo.cerebro.cerebro import cifras_de_herramienta
from asistente.nucleo.cerebro.colaboracion import Despachador, EspecialistaDeModulo, revisar_cifras
from asistente.nucleo.cerebro.colaboracion.modulos import cifra_de_herramienta
from asistente.nucleo.cerebro.contrato import Encargo, InformeEspecialista
from tests.cerebro.colaboracion.fakes import ProveedorGuion, herramientas, texto, turno

D = Decimal
SUSCRIPCIONES_MES = [
    {"concepto": "Netflix", "valor": 13.99, "periodicidad": "mes"},
    {"concepto": "Spotify", "valor": "10,99", "periodicidad": "mes"},
    {"concepto": "HBO", "valor": "9.99", "periodicidad": "mes"},
    {"concepto": "gimnasio", "valor": 39.90, "periodicidad": "mes"},
]
HIPOTECA_ARGS = {"capital": "180.000", "tipo_anual_pct": "3,2", "plazo_anos": 25, "amortizacion": 20000}


def valores(resultado: Any) -> dict[str, Decimal]:
    return {c.concepto: c.valor for c in resultado.cifras}


# --- numbers in --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (13.99, "13.99"),
        ("13,99", "13.99"),
        ("180.000", "180000"),
        ("1.234,56 €", "1234.56"),
        ("3.2", "3.2"),
        (7, "7"),
        ("3,2 %", "3.2"),
    ],
)
def test_numbers_are_read_exactly(entrada: Any, esperado: str) -> None:
    assert leer_decimal(entrada, "x") == D(esperado)


@pytest.mark.parametrize("entrada", ["2+2", "__import__('os')", None, True, "1e400", float("inf"), [1]])
def test_nothing_is_evaluated(entrada: Any) -> None:
    with pytest.raises(CalculoError):
        leer_decimal(entrada, "x")


# --- amounts -----------------------------------------------------------------------------------------------------


def test_the_subscriptions_question_gives_898_44_a_year() -> None:
    r = ejecutar_importes({"operacion": "sumar", "importes": SUSCRIPCIONES_MES})
    assert not r.es_error
    v = valores(r)
    assert v["total"] == D("74.87") and v["total al año (×12)"] == D("898.44")
    assert v["gimnasio al año"] == D("478.80")
    assert "898,44 €/año" in r.contenido and "× 12" in r.contenido  # the assumption is said
    assert {c.unidad for c in r.cifras} == {"EUR/mes", "EUR/año"}


def test_mixed_periods_are_converted_and_one_offs_refused() -> None:
    r = calcular_importes("sumar", [Importe("luz", D("50"), "mes"), Importe("seguro", D("300"), "año")])
    assert [(x.concepto, x.valor) for x in r.lineas[:2]] == [
        ("total al mes", D("75.00")),
        ("total al año", D("900.00")),
    ]
    with pytest.raises(CalculoError):
        calcular_importes("sumar", [Importe("a", D(1), "mes"), Importe("b", D(2), "unico")])


@pytest.mark.parametrize(
    ("operacion", "importes", "extra", "concepto", "valor"),
    [
        ("restar", [100, "30,5"], {}, "diferencia", "69.50"),
        ("multiplicar", [{"concepto": "cuota", "valor": "10,005"}], {"factor": 3}, "cuota × 3", "30.02"),
        ("anualizar", [{"concepto": "luz", "valor": 50, "periodicidad": "mes"}], {}, "luz al año", "600.00"),
        ("mensualizar", [{"concepto": "IBI", "valor": 400, "periodicidad": "año"}], {}, "IBI al mes", "33.33"),
        ("porcentaje", [{"concepto": "base", "valor": 100}], {"porcentaje": 21}, "21 % de base", "21.00"),
        ("variacion", [80, 100], {}, "variación porcentual", "25.00"),
    ],
)
def test_each_operation(operacion: str, importes: list[Any], extra: dict[str, Any], concepto: str, valor: str) -> None:
    r = ejecutar_importes({"operacion": operacion, "importes": importes, **extra})
    assert not r.es_error, r.contenido
    assert valores(r)[concepto] == D(valor)


def test_rounding_is_explicit_and_half_up() -> None:
    r = ejecutar_importes(
        {"operacion": "mensualizar", "importes": [{"valor": 100, "periodicidad": "año"}], "decimales": 4}
    )
    assert valores(r)["importe 1 al mes"] == D("8.3333")
    r = ejecutar_importes({"operacion": "multiplicar", "importes": [D("0.125")], "factor": 1})
    assert next(iter(valores(r).values())) == D("0.13")


@pytest.mark.parametrize(
    "args",
    [
        {"operacion": "dividir", "importes": [1]},
        {"operacion": "sumar", "importes": []},
        {"operacion": "multiplicar", "importes": [1]},
        {"operacion": "variacion", "importes": [0, 5]},
        {"operacion": "sumar", "importes": ["os.system"]},
    ],
)
def test_bad_arguments_are_errors_not_exceptions(args: dict[str, Any]) -> None:
    r = ejecutar_importes(args)
    assert r.es_error and r.contenido.startswith("No he podido calcular")


# --- loans -------------------------------------------------------------------------------------------------------


def test_the_mortgage_question() -> None:
    r = ejecutar_prestamo(HIPOTECA_ARGS)
    assert not r.es_error, r.contenido
    v = valores(r)
    assert v["cuota mensual"] == D("872.42")
    assert v["número de cuotas"] == 300
    assert v["reducir cuota: nueva cuota mensual"] == D("775.49")
    assert v["reducir plazo: cuotas menos"] == 47
    assert v["reducir plazo: intereses ahorrados"] > v["reducir cuota: intereses ahorrados"] > 0
    assert v["diferencia de intereses ahorrados (plazo − cuota)"] == (
        v["reducir plazo: intereses ahorrados"] - v["reducir cuota: intereses ahorrados"]
    )
    assert "Tipo fijo del 3,20 %" in r.contenido and "Sin comisiones" in r.contenido


def test_the_loan_reuses_the_mortgage_engine() -> None:
    from asistente.ambitos.hipoteca.calculo import cuota_francesa

    r = calcular_prestamo(D(100000), D(3), 240, hoy=date(2026, 1, 1))
    assert r.lineas[0].valor == cuota_francesa(D(100000), D(3), 240)
    # Zero interest: the instalment is capital / n and no interest is paid.
    r0 = calcular_prestamo(D(12000), D(0), 12, hoy=date(2026, 1, 1))
    assert [x.valor for x in r0.lineas[:2]] == [D("1000.00"), D("0.00")]


def test_repaying_after_some_instalments_and_the_schedule() -> None:
    r = calcular_prestamo(
        D(180000), D("3.2"), 300, amortizacion=D(20000), tras_cuota=12, filas_cuadro=3, hoy=date(2026, 1, 1)
    )
    v = {x.concepto: x.valor for x in r.lineas}
    assert v["capital pendiente antes de amortizar"] == D("175221.27")
    assert v["cuota 1: intereses"] == D("480.00")  # 180.000 × 3,2 % / 12
    assert v["cuota 1: capital amortizado"] == D("392.42")
    assert "año 1: capital pendiente al final" in v
    assert len(r.lineas) <= 60  # fits a report


def test_repaying_everything_cancels_the_loan() -> None:
    r = calcular_prestamo(D(10000), D(5), 24, amortizacion=D(50000), hoy=date(2026, 1, 1))
    v = {x.concepto: x.valor for x in r.lineas}
    assert v["reducir plazo: intereses ahorrados"] == v["intereses totales"]


@pytest.mark.parametrize(
    "args",
    [
        {"capital": 0, "tipo_anual_pct": 3, "plazo_meses": 12},
        {"capital": 1000, "tipo_anual_pct": 40, "plazo_meses": 12},
        {"capital": 1000, "tipo_anual_pct": 3},
        {"capital": 1000, "tipo_anual_pct": 3, "plazo_meses": 12, "plazo_anos": 2},
        {"capital": 1000, "tipo_anual_pct": 3, "plazo_meses": 12, "tras_cuota": 12},
    ],
)
def test_bad_loans_are_errors(args: dict[str, Any]) -> None:
    assert ejecutar_prestamo(args).es_error


# --- availability ------------------------------------------------------------------------------------------------


def test_the_specialists_have_the_tools() -> None:
    assert {"suscripciones_calculo_importes"} <= {h.spec.name for h in SUSCRIPCIONES.herramientas}
    assert {"hipoteca_calculo_importes", "hipoteca_calculo_prestamo"} <= {h.spec.name for h in HIPOTECA.herramientas}
    assert "hipoteca_calculo_prestamo" in HIPOTECA.instrucciones and "tipo fijo" in HIPOTECA.instrucciones
    assert "suscripciones_calculo_importes" in SUSCRIPCIONES.instrucciones
    from asistente.nucleo.modulos.registro import problemas_de

    assert problemas_de(HIPOTECA) == [] and problemas_de(SUSCRIPCIONES) == []


# --- the figures gate --------------------------------------------------------------------------------------------


def _informe(resultado: Any, especialista: str, herramienta: str, ledger: EvidenceLedger) -> InformeEspecialista:
    fid = ledger.register(
        text=resultado.contenido, metadata={"source": f"modulo://{especialista}/{herramienta}/1"}
    ).fragment_id
    cifras = [
        c
        for i, d in enumerate(resultado.cifras, 1)
        if (c := cifra_de_herramienta(d, especialista=especialista, fragment_id=fid, herramienta=herramienta, numero=i))
    ]
    return InformeEspecialista(
        encargo_id="e1",
        especialista=especialista,
        nombre=especialista.title(),
        estado="ok",
        cifras=cifras,
        fragment_ids=[fid],
    )


USUARIO_SUSC = "Netflix 13,99, Spotify 10,99, HBO 9,99, gimnasio 39,90, ¿cuánto gasto al año?"


def test_the_gate_accepts_the_tools_yearly_total() -> None:
    ledger = EvidenceLedger()
    informe = _informe(
        ejecutar_importes({"operacion": "sumar", "importes": SUSCRIPCIONES_MES}), "suscripciones", IMPORTES, ledger
    )
    respuesta = "Gastas 74,87 € al mes, es decir 898,44 € al año [F1]; solo el gimnasio son 478,80 € al año."
    resultado = revisar_cifras(respuesta, [informe], ledger=ledger, textos_usuario=[USUARIO_SUSC])
    assert resultado.violaciones == []
    propias = {c.id: c for c in informe.cifras}
    usadas = [a for a in resultado.atribuciones if not a.del_usuario]
    assert usadas and all(
        a.especialista == "suscripciones" and propias[a.cifra_id].herramienta == IMPORTES for a in usadas
    )


def test_the_gate_rejects_the_models_own_arithmetic() -> None:
    ledger = EvidenceLedger()
    sin_herramienta = revisar_cifras("Gastas 898,44 € al año.", [], ledger=ledger, textos_usuario=[USUARIO_SUSC])
    assert [v.literal for v in sin_herramienta.violaciones] == ["898,44 € al año"]
    # With the tool's figures, a number the tool did NOT produce is still rejected (wrong total, a semester).
    informe = _informe(
        ejecutar_importes({"operacion": "sumar", "importes": SUSCRIPCIONES_MES}), "suscripciones", IMPORTES, ledger
    )
    malo = revisar_cifras(
        "Son 900,00 € al año y 449,22 € al semestre.", [informe], ledger=ledger, textos_usuario=[USUARIO_SUSC]
    )
    assert sorted(v.literal for v in malo.violaciones) == ["449,22 €", "900,00 € al año"]


def test_the_gate_accepts_the_loan_figures_and_rejects_invented_ones() -> None:
    ledger = EvidenceLedger()
    r = ejecutar_prestamo(HIPOTECA_ARGS)
    informe = _informe(r, "hipoteca", PRESTAMO, ledger)
    v = valores(r)
    usuario = "hipoteca 180.000 € al 3,2 % a 25 años, ¿amortizo 20.000 € en cuota o plazo?"
    plazo = f"{v['reducir plazo: intereses ahorrados']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    bien = (
        f"Con 180.000 € al 3,2 % a 25 años la cuota es 872,42 €/mes. Si amortizas 20.000 € reduciendo plazo "
        f"ahorras {plazo} € de intereses y terminas 47 meses antes; reduciendo cuota pagas 775,49 €/mes [F1]."
    )
    assert revisar_cifras(bien, [informe], ledger=ledger, textos_usuario=[usuario]).violaciones == []
    malo = revisar_cifras("La cuota sería de unos 880,00 €/mes.", [informe], ledger=ledger, textos_usuario=[usuario])
    assert [x.literal for x in malo.violaciones] == ["880,00 €/mes"]


def test_the_brains_own_use_registers_a_fragment_the_gate_reads() -> None:
    ledger = EvidenceLedger()
    (spec, ejecutar), _ = herramientas_para_cerebro(ledger)
    assert spec.name == IMPORTES
    salida = ejecutar({"operacion": "sumar", "importes": SUSCRIPCIONES_MES})
    assert not salida.is_error and salida.content.startswith("[F1] ")
    fragmento = ledger.get("F1")
    assert fragmento is not None and fragmento.authority == "CALCULADORA"
    cifras = cifras_de_herramienta(IMPORTES, salida.content, ledger)
    assert D("898.44") in {c.valor for c in cifras}
    error = ejecutar({"operacion": "sumar", "importes": []})
    assert error.is_error and ledger.get("F2") is None


# --- through the real module specialists -------------------------------------------------------------------------


def test_the_subscriptions_specialist_uses_it_without_a_statement() -> None:
    args = {"operacion": "sumar", "importes": SUSCRIPCIONES_MES}
    proveedor = ProveedorGuion(
        {
            "especialista:suscripciones": [
                [herramientas(("suscripciones_calculo_importes", args)), texto("Gastas 898,44 € al año [F1].")]
            ]
        }
    )
    contexto = turno(proveedor)
    encargo = Encargo(id="e1", especialista="suscripciones", pregunta=USUARIO_SUSC)
    (informe,) = Despachador({"suscripciones": EspecialistaDeModulo(SUSCRIPCIONES)}).ejecutar([encargo], contexto)
    assert informe.estado == "ok"
    total = next(c for c in informe.cifras if c.valor == D("898.44"))
    assert (
        total.herramienta == "suscripciones_calculo_importes"
        and total.unidad == "EUR/año"
        and total.fragment_id == "F1"
    )
    resultado = revisar_cifras(
        "Gastas 898,44 € al año [F1].", [informe], ledger=contexto.ledger, textos_usuario=[USUARIO_SUSC]
    )
    assert resultado.violaciones == []


def test_the_mortgage_specialist_computes_without_a_stored_loan() -> None:
    proveedor = ProveedorGuion(
        {
            "especialista:hipoteca": [
                [herramientas(("hipoteca_calculo_prestamo", HIPOTECA_ARGS)), texto("Cuota 872,42 €/mes [F1].")]
            ]
        }
    )
    contexto = turno(proveedor)
    encargo = Encargo(id="e1", especialista="hipoteca", pregunta="¿Amortizo 20.000 € en cuota o plazo?")
    (informe,) = Despachador({"hipoteca": EspecialistaDeModulo(HIPOTECA)}).ejecutar([encargo], contexto)
    assert informe.estado == "ok"
    assert any(c.valor == D("775.49") and c.herramienta == "hipoteca_calculo_prestamo" for c in informe.cifras)
    assert "Tipo fijo" in (contexto.ledger.get("F1").text if contexto.ledger.get("F1") else "")

"""Reproduction of the battery case «cer-potencia-luz»: what the Luz specialist's report carries as figures.

Without declared figures the runtime read them from the bill text: one simulated year gave 122 figures with
labels like «×» or «-10]: 111», the report kept only the first 60 and the TOTAL (and every figure of a second
bill) was dropped. With the tools declaring their figures the report stays small and every figure is usable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from asistente.ambitos.luz import MODULO
from asistente.nucleo.cerebro.colaboracion.modulos import cifra_de_herramienta
from asistente.nucleo.cerebro.contrato import MAX_CIFRAS_POR_INFORME
from asistente.nucleo.modulos.contrato import ContextoModulo

HOY = date(2026, 11, 24)
OFERTA = {"nombre": "Fija", "energia_eur_kwh": "0.13", "potencia_eur_kw_dia": {"p1": "0.0821", "p2": "0.0036"}}


def _tool(nombre: str):  # type: ignore[no-untyped-def]
    return next(h for h in MODULO.herramientas if h.spec.name == nombre)


def test_the_power_question_fits_in_one_report_with_its_totals() -> None:
    ctx = ContextoModulo(perfil={}, hoy=HOY)
    resultados = [
        _tool("luz_calcular_factura").ejecutar(
            {
                "fecha_inicio": "2025-11-01",
                "fecha_fin": "2026-10-31",
                "potencia_p1_kw": kw,
                "consumo_kwh": {"p1": 800, "p2": 800, "p3": 1600},
                "oferta": OFERTA,
            },
            ctx,
        )
        for kw in (4.6, 3.45)
    ]
    resultados.append(_tool("luz_potencia_optima").ejecutar({"potencia_p1_kw": 4.6, "potencia_objetivo_kw": 3.45}, ctx))
    assert not any(r.es_error for r in resultados)
    assert all(r.cifras for r in resultados)  # declared: the runtime does not parse the text

    cifras = []
    for n, r in enumerate(resultados, start=1):
        for declarada in r.cifras:
            cifra = cifra_de_herramienta(
                declarada, especialista="luz", fragment_id=f"F{n}", herramienta="luz", numero=len(cifras) + 1
            )
            assert cifra is not None, declarada
            cifras.append(cifra)
    assert len(cifras) <= MAX_CIFRAS_POR_INFORME
    totales = [c for c in cifras if "TOTAL" in c.concepto]
    assert len(totales) == 2 and totales[0].valor > totales[1].valor
    assert any(c.concepto.startswith("ahorro anual") and c.valor > Decimal(0) for c in cifras)
    assert not any("×" in c.concepto for c in cifras)

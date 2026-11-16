"""The central agent's ``planificar_hogar`` tool: the model states the problem, the planner computes, and the
plan is one citable fragment."""

from __future__ import annotations

from datetime import date
import json

import pytest

from hacienda_gpt.agent.evidence import EvidenceLedger
from hacienda_gpt.agent.providers.base import ToolCall, ToolSpec
from hacienda_gpt.agent.tools import Toolbox, ToolOutcome
from hacienda_gpt.decision.fact_extractor import EXPECTED_FACT_TYPE
from hacienda_gpt.decision.schemas import CaseState, Fact, FactValueType
from hacienda_gpt.planificador import agente
from hacienda_gpt.planificador.agente import PLANIFICAR_TOOL
from .test_planificador import FAMILIA, HIPOTECA_FIJA

HOY = date(2026, 11, 16)


class _NoSearch:
    def search(self, *args, **kwargs):  # pragma: no cover - never called
        return []


def _case() -> CaseState:
    facts = []
    for nombre, valor in FAMILIA.items():
        if nombre == "hipoteca_prestamo":
            facts.append(
                Fact(
                    fact_id="fact_hipoteca_prestamo",
                    name=nombre,
                    value=json.dumps(HIPOTECA_FIJA, sort_keys=True),
                    value_type=FactValueType.STRING,
                    source="t",
                    confidence=1.0,
                )
            )
        else:
            facts.append(
                Fact(
                    fact_id=f"fact_{nombre}",
                    name=nombre,
                    value=valor,
                    value_type=EXPECTED_FACT_TYPE[nombre],
                    source="t",
                    confidence=1.0,
                )
            )
    return CaseState(case_id="c-plan", user_id="u", jurisdiction="ES", tax_period="2026", facts=facts)


# A household module's specialist, as the core adds it when the user has modules on.
ESPECIALISTA = (
    ToolSpec(name="consultar_hipoteca", description="x", input_schema={"type": "object"}),
    lambda arguments: ToolOutcome(content="x", summary="x"),
)


@pytest.fixture(autouse=True)
def hoy_fijo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agente, "hoy_en_espana", lambda: HOY)


def test_the_tool_is_offered_and_registers_the_plan_as_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agente, "_modulos_activos", lambda case: {"fiscal", "hipoteca"})
    ledger = EvidenceLedger()
    toolbox = Toolbox(searcher=_NoSearch(), ledger=ledger, case=_case(), extra=[ESPECIALISTA])
    assert PLANIFICAR_TOOL.name in {spec.name for spec in toolbox.specs()}
    # Without household modules (or without a case) the advisor offers exactly the tools it always did.
    assert PLANIFICAR_TOOL.name not in {
        spec.name for spec in Toolbox(searcher=_NoSearch(), ledger=ledger, case=_case()).specs()
    }
    assert PLANIFICAR_TOOL.name not in {
        spec.name for spec in Toolbox(searcher=_NoSearch(), ledger=ledger, extra=[ESPECIALISTA]).specs()
    }
    resultado = toolbox.execute(
        ToolCall(
            id="c1",
            name="planificar_hogar",
            arguments={
                "importe_disponible": 20000,
                "horizonte_anos": 5,
                "rentabilidad_alternativa_pct": 2,
                "gastos_mensuales": 2000,
                "liquidez_actual": 6000,
            },
        )
    )
    assert not resultado.is_error, resultado.content
    assert resultado.content.startswith("[F1] Plan del hogar 2026")
    assert "Amortizar hipoteca" in resultado.content and "Tu plan de pensiones" in resultado.content
    assert "vista diferimiento" in resultado.content and "No modelado:" in resultado.content
    assert len(ledger) == 1
    assert resultado.summary.startswith("posición neta")


def test_without_the_mortgage_module_the_loan_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agente, "_modulos_activos", lambda case: {"fiscal"})
    toolbox = Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger(), case=_case())
    resultado = toolbox.execute(ToolCall(id="c1", name="planificar_hogar", arguments={"importe_disponible": 5000}))
    assert not resultado.is_error
    assert "Módulo no activado" not in resultado.content  # the text says the cause by its id
    assert "modulo_inactivo" in resultado.content


def test_rejected_levers_and_bad_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agente, "_modulos_activos", lambda case: {"fiscal", "hipoteca"})
    toolbox = Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger(), case=_case())
    resultado = toolbox.execute(
        ToolCall(
            id="c1",
            name="planificar_hogar",
            arguments={"importe_disponible": 5000, "excluidas": ["amortizar_hipoteca"]},
        )
    )
    assert not resultado.is_error
    assert "Amortizar hipoteca (restricciones): La has descartado (usuario)." in resultado.content
    malo = toolbox.execute(ToolCall(id="c2", name="planificar_hogar", arguments={"horizonte_anos": 0}))
    assert malo.is_error and "Datos del plan no válidos" in malo.content

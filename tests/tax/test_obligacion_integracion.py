"""The obligation to file is wired through: the extractor knows its facts, the advisor is told to use its tool,
and «I am self-employed» is not taken as «I was registered as self-employed during that year»."""

from __future__ import annotations

from pathlib import Path

from hacienda_gpt.agent.prompts import AGENT_SYSTEM_PROMPT
from hacienda_gpt.decision.fact_extractor import _SYSTEM_PROMPT, EXPECTED_FACT_TYPE, EXTRACTOR_FACT_NAMES
from hacienda_gpt.tax.profile import build_input

HECHOS_OBLIGACION = (
    "num_pagadores_trabajo",
    "importe_segundo_y_restantes_pagadores",
    "pension_compensatoria_o_anualidades",
    "pagador_no_obligado_a_retener",
    "trabajo_tipo_fijo_retencion",
    "pensiones_procedimiento_especial",
    "ganancias_sometidas_a_retencion",
    "alta_autonomo_en_el_ejercicio",
    "deduccion_doble_imposicion_internacional",
    "aportaciones_patrimonio_protegido",
    "beneficiario_ingreso_minimo_vital",
)
PRESENTACION = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "presentation.ts"


def test_the_extractor_is_told_what_each_obligation_fact_means_in_its_slot() -> None:
    for name in HECHOS_OBLIGACION:
        assert name in EXTRACTOR_FACT_NAMES
        linea = next(line for line in _SYSTEM_PROMPT.splitlines() if line.startswith(f"    {name} "))
        assert "→" in linea, name
        # Numbers under value_number, the rest under value_boolean: the slot rule of the prompt.
        bloque = _SYSTEM_PROMPT[: _SYSTEM_PROMPT.index(linea)]
        slot = "value_number" if EXPECTED_FACT_TYPE[name].value == "number" else "value_boolean"
        assert bloque.rfind(slot) > max(
            bloque.rfind(o) for o in {"value_number", "value_boolean", "value_string"} - {slot}
        )


def test_every_extractor_fact_is_described_in_the_prompt() -> None:
    # Only the Madrid-specific donations fact has no line of its own (the generic regional one has).
    sin_linea = {name for name in EXTRACTOR_FACT_NAMES if f"    {name} " not in _SYSTEM_PROMPT}
    assert sin_linea <= {"donativos_fundaciones_madrid"}


def test_the_advisor_is_told_to_answer_the_obligation_with_its_tool() -> None:
    assert "«¿tengo que declarar?»" in AGENT_SYSTEM_PROMPT and "`obligacion_declarar`" in AGENT_SYSTEM_PROMPT


def test_the_web_app_labels_every_obligation_fact() -> None:
    texto = PRESENTACION.read_text(encoding="utf-8")
    assert "...Object.fromEntries(OBLIGACION_FIELDS.map" in texto
    for name in ("deduccion_doble_imposicion_internacional", "aportaciones_patrimonio_protegido"):
        assert f"  {name}: " in texto
    assert "  beneficiario_ingreso_minimo_vital: " in texto


def test_being_self_employed_now_does_not_say_when_one_was_registered() -> None:
    # «es_autonomo» is the user's status today; the rule needs registration in RETA or the sea workers' scheme
    # at some time OF THE YEAR being declared (art. 96.2 LIRPF, last paragraph). Neither follows from the other
    # (a past year, or a professional in a mutual instead of RETA): the advisor asks (agent/tools.py says so).
    assert build_input(ejercicio=2025, facts={"es_autonomo": True}).alta_autonomo_en_el_ejercicio is None

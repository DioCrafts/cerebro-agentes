"""The obligation to file: its quotes are in the law, its figures are the law's, and it is wired everywhere.

``tests/fixtures/boe_obligacion`` holds literal extracts of the consolidated texts on boe.es (art. 96 and DT 18.ª
LIRPF, arts. 60, 60 bis, 61, 76 and 80 RIRPF, art. 36 of the ingreso mínimo vital law).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any

import pytest

from hacienda_gpt.agent.evidence import EvidenceLedger
from hacienda_gpt.agent.providers.base import ToolCall
from hacienda_gpt.agent.tools import CALCULATE_TOOL, OBLIGACION_TOOL, Toolbox
from hacienda_gpt.decision.fact_extractor import EXPECTED_FACT_TYPE
from hacienda_gpt.decision.schemas import CaseState, Fact
from hacienda_gpt.modulos.fiscal import MODULO
from hacienda_gpt.tax.irpf import IRPFInput
from hacienda_gpt.tax.obligacion import _PREGUNTAS, CITAS, obligado_a_declarar
from hacienda_gpt.tax.params import DATA_DIR, load_params
from hacienda_gpt.tax.profile import PROFILE_FACT_TYPES, build_input
from hacienda_gpt.tax.recodificacion.adaptadores import CIFRAS
from hacienda_gpt.tax.recodificacion.nucleo import Objetivo
from hacienda_gpt.tax.simulador import simular
from hacienda_gpt.tax.verificacion import comprobaciones, huella_de_grupo, verificar

D = Decimal
EXTRACTOS = Path(__file__).resolve().parents[1] / "fixtures" / "boe_obligacion"
LIRPF = "BOE-A-2006-20764"
NUEVOS_CAMPOS = {
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
}


def _texto(boe_id: str) -> str:
    lineas = (EXTRACTOS / f"{boe_id}.txt").read_text(encoding="utf-8").splitlines()
    return " ".join(" ".join(linea for linea in lineas if not linea.startswith("#")).split())


def _miles(x: Decimal) -> str:
    return f"{x:,.0f}".replace(",", ".")


def _evidencia() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DATA_DIR / "evidencia_parametros.json").read_text(encoding="utf-8"))
    return data


# --------------------------------------------------------------------------- #
# The law
# --------------------------------------------------------------------------- #


def test_every_passage_the_answer_quotes_is_literally_in_the_law() -> None:
    for clave, cita in CITAS.items():
        texto = _texto(cita.boe_id)
        for parte in cita.texto.split("[…]"):
            assert " ".join(parte.split()) in texto, (clave, parte[:80])


def test_the_figures_of_both_years_are_the_ones_the_law_prints_and_the_quotes_shown_say_them() -> None:
    texto = _texto(LIRPF)
    for ejercicio in (2025, 2026):
        o = load_params(ejercicio).obligacion_declarar
        assert o.fuente.verificado
        for frase, cifra in (
            ("con el límite de {} euros anuales", o.limite_trabajo_un_pagador),
            ("será de {} euros para los contribuyentes", o.limite_trabajo_varios_pagadores),
            ("no supera en su conjunto la cantidad de {} euros", o.limite_segundo_pagador),
            (
                "sometidos a retención o ingreso a cuenta, con el límite conjunto de {} euros",
                o.limite_capital_y_ganancias_con_retencion,
            ),
            ("ayudas públicas, con el límite conjunto de {} euros", o.limite_imputadas_letras_subvenciones),
            ("así como ganancias patrimoniales, con el límite conjunto de {} euros", o.limite_conjunto_rentas_minimas),
            ("pérdidas patrimoniales de cuantía inferior a {} euros", o.limite_perdidas_patrimoniales),
        ):
            assert frase.format(_miles(cifra)) in texto, frase
        # The quotes the user is shown print the figures the code applied.
        assert f"{_miles(o.limite_trabajo_un_pagador)} euros" in CITAS["96.2.a"].texto
        assert f"{_miles(o.limite_trabajo_varios_pagadores)} euros" in CITAS["96.3.b"].texto
        assert f"{_miles(o.limite_segundo_pagador)} euros" in CITAS["96.3.a.1"].texto
        assert f"{_miles(o.limite_capital_y_ganancias_con_retencion)} euros" in CITAS["96.2.b"].texto
        assert f"{_miles(o.limite_perdidas_patrimoniales)} euros" in CITAS["96.2.minimas"].texto


def test_the_regulation_still_prints_15000_and_the_law_wins() -> None:
    # Art. 61.3 RIRPF was not updated after the Act raised the limit: the note on the data says so.
    assert "2.º 15.000 euros anuales, cuando:" in _texto("BOE-A-2007-6820")
    assert "15.876 euros" in _texto(LIRPF)
    nota = load_params(2026).obligacion_declarar.fuente.nota or ""
    assert "15.000" in nota and "rige la cifra de la Ley" in nota and "1.500" in nota


def test_the_advance_payments_of_art_81_bis_do_not_oblige_to_file() -> None:
    # The regulation lets those not obliged regularise them without a return.
    texto = _texto("BOE-A-2007-6820")
    assert CITAS["60bis.3"].texto in texto and CITAS["60.5"].texto in texto


# --------------------------------------------------------------------------- #
# The data: quotes, params-verify and the re-encoding gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ejercicio", [2025, 2026])
def test_params_verify_checks_the_group_by_its_quotes_and_they_are_in_the_law(ejercicio: int) -> None:
    params = load_params(ejercicio)
    entrada = _evidencia()[f"irpf_{ejercicio}.json"]["obligacion_declarar"]
    assert entrada["sha256_valor"] == huella_de_grupo(params.obligacion_declarar)
    (check,) = [c for c in comprobaciones(params) if c.ruta == ["obligacion_declarar"]]
    assert check.tipo == "cita" and check.boe_id == LIRPF and check.version is None
    pagina = (EXTRACTOS / f"{LIRPF}.txt").read_text(encoding="utf-8")
    (resultado,) = verificar([check], lambda boe_id, version: pagina if boe_id == LIRPF else None)
    assert resultado.ok, resultado.faltan


@pytest.mark.parametrize("ejercicio", [2025, 2026])
def test_every_figure_of_the_group_is_in_its_quotes(ejercicio: int) -> None:
    raw = json.loads((DATA_DIR / f"irpf_{ejercicio}.json").read_text(encoding="utf-8"))
    adaptador = CIFRAS["obligacion_declarar"]
    objetivo = Objetivo(
        tipo=adaptador.tipo,
        fichero=adaptador.fichero,
        ruta=["obligacion_declarar"],
        etiqueta="obligacion_declarar",
        ambito="estado",
        boe_id=LIRPF,
        articulos_ley=["96"],
        ejercicios=[ejercicio],
        antes={ejercicio: raw["obligacion_declarar"]},
    )
    valor = {k: v for k, v in raw["obligacion_declarar"].items() if k != "fuente"}
    citas = _evidencia()[f"irpf_{ejercicio}.json"]["obligacion_declarar"]["citas"]
    modelo, control = adaptador.controlar(valor, citas, objetivo, efectos_desde=None, datos={ejercicio: raw})
    assert modelo is not None and control.ok, control.fallos
    # A figure no quote prints is caught.
    _, mal = adaptador.controlar(
        {**valor, "limite_segundo_pagador": "2500"}, citas, objetivo, efectos_desde=None, datos={ejercicio: raw}
    )
    assert not mal.ok


def test_the_re_encoding_runs_reviewer_cases_through_the_obligation() -> None:
    adaptador = CIFRAS["obligacion_declarar"]
    objetivo = Objetivo.model_construct(ejercicios=[2026])
    actual = load_params(2026).obligacion_declarar
    caso = {
        "ejercicio": 2026,
        "contribuyente": {
            "rendimiento_trabajo_bruto": "20000",
            "num_pagadores_trabajo": 2,
            "importe_segundo_y_restantes_pagadores": "2000",
            "pensiones_procedimiento_especial": False,
            "pension_compensatoria_o_anualidades": False,
            "pagador_no_obligado_a_retener": False,
            "trabajo_tipo_fijo_retencion": False,
        },
    }
    assert adaptador.evaluar_caso(actual, caso, objetivo)[0] == 1  # 2.000 > 1.500: limit 15.876
    # A proposal that read the repealed 2.500 euros would decide the same case the other way: the case catches it.
    derogada = actual.model_copy(update={"limite_segundo_pagador": D(2500)})
    importe, nota = adaptador.evaluar_caso(derogada, caso, objetivo)
    assert importe == 0 and nota is not None and nota.startswith("No estás obligado")
    with pytest.raises(ValueError, match="contribuyente"):
        adaptador.evaluar_caso(actual, {"ejercicio": 2026}, objetivo)
    with pytest.raises(ValueError, match="caso no válido"):
        adaptador.evaluar_caso(actual, {"contribuyente": {"campo_inventado": 1}}, objetivo)


# --------------------------------------------------------------------------- #
# Wiring: profile facts, simulator, agent tool, tax module
# --------------------------------------------------------------------------- #


def test_the_new_inputs_are_profile_facts_known_to_the_extractor_and_the_tool_descriptions() -> None:
    assert NUEVOS_CAMPOS <= set(IRPFInput.model_fields)
    assert NUEVOS_CAMPOS <= set(PROFILE_FACT_TYPES) and NUEVOS_CAMPOS <= set(EXPECTED_FACT_TYPE)
    assert set(_PREGUNTAS) <= NUEVOS_CAMPOS
    for campo in NUEVOS_CAMPOS:
        assert campo in CALCULATE_TOOL.description and campo in OBLIGACION_TOOL.description
    # Facts arrive as floats from the fact store; the counts become integers.
    inp = build_input(
        ejercicio=2026,
        facts={
            "num_pagadores_trabajo": 2.0,
            "importe_segundo_y_restantes_pagadores": 1800.0,
            "rendimiento_trabajo_bruto": 17000.0,
        },
        datos={"pensiones_procedimiento_especial": False},
    )
    assert inp.num_pagadores_trabajo == 2 and inp.importe_segundo_y_restantes_pagadores == D(1800)
    assert obligado_a_declarar(inp).obligado == "si"


def test_the_simulation_carries_the_answer_of_each_scenario() -> None:
    simulacion = simular(
        ejercicio=2026,
        facts={"rendimiento_trabajo_bruto": 30000.0},
        escenarios=[
            {"nombre": "Base", "cambios": {}},
            {"nombre": "Menos sueldo", "cambios": {"rendimiento_trabajo_bruto": 12000, "retenciones": 300}},
        ],
        con_beneficios=False,
    )
    base, otro = (e.resultado.obligacion_declarar for e in simulacion.escenarios)
    assert base is not None and base.obligado == "si"
    assert otro is not None and otro.obligado == "no" and otro.devolucion_estimada == D(300)
    # The JSON the simulator screen reads.
    dump = simulacion.escenarios[1].resultado.model_dump(mode="json")["obligacion_declarar"]
    assert dump["obligado"] == "no" and dump["regla"]["cita"].startswith("2. No obstante")


def _caso(**facts: Any) -> CaseState:
    ahora = datetime.now(UTC)
    return CaseState(
        case_id="c-oblig",
        user_id="u",
        jurisdiction="ES",
        tax_period="2025",
        created_at=ahora,
        updated_at=ahora,
        facts=[
            Fact(fact_id=f"fact_{k}", name=k, value=v, value_type=EXPECTED_FACT_TYPE[k], source="t", confidence=1.0)
            for k, v in facts.items()
        ],
    )


def test_the_agent_tool_answers_with_the_rule_its_quote_and_what_to_ask() -> None:
    ledger = EvidenceLedger()
    toolbox = Toolbox(searcher=None, ledger=ledger, case=_caso(rendimiento_trabajo_bruto=18000.0, es_autonomo=True))  # type: ignore[arg-type]
    assert "obligacion_declarar" in {spec.name for spec in toolbox.specs()}
    out = toolbox.execute(ToolCall(id="1", name="obligacion_declarar", arguments={}))
    assert not out.is_error and out.content.startswith("[F1] Obligación de declarar el IRPF 2025: NO SE PUEDE SABER")
    assert "Datos que faltan" in out.content and "[dato: num_pagadores_trabajo]" in out.content
    assert "trabaja por cuenta propia" in out.content
    assert "obligado a declarar: no se puede saber" in out.summary
    # With the missing facts given, a firm answer and the law's sentence.
    datos = {
        "num_pagadores_trabajo": 1,
        "pension_compensatoria_o_anualidades": False,
        "pagador_no_obligado_a_retener": False,
        "trabajo_tipo_fijo_retencion": False,
        "alta_autonomo_en_el_ejercicio": False,
    }
    firme = toolbox.execute(ToolCall(id="2", name="obligacion_declarar", arguments={"datos": datos}))
    assert ": NO\n" in firme.content and "«2. No obstante, no tendrán que declarar" in firme.content
    assert re.search(r"\[F2\]", firme.content)
    malo = toolbox.execute(ToolCall(id="3", name="obligacion_declarar", arguments={"datos": {"inventado": 1}}))
    assert malo.is_error and "Campos desconocidos" in malo.content
    # calcular_irpf says it too, in one line.
    calculo = toolbox.execute(ToolCall(id="4", name="calcular_irpf", arguments={"datos": datos}))
    assert "¿Obligado a declarar? no [art. 96.2.a) LIRPF]" in calculo.content


def test_the_tax_module_exposes_the_tool() -> None:
    assert "obligacion_declarar" in {h.spec.name for h in MODULO.herramientas}

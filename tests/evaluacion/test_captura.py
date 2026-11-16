"""Feedback capture: data minimisation, pseudonymisation, automatic signals, and the operator's draft."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType
from asistente.nucleo.evaluacion.captura import (
    CONSENTIMIENTO_VERSION,
    FeedbackEntrada,
    FeedbackRegistrado,
    borrador_de_caso,
    comprobar_anonimizado,
    hechos_usados,
    identificadores_en,
    instantanea_de,
    referencia_anonima,
    seudonimizar,
)
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, ExpCifra
from asistente.nucleo.evaluacion.senales import agregar, es_repregunta, senales_de_auditoria
from asistente.nucleo.evaluacion.suite import cifras_esperadas

PRESTAMO = {
    "capital_inicial": "100000",
    "fecha_firma": "2020-01-15",
    "plazo_meses": 300,
    "tipo": "fijo",
    "tipo_inicial": "3",
}


def _fact(nombre: str, valor, tipo: FactValueType = FactValueType.NUMBER) -> Fact:  # type: ignore[no-untyped-def]
    return Fact(fact_id=f"fact_{nombre}", name=nombre, value=valor, value_type=tipo, source="usuario", confidence=1.0)


def _case() -> CaseState:
    return CaseState(
        case_id="case_1",
        user_id="u1",
        jurisdiction="ES",
        tax_period="2025",
        facts=[
            _fact("comunidad_autonoma", "Madrid", FactValueType.STRING),
            _fact("rendimiento_trabajo_bruto", 30000.0),
            _fact("hipoteca_prestamo", json.dumps(PRESTAMO), FactValueType.STRING),
            _fact("luz_cups", "ES0031405912345678JY0F", FactValueType.STRING),
            _fact("luz_potencia_p1_kw", 4.6),
        ],
    )


def test_only_the_facts_the_answer_used_are_kept() -> None:
    case = _case()
    assert hechos_usados(case, []) == []
    # The tax calculator reads the tax profile, never a module's facts.
    assert hechos_usados(case, [{"tool": "calcular_irpf", "arguments": {}}]) == [
        "comunidad_autonoma",
        "rendimiento_trabajo_bruto",
    ]
    # Consulting the mortgage specialist: its facts and the shared ones, not the electricity ones.
    usados = hechos_usados(
        case, [{"tool": "consultar_hipoteca", "arguments": {"pregunta": "cuota"}, "agent": "asesor"}]
    )
    assert "hipoteca_prestamo" in usados and "luz_cups" not in usados and "luz_potencia_p1_kw" not in usados
    # A failed call used nothing; a fact named in the arguments was used.
    assert hechos_usados(case, [{"tool": "calcular_irpf", "is_error": True}]) == []
    assert hechos_usados(case, [{"tool": "luz_x", "arguments": {"datos": {"luz_potencia_p1_kw": 5}}}]) == [
        "luz_potencia_p1_kw"
    ]


def test_the_snapshot_is_pseudonymised_and_flags_sensitive_facts() -> None:
    luz = instantanea_de(_case(), [{"tool": "consultar_luz", "agent": "asesor"}])
    assert luz["hechos"]["luz_cups"] == "[cups]" and luz["hechos"]["luz_potencia_p1_kw"] == 4.6
    # Another module's data never travels with the case; the shared (tax) facts are what a specialist may read.
    assert "hipoteca_prestamo" not in luz["hechos"] and luz["hechos"]["comunidad_autonoma"] == "Madrid"
    assert luz["sensibles"] == ["luz_cups"] and luz["tax_period"] == "2025"
    hipoteca = instantanea_de(_case(), [{"tool": "consultar_hipoteca", "agent": "asesor"}])
    assert hipoteca["hechos"]["hipoteca_prestamo"] == PRESTAMO  # parsed, numbers kept to reproduce figures
    assert "luz_cups" not in hipoteca["hechos"] and hipoteca["sensibles"] == ["hipoteca_prestamo"]


@pytest.mark.parametrize(
    ("texto", "marca"),
    [
        ("escríbeme a ana.perez@example.com", "[correo]"),
        ("mi IBAN es ES91 2100 0418 4502 0005 1332", "[iban]"),
        ("DNI 12345678Z", "[documento]"),
        ("NIE X1234567L", "[documento]"),
        ("llámame al 612 345 678", "[teléfono]"),
        ("CUPS ES0031405912345678JY0F", "[cups]"),
    ],
)
def test_identifiers_are_replaced(texto: str, marca: str) -> None:
    assert marca in seudonimizar(texto)
    assert identificadores_en(texto) and not identificadores_en(seudonimizar(texto))


def test_amounts_and_dates_are_not_mistaken_for_identifiers() -> None:
    texto = "Gano 30.000 € y pago 474,21 € al mes desde el 15/01/2020; plazo de 300 meses."
    assert seudonimizar(texto) == texto


def test_storing_a_case_needs_the_current_consent() -> None:
    base = {"pregunta": "¿?", "respuesta": "Sí.", "valoracion": "negativa", "case_id": "c"}
    with pytest.raises(ValueError, match="consentimiento"):
        FeedbackEntrada.model_validate({**base, "guardar_para_evaluacion": True})
    with pytest.raises(ValueError, match="consentimiento"):
        FeedbackEntrada.model_validate({**base, "guardar_para_evaluacion": True, "version_consentimiento": "2020"})
    FeedbackEntrada.model_validate(
        {**base, "guardar_para_evaluacion": True, "version_consentimiento": CONSENTIMIENTO_VERSION}
    )
    with pytest.raises(ValueError, match="positiva"):
        FeedbackEntrada.model_validate({**base, "valoracion": "positiva", "tipo": "otro"})


def _evento(entrada: str, minutos: int = 0, **extra) -> dict:  # type: ignore[no-untyped-def]
    momento = datetime(2026, 11, 16, 10, 0, tzinfo=UTC) + timedelta(minutes=minutos)
    return {"event_type": "agent_answer", "input": entrada, "event_time": momento.isoformat(), **extra}


def test_automatic_signals_from_the_audit_trail() -> None:
    eventos = [
        _evento(
            "¿Cuánto me sale la factura de la luz este mes?",
            claims=[{"text": "x", "verdict": "no_respaldada"}, {"text": "y", "verdict": "respaldada"}],
            tool_trace=[
                {"tool": "consultar_hipoteca", "agent": "asesor", "summary": "especialista hipoteca: 0 fragmento(s)"},
                {"tool": "leer_expediente", "agent": "especialista_hipoteca", "summary": "3 datos"},
                {"tool": "calcular_irpf", "agent": "asesor", "summary": "error", "is_error": True},
            ],
        ),
        {"event_type": "fact_updated"},
        _evento("¿Cuánto me sale la factura de la luz de este mes?", minutos=2),
        _evento("No, te he dicho la de octubre", minutos=50),
    ]
    senales = senales_de_auditoria(eventos)
    tipos = sorted((x.tipo, x.turno) for x in senales)
    assert tipos == [
        ("afirmacion_rechazada", 0),
        ("error_herramienta", 0),
        ("especialista_sin_resultado", 0),
        ("repregunta", 0),
    ]
    assert agregar(senales)["por_modulo"] == {"hipoteca": 1}
    # A correction is a repeat even with other words, but not after half an hour.
    assert es_repregunta(_evento("a b c"), _evento("No, me refiero a otra cosa", minutos=5))
    assert not es_repregunta(eventos[2], eventos[3])
    # No signal ever carries user text.
    assert "luz" not in json.dumps([x.model_dump() for x in senales])


def _registro() -> FeedbackRegistrado:
    return FeedbackRegistrado(
        id="fb_1",
        case_id="case_1",
        creado_el=datetime.now(UTC),
        valoracion="negativa",
        tipo="cifra_erronea",
        guardado_para_evaluacion=True,
        estado="pendiente",
        etiquetas=["cifra_erronea"],
    )


def test_the_draft_reads_the_users_figure_from_a_calculator_line_never_from_the_user() -> None:
    contenido = {
        "pregunta": "¿Cuál es mi cuota? Escríbeme a ana@example.com",
        "respuesta": "Tu cuota es de 500 €.",
        "correccion": {"tipo": "cifra_erronea", "comentario": "La cuota es otra", "cifra_correcta": "474,21 €"},
        "tool_trace": [
            {"tool": "consultar_hipoteca", "agent": "asesor", "arguments": {"pregunta": "cuota"}},
            {"tool": "hipoteca_cuadro", "agent": "especialista_hipoteca", "arguments": {"fecha": "2026-03-20"}},
        ],
        "instantanea": {
            "tax_period": "2026",
            "hechos": {"hipoteca_prestamo": PRESTAMO},
            "sensibles": ["hipoteca_prestamo"],
        },
    }
    caso, notas = borrador_de_caso(_registro(), contenido)
    assert caso.categoria == "regresion" and caso.modulos == ["hipoteca"]
    assert "[correo]" in caso.pregunta and "example.com" not in caso.pregunta
    cifra = next(e for e in caso.expectativas if isinstance(e, ExpCifra))
    assert cifra.calculo.herramienta == "hipoteca_cuadro"
    assert cifra_esperadas_de(caso) == {"cifra_corregida": "474,21 €"}
    assert caso.origen.referencia == referencia_anonima("fb_1") and "fb_1" not in caso.model_dump_json()
    assert any("sensibles" in n for n in notas)

    # A figure no calculator produces is never turned into an expectation.
    contenido["correccion"]["cifra_correcta"] = "999,99 €"  # type: ignore[index]
    caso2, notas2 = borrador_de_caso(_registro(), contenido)
    assert not any(isinstance(e, ExpCifra) for e in caso2.expectativas)
    assert any("no la da ninguna calculadora" in n for n in notas2)


def cifra_esperadas_de(caso: CasoEvaluacion) -> dict[str, str]:
    esperadas = cifras_esperadas(caso)
    assert not esperadas.errores, esperadas.errores
    return esperadas.cifras


def test_wrong_module_feedback_proposes_the_opposite_expectation() -> None:
    contenido = {
        "pregunta": "¿Cuándo caduca mi pasaporte?",
        "correccion": {"tipo": "modulo_erroneo"},
        "tool_trace": [{"tool": "consultar_luz", "agent": "asesor"}],
        "instantanea": {},
    }
    caso, notas = borrador_de_caso(_registro(), contenido)
    assert [e.model_dump() for e in caso.expectativas] == [{"tipo": "modulo_no_consultado", "modulo": "luz"}]
    assert any("ámbito era otro" in n for n in notas)


def test_promotion_check_finds_personal_data_anywhere_in_the_case() -> None:
    caso = CasoEvaluacion.model_validate(
        {
            "id": "regresion-x",
            "categoria": "regresion",
            "pregunta": "Mi DNI es 12345678Z",
            "expediente": {"hechos": {"luz_cups": "ES0031405912345678JY0F"}},
            "expectativas": [{"tipo": "sin_accion_pendiente"}],
        }
    )
    problemas = comprobar_anonimizado(caso)
    assert any(p.startswith("pregunta") for p in problemas) and any(p.startswith("expediente") for p in problemas)

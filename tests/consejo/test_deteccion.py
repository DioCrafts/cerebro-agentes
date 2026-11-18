"""The detector of hard decisions on a table of Spanish phrasings: real choices with something at stake are hard;
factual questions are NEVER hard; small or reversible choices are not; asking for the council always is."""

from __future__ import annotations

import json

import pytest

from asistente.nucleo.cerebro.contrato import Encargo, Intencion
from asistente.nucleo.consejo.deteccion import (
    Deteccion,
    es_decision_dificil,
    es_factual,
    importes_de_texto,
    opciones_de_texto,
    pedido_por_usuario,
)
from .fakes import caso

DIFICILES = [
    "¿Me conviene amortizar la hipoteca o invertir en un fondo indexado?",
    "¿Amortizo o invierto?",
    "¿Acepto la herencia de mi tío o renuncio?",
    "¿Debería aceptar la herencia de mi padre?",
    "Tengo 60.000 € ahorrados, ¿los meto en la hipoteca o en un fondo?",
    "¿Compro piso ahora o espero un año?",
    "¿Qué es mejor, alquilar o comprar?",
    "¿Me compensa recurrir la liquidación de 3.000 € que me ha mandado Hacienda?",
    "¿Me hago autónomo o sigo como asalariado?",
    "¿Vale la pena contratar un plan de pensiones este año?",
    "Dudo entre aceptar la herencia a beneficio de inventario y renunciar",
    "No lo tengo claro: ¿vendo el piso de mi madre o lo alquilo?",
    "¿Debería jubilarme anticipadamente a los 63?",
    "¿Rescato el plan de pensiones o lo dejo?",
    "¿Qué me recomiendas, fondo indexado o depósito?",
    "¿Cancelo la hipoteca con la indemnización del despido o guardo el dinero?",
    "¿Firmo la hipoteca a tipo fijo o a tipo variable?",
    "Mis padres quieren donarme su casa, ¿nos conviene más donación o esperar a la herencia?",
    "¿Sería mejor vender las acciones ahora o esperar a que suban?",
    "Me da miedo endeudarme: ¿compro la vivienda o sigo de alquiler?",
    "¿Merece la pena amortizar 30.000 € de hipoteca reduciendo cuota o plazo?",
    "¿Dejo el trabajo para montar un negocio o espero un año más?",
    "No sé si refinanciar el préstamo o pagarlo de golpe con mis ahorros",
    "¿Debería pedir la excedencia o capitalizar el paro?",
    "¿Qué hacemos con los 150.000 € de la venta del piso: amortizar o invertir?",
    "¿Me conviene más rescatar el plan de pensiones en forma de capital o de renta?",
]

# The user asks for the council itself (always offered, whatever the question).
PEDIDOS = [
    "Consúltalo con el consejo",
    "¿Puedes preguntarle al consejo si me conviene cambiar de banco?",
    "Quiero la opinión del consejo sobre esto",
    "Que lo estudie el consejo, por favor",
    "Convoca al consejo para lo de la herencia",
]

# Factual questions: NEVER hard, even about weighty topics.
FACTUALES = [
    "¿Cuánto me ahorro si amortizo 10.000 € de hipoteca?",
    "¿Qué pasa si renuncio a la herencia?",
    "¿Cuándo se presenta el modelo 303?",
    "¿Qué impuestos pago si vendo mi piso?",
    "¿Cuánto pagaré de IRPF este año?",
    "¿Cuándo vence el plazo para aceptar la herencia?",
    "¿Cómo se calcula la plusvalía municipal si vendo la casa?",
    "¿Es obligatorio declarar si cobro 20.000 €?",
    "¿Qué es la deducción por vivienda habitual?",
    "¿Cuál es el plazo para recurrir una liquidación?",
    "¿Qué documentos necesito para aceptar una herencia?",
    "¿Cuánto cuesta cancelar la hipoteca en el registro?",
    "¿Puedo deducirme el plan de pensiones de mi mujer?",
    "Explícame cómo tributa la venta de acciones",
    "¿Qué diferencia hay entre amortizar plazo y cuota?",
    "¿Qué ocurre si no presento el modelo 720?",
    "¿Cómo me afecta la subida del euríbor?",
]

# Not hard: no real choice, or nothing important at stake.
NO_DIFICILES = [
    "Hola, ¿qué tal?",
    "Gracias por la ayuda",
    "¿Me das un consejo para ahorrar en la luz?",
    "¿Me conviene la tarifa fija o la de discriminación horaria?",
    "¿Debería cambiarme de compañía de luz?",
    "¿Recurro la multa de tráfico de 100 €?",
    "¿Qué es mejor para mí, hacer la declaración conjunta o individual?",
    "No sé si cambiar de tarifa de luz",
    "¿Pago el IBI en uno o dos plazos?",
    "Apúntame que tengo que renovar el DNI",
    "Prepara la reclamación a la compañía de luz",
    "¿Me doy de baja de Netflix o de HBO?",
    "Recuérdame el plazo del 130",
]


@pytest.mark.parametrize("pregunta", DIFICILES)
def test_hard_decisions(pregunta: str) -> None:
    deteccion = es_decision_dificil(pregunta)
    assert deteccion.dificil, deteccion.motivos
    assert not deteccion.factual and not deteccion.pedido_por_usuario
    assert deteccion.motivos and deteccion.puntuacion >= 3.5


@pytest.mark.parametrize("pregunta", PEDIDOS)
def test_asking_for_the_council_is_always_hard(pregunta: str) -> None:
    deteccion = es_decision_dificil(pregunta)
    assert deteccion.dificil and deteccion.pedido_por_usuario and pedido_por_usuario(pregunta)
    assert deteccion.motivos == ["lo has pedido expresamente"]


@pytest.mark.parametrize("pregunta", FACTUALES)
def test_factual_questions_are_never_hard(pregunta: str) -> None:
    deteccion = es_decision_dificil(pregunta)
    assert not deteccion.dificil and deteccion.factual, deteccion.motivos
    assert es_factual(pregunta)


@pytest.mark.parametrize("pregunta", NO_DIFICILES)
def test_small_or_reversible_choices_are_not_hard(pregunta: str) -> None:
    deteccion = es_decision_dificil(pregunta)
    assert not deteccion.dificil, deteccion.motivos


def test_the_table_has_at_least_fifty_phrasings() -> None:
    assert len(DIFICILES) + len(PEDIDOS) + len(FACTUALES) + len(NO_DIFICILES) >= 50


def test_reasons_are_explainable_term_by_term() -> None:
    deteccion = es_decision_dificil("Tengo 60.000 € ahorrados, ¿los meto en la hipoteca o en un fondo?")
    assert deteccion.opciones_detectadas == ["Meterlos en la hipoteca", "En un fondo"]
    assert any(m.startswith("compara alternativas") for m in deteccion.motivos)
    assert "importe grande: 60.000 €" in deteccion.motivos
    pequeno = es_decision_dificil("¿Recurro la multa de tráfico de 100 €?")
    assert "importe pequeño (100 €)" in pequeno.motivos


@pytest.mark.parametrize(
    ("pregunta", "opciones"),
    [
        ("¿Amortizo la hipoteca o invierto el dinero?", ["Amortizar la hipoteca", "Invertir el dinero"]),
        ("¿Me conviene más amortizar o invertir en un fondo?", ["Amortizar", "Invertir en un fondo"]),
        ("¿Qué es mejor, alquilar o comprar?", ["Alquilar", "Comprar"]),
        ("¿Vendo, alquilo o lo dejo como está?", ["Vender", "Alquilar", "Dejarlo como está"]),
        (
            "Dudo entre aceptar la herencia a beneficio de inventario y renunciar",
            ["Aceptar la herencia a beneficio de inventario", "Renunciar"],
        ),
        ("¿Rescato el plan o lo dejo?", ["Rescatar el plan", "Dejarlo"]),
        ("¿Pago el IBI en uno o dos plazos?", ["Pagar el IBI en uno", "Dos plazos"]),
        ("¿Pago 2 o 3 cuotas?", []),
        ("¿Es más o menos lo mismo?", []),
        ("¿Debería vender el piso?", []),
    ],
)
def test_alternatives_written_by_the_user(pregunta: str, opciones: list[str]) -> None:
    assert opciones_de_texto(pregunta) == opciones


def test_amounts_in_the_text() -> None:
    assert importes_de_texto("tengo 60.000 € y 20 mil en el banco, 15k en acciones y 1.500.000 de patrimonio") == [
        60000,
        20000,
        15000,
        1500000,
    ]
    assert importes_de_texto("en 2026 a los 63 años con un 3 % de interés") == []


def test_the_brains_plan_with_several_specialists_is_a_signal() -> None:
    pregunta = "¿Me conviene pedir un préstamo para la reforma?"
    sola = es_decision_dificil(pregunta)
    plan = Intencion(
        tipo="cruzada",
        encargos=[
            Encargo(id="e1", especialista="hipoteca", pregunta="x"),
            Encargo(id="e2", especialista="fiscal", pregunta="y"),
        ],
        confianza=0.9,
        origen="reglas",
    )
    cruzada = es_decision_dificil(pregunta, plan)
    assert cruzada.puntuacion == sola.puntuacion + 1
    assert any(m.startswith("cruza varios ámbitos: hipoteca, fiscal") for m in cruzada.motivos)


def test_the_mortgage_in_the_case_is_a_large_amount() -> None:
    pregunta = "¿Me conviene cambiar la hipoteca de variable a fijo?"
    sin_caso = es_decision_dificil(pregunta)
    prestamo = json.dumps({"capital_inicial": 180000, "tipo": "variable"})
    con_caso = es_decision_dificil(pregunta, None, caso(hipoteca_prestamo=prestamo))
    assert con_caso.puntuacion == sin_caso.puntuacion + 1.5
    assert "importe grande: la hipoteca de tu expediente (180.000 €)" in con_caso.motivos


def test_the_hint_for_the_brain() -> None:
    assert "Parece una decisión difícil" in es_decision_dificil(DIFICILES[0]).como_pista()
    assert "No parece" in es_decision_dificil(FACTUALES[0]).como_pista()
    assert "pedido expresamente" in es_decision_dificil(PEDIDOS[0]).como_pista()
    assert es_decision_dificil("") == Deteccion(dificil=False, motivos=["mensaje vacío"])

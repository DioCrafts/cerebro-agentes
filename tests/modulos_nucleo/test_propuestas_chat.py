"""The modules' tax proposals in the chat (``modulos/propuestas_chat.py``): scripted provider, real mortgage module.

The central agent sees the pending proposal in its first user message, offers it, and records it ONLY through
``aceptar_propuesta`` after the user's literal «sí» to an offer that showed the figure; a «no» kept in the decision
memory stops the offer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
import json

import pytest

from hacienda_gpt import settings
from hacienda_gpt.agent.agent import TaxAgent
from hacienda_gpt.decision.schemas import CaseState, Fact, FactValueType
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.memoria import reloj
from hacienda_gpt.memoria.almacen import olvidar_almacenes
from hacienda_gpt.memoria.servicio import registrar
from hacienda_gpt.modulos import registro
from hacienda_gpt.modulos.contrato import ContextoModulo
from hacienda_gpt.modulos.fiscal import MODULO as FISCAL
from hacienda_gpt.modulos.hipoteca import MODULO as HIPOTECA
from hacienda_gpt.modulos.hipoteca import pagos_del_ejercicio
from hacienda_gpt.modulos.propuestas_chat import PropuestasChat, es_confirmacion, muestra_la_cifra
from hacienda_gpt.modulos.puentes import HECHO_VIVIENDA_PRE2013, propuestas_para
from tests.agent.fakes import FakeSearcher, corpus, text_turn, tool_turn, verdict
from tests.modulos_nucleo.test_especialistas import RecordingProvider

HOY = date(2026, 11, 16)
# A fixed-rate loan for a home bought in 2010 (no index needed), with an early repayment and its fee in 2026.
PRESTAMO = {
    "capital_inicial": "150000",
    "fecha_firma": "2010-03-15",
    "plazo_meses": 360,
    "tipo": "fijo",
    "tipo_inicial": "4",
    "vivienda_habitual": True,
    "adquisicion_antes_2013": True,
    "amortizaciones_realizadas": [{"fecha": "2026-05-10", "importe": "3000", "comision_pagada": "15"}],
}
MODULOS = [FISCAL, HIPOTECA]


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    registro.reiniciar()
    monkeypatch.setattr(reloj, "hoy", lambda: HOY)
    path = str(tmp_path / "estado.sqlite3")
    SQLiteCaseStateStore(path)
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", path)
    yield path
    olvidar_almacenes()
    registro.reiniciar()


def _caso(prestamo: dict | None = None, **otros: float) -> CaseState:
    facts = [
        Fact(
            fact_id="fact_hipoteca_prestamo",
            name="hipoteca_prestamo",
            value=json.dumps(prestamo or PRESTAMO),
            value_type=FactValueType.STRING,
            source="user_edit",
            confidence=1.0,
        ),
        *(
            Fact(fact_id=f"fact_{k}", name=k, value=v, value_type=FactValueType.NUMBER, source="t", confidence=1.0)
            for k, v in otros.items()
        ),
    ]
    return CaseState(case_id="c1", user_id="user-c1", jurisdiction="ES", tax_period="2026", facts=facts)


def _esperado() -> Decimal:
    pagos = pagos_del_ejercicio(ContextoModulo(perfil={"hipoteca_prestamo": PRESTAMO}, hoy=HOY), 2026)
    assert pagos is not None
    return sum(pagos.values(), Decimal(0)).quantize(Decimal("0.01"))


def _cifra(valor: Decimal) -> str:
    return f"{valor:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _oferta() -> str:
    return (
        f"Tu módulo de hipoteca calcula que este año pagaste {_cifra(_esperado())} € (capital, intereses y la "
        "amortización anticipada) — ¿lo uso para tu deducción por vivienda?"
    )


def _agente(provider) -> TaxAgent:
    return TaxAgent(provider=provider, searcher=FakeSearcher(corpus()))


def _aceptar(cita: str) -> tuple:
    return ("aceptar_propuesta", {"puente": "hipoteca", "cita_del_usuario": cita})


def test_the_bridge_sums_what_the_law_counts_and_the_module_knows() -> None:
    (propuesta,) = propuestas_para(_caso(), MODULOS, hoy=HOY)
    pagos = pagos_del_ejercicio(ContextoModulo(perfil={"hipoteca_prestamo": PRESTAMO}, hoy=HOY), 2026)
    assert pagos is not None and pagos["amortizaciones_anticipadas"] == Decimal("3000")
    assert pagos["comisiones_amortizacion_anticipada"] == Decimal("15")
    assert Decimal(str(propuesta.valor)) == _esperado()
    # Each part shown, the fee as «demás gastos derivados» of the financing (art. 68.1.1.º, 2012 wording).
    assert set(propuesta.desglose) == {
        "capital",
        "intereses",
        "amortizaciones_anticipadas",
        "comisiones_amortizacion_anticipada",
    }
    assert propuesta.desglose["comisiones_amortizacion_anticipada"] == "15,00 €"
    texto = propuesta.explicacion
    assert "1 de enero de 2013" in texto and "anterior a 2013" in texto  # the condition nobody checks
    assert "9.040,00 €" in texto and "15 %" in texto  # the calculator's own limits, read from its parameters
    assert "cobertura" in texto and "varios titulares" in texto  # what the module does not know


@pytest.mark.parametrize(
    "cambio",
    [{"adquisicion_antes_2013": False}, {"adquisicion_antes_2013": None}, {"vivienda_habitual": False}],
)
def test_no_proposal_unless_a_main_home_bought_before_2013(cambio: dict) -> None:
    assert propuestas_para(_caso({**PRESTAMO, **cambio}), MODULOS, hoy=HOY) == []


def test_no_proposal_once_the_fact_is_known_or_the_module_is_off() -> None:
    assert propuestas_para(_caso(**{HECHO_VIVIENDA_PRE2013: 4000.0}), MODULOS, hoy=HOY) == []
    assert propuestas_para(_caso(), [FISCAL], hoy=HOY) == []
    assert PropuestasChat.para(_caso(), [FISCAL], query="hola") is None
    assert PropuestasChat.para(None, MODULOS, query="hola") is None


def test_the_agent_sees_the_proposal_and_can_cite_it() -> None:
    provider = RecordingProvider([text_turn("Respuesta.")], [verdict("pregunta_aclaratoria")])
    _agente(provider).run("¿Qué me puedo deducir este año?", case=_caso(), modulos=MODULOS)

    primero = provider.starts[0]
    mensaje = primero["user_message"]
    assert "<propuestas_de_modulos>" in mensaje and "puente `hipoteca`" in mensaje
    assert f"{_cifra(_esperado())} €" in mensaje and "[F" in mensaje  # citable figure from the ledger
    assert "`fiscal:propuesta:hipoteca`" in mensaje and "Aún no ofrecida." in mensaje
    assert "Cifras que proponen los módulos del hogar" in primero["system"]
    assert "aceptar_propuesta" in [t.name for t in primero["tools"]]


def test_an_offer_already_made_in_the_conversation_is_marked() -> None:
    provider = RecordingProvider([text_turn("Respuesta.")], [verdict("pregunta_aclaratoria")])
    _agente(provider).run(
        "¿Y el IBI se deduce?",
        chat_history=[{"role": "user", "content": "Hola"}, {"role": "assistant", "content": _oferta()}],
        case=_caso(),
        modulos=MODULOS,
    )
    assert "Ya ofrecida en esta conversación." in provider.starts[0]["user_message"]


def test_accepting_records_the_bridge_figure_with_its_provenance() -> None:
    provider = RecordingProvider(
        [tool_turn(_aceptar("Sí, úsalo")), text_turn("Hecho.")], [verdict("pregunta_aclaratoria")]
    )
    result = _agente(provider).run(
        "Sí, úsalo para la renta.",
        chat_history=[{"role": "user", "content": "¿Qué me deduzco?"}, {"role": "assistant", "content": _oferta()}],
        case=_caso(),
        modulos=MODULOS,
    )

    assert not provider.tool_results[0].is_error, provider.tool_results[0].content
    assert result.updated_case is not None
    hecho = next(f for f in result.updated_case.facts if f.name == HECHO_VIVIENDA_PRE2013)
    assert Decimal(str(hecho.value)) == _esperado()
    assert hecho.source == "puente:hipoteca" and hecho.value_type is FactValueType.NUMBER


@pytest.mark.parametrize(
    ("query", "historial", "cita", "motivo"),
    [
        # The figure was never shown to the user.
        ("Sí, úsalo.", [{"role": "assistant", "content": "¿Quieres que mire tu hipoteca?"}], "Sí, úsalo", "ver antes"),
        # Words the user did not write in this message (an earlier «sí» does not count).
        ("¿Y eso cuánto me ahorra?", None, "Sí, úsalo", "literalmente"),
        # A «no» is not a «yes».
        ("No, no lo uses.", None, "no lo uses", "negación"),
        ("Déjame pensarlo.", None, "Déjame pensarlo", "no dicen que sí"),
    ],
)
def test_accepting_needs_the_users_literal_yes_to_the_shown_figure(query, historial, cita, motivo) -> None:
    historial = historial or [
        {"role": "user", "content": "Sí, úsalo"},
        {"role": "assistant", "content": _oferta()},
    ]
    provider = RecordingProvider([tool_turn(_aceptar(cita)), text_turn("Vale.")], [verdict("pregunta_aclaratoria")])
    result = _agente(provider).run(query, chat_history=historial, case=_caso(), modulos=MODULOS)

    assert provider.tool_results[0].is_error and motivo in provider.tool_results[0].content
    assert result.updated_case is None


def test_a_figure_that_changed_since_the_offer_must_be_shown_again() -> None:
    viejo = "Tu módulo de hipoteca calcula que este año pagaste 1.234,56 € — ¿lo uso para tu deducción por vivienda?"
    provider = RecordingProvider([tool_turn(_aceptar("Sí")), text_turn("Vale.")], [verdict("pregunta_aclaratoria")])
    result = _agente(provider).run(
        "Sí", chat_history=[{"role": "assistant", "content": viejo}], case=_caso(), modulos=MODULOS
    )
    assert provider.tool_results[0].is_error and result.updated_case is None


@pytest.mark.parametrize("ambito", ["fiscal:propuesta:hipoteca", "fiscal:propuesta", "fiscal"])
def test_a_rejected_proposal_is_not_offered_again(entorno: str, ambito: str) -> None:
    registrar(entorno, case_id="c1", user_id="user-c1", ambito=ambito, tipo="rechazo", razon="no", fuente="chat")
    chat = PropuestasChat.para(_caso(), MODULOS, query="¿Qué me deduzco?")
    assert chat is not None and chat.ofrecibles() == [] and chat.contexto() == ""

    provider = RecordingProvider([text_turn("Respuesta.")], [verdict("pregunta_aclaratoria")])
    _agente(provider).run("¿Qué me deduzco?", case=_caso(), modulos=MODULOS)
    assert "<propuestas_de_modulos>" not in provider.starts[0]["user_message"]
    assert "Cifras que proponen los módulos del hogar" not in provider.starts[0]["system"]


def test_an_expired_rejection_lets_it_come_back(entorno: str) -> None:
    registrar(
        entorno,
        case_id="c1",
        user_id="user-c1",
        ambito="fiscal:propuesta:hipoteca",
        tipo="rechazo",
        razon="ahora no",
        fuente="chat",
        caducidad=date(2026, 12, 1),
    )
    assert PropuestasChat.para(_caso(), MODULOS, query="x").contexto() == ""  # type: ignore[union-attr]
    chat = PropuestasChat.para(_caso(), MODULOS, query="x", hoy=date(2026, 12, 2))
    assert chat is not None and len(chat.ofrecibles()) == 1


def test_the_agent_records_a_no_on_the_proposals_ambito() -> None:
    provider = RecordingProvider(
        [
            tool_turn(
                (
                    "registrar_decision",
                    {
                        "ambito": "fiscal:propuesta:hipoteca",
                        "tipo": "rechazo",
                        "cita_del_usuario": "no quiero usar la cifra de la hipoteca",
                    },
                )
            ),
            text_turn("De acuerdo."),
        ],
        [verdict("pregunta_aclaratoria")],
    )
    _agente(provider).run(
        "No quiero usar la cifra de la hipoteca, prefiero el certificado del banco.",
        chat_history=[{"role": "assistant", "content": _oferta()}],
        case=_caso(),
        modulos=MODULOS,
    )
    assert not provider.tool_results[0].is_error, provider.tool_results[0].content
    assert PropuestasChat.para(_caso(), MODULOS, query="x").contexto() == ""  # type: ignore[union-attr]


def test_confirmation_and_figure_matching_rules() -> None:
    assert es_confirmacion("Sí, úsalo", "sí, úsalo por favor") is None
    assert es_confirmacion("de acuerdo", "De acuerdo, adelante") is None
    assert es_confirmacion("sí", "Sí") is None
    assert es_confirmacion("sí", "No") is not None
    assert es_confirmacion("vale pero no", "Vale pero no la uses") is not None
    (propuesta,) = propuestas_para(_caso(), MODULOS, hoy=HOY)
    cifra = _cifra(Decimal(str(propuesta.valor)))
    assert muestra_la_cifra(f"pagaste {cifra} €", propuesta)
    assert muestra_la_cifra(f"pagaste {cifra.replace('.', '')} €", propuesta)
    assert not muestra_la_cifra(f"pagaste 1{cifra} €", propuesta)
    assert not muestra_la_cifra("pagaste 12,00 €", propuesta)

"""The brain's ``convocar_consejo``: offered only for a hard decision (the deterministic detector), it NEVER runs the
council — it creates a pending session with its estimate and the answer carries the sentence for the card."""

from __future__ import annotations

import re
from typing import Any

from asistente.nucleo.cerebro.herramientas import herramientas_del_cerebro
from asistente.nucleo.consejo.servicio import CONVOCAR, herramienta_convocar, servicio_consejo
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import NotificationStore
from tests.agent.fakes import text_turn, tool_turn
from tests.cerebro.fakes import HIPOTECA, EspecialistaFalso, Grabador, cerebro, modulos
from .fakes import caso

DECISION = "¿Me conviene amortizar la hipoteca o invertir en un fondo indexado?"


def _caso(db: str) -> Any:
    case = SQLiteCaseStateStore(db).get_case("c1")
    assert case is not None
    return case


def test_the_tool_is_offered_only_for_hard_decisions(db: str) -> None:
    case = _caso(db)
    assert herramienta_convocar(case=case, consulta="¿Cuánto me ahorro si amortizo 10.000 €?") is None
    assert herramienta_convocar(case=case, consulta="¿Me conviene la tarifa fija o la variable de la luz?") is None
    assert herramienta_convocar(case=None, consulta=DECISION) is None
    herramienta = herramienta_convocar(case=case, consulta=DECISION)
    assert herramienta is not None and herramienta.deteccion.dificil
    assert [spec.name for spec, _ in herramienta.herramientas] == [CONVOCAR]
    assert "Parece una decisión difícil" in herramienta.addendum and "{pista}" not in herramienta.addendum


def test_the_tool_never_runs_the_council_it_creates_a_pending_session(db: str) -> None:
    herramienta = herramienta_convocar(
        case=_caso(db),
        consulta=DECISION,
        chat_history=[{"role": "user", "content": "Prefiero no quedarme sin ahorros."}],
    )
    assert herramienta is not None
    _spec, ejecutar = herramienta.herramientas[0]
    salida = ejecutar({"pregunta": "algo que el modelo reescribió", "motivo": "Compromete los ahorros"})
    assert not salida.is_error
    nota = re.search(
        r"«(Te propongo consultarlo con el consejo: coste estimado [\d,]+ USD, unos \d+ minutos?\. "
        r"Confírmalo en la tarjeta\.)»",
        salida.content,
    )
    assert nota is not None and "NO se ha ejecutado" in salida.content
    sesiones = servicio_consejo().listar("c1")
    assert len(sesiones) == 1
    registro = sesiones[0]
    assert registro.sesion.estado == "pendiente_confirmacion"
    # The session keeps the user's own question (not the model's rewording), and its previous words.
    assert registro.sesion.pregunta_usuario == DECISION
    assert registro.extra.origen == "cerebro" and registro.extra.motivo == "Compromete los ahorros"
    assert registro.extra.opciones_sugeridas == ["Amortizar la hipoteca", "Invertir en un fondo indexado"]
    assert registro.extra.mensajes_previos == ["Prefiero no quedarme sin ahorros."]
    assert NotificationStore(db).list("c1") == []
    # Calling it again does not convene twice.
    ejecutar({"pregunta": DECISION, "motivo": "otra vez"})
    assert len(servicio_consejo().listar("c1")) == 1


def test_when_the_user_asks_for_the_council_the_brains_statement_is_kept(db: str) -> None:
    herramienta = herramienta_convocar(case=_caso(db), consulta="Consúltalo con el consejo")
    assert herramienta is not None and herramienta.deteccion.pedido_por_usuario
    _spec, ejecutar = herramienta.herramientas[0]
    ejecutar({"pregunta": "¿Aceptar la herencia del padre o renunciar a ella?", "motivo": "Lo ha pedido"})
    assert (
        servicio_consejo().listar("c1")[0].sesion.pregunta_usuario
        == "¿Aceptar la herencia del padre o renunciar a ella?"
    )


def test_switched_off_no_tool(db: str, monkeypatch) -> None:
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "CONSEJO_ACTIVO", False)
    assert herramienta_convocar(case=_caso(db), consulta=DECISION) is None


def test_the_brains_toolbox_gets_it_on_a_hard_decision_only(db: str) -> None:
    from asistente.nucleo.agent.evidence import EvidenceLedger
    from asistente.nucleo.cerebro.contrato import ExpedienteCompartido

    def nombres(consulta: str) -> list[str]:
        return herramientas_del_cerebro(
            proveedor=None,
            expediente=ExpedienteCompartido(_caso(db)),
            ledger=EvidenceLedger(),
            consulta=consulta,
            chat_history=[],
            modulos=[],
        ).nombres

    assert CONVOCAR in nombres(DECISION)
    assert CONVOCAR not in nombres("¿Cuándo se presenta el modelo 303?")


def test_a_brain_turn_proposes_the_council_and_the_answer_passes_the_figures_gate(db: str) -> None:
    sintesis = Grabador(nombre="sintesis")
    hipoteca = EspecialistaFalso(
        "hipoteca",
        "Hipoteca",
        calculo="Amortizar 20.000 € ahorra 6.400 € de intereses.",
        cifras=[("ahorro", "6400", "EUR")],
    )

    def respuesta() -> Any:
        nota = re.search(r"«([^»]+)»", sintesis.tool_results[-1].content)
        assert nota is not None
        return text_turn(f"Según el especialista de Hipoteca, amortizar ahorra 6.400 € de intereses. {nota.group(1)}")

    convocar = tool_turn((CONVOCAR, {"pregunta": DECISION, "motivo": "Compromete los ahorros"}))
    sintesis.turns = [convocar, respuesta]  # type: ignore[list-item]  # a callable turn is evaluated when it is due
    brain = cerebro(sintesis=sintesis, especialistas={"hipoteca": hipoteca}, modelo_intencion=False)
    resultado = brain.run(DECISION, case=caso("c1", "u1"), modulos=modulos(HIPOTECA))

    herramientas = [t.name for t in sintesis.inicios[0]["tools"]]
    assert CONVOCAR in herramientas
    assert "El consejo, para decisiones difíciles" in sintesis.inicios[0]["system"]
    assert "Te propongo consultarlo con el consejo" in resultado.answer
    assert resultado.cerebro is not None and resultado.cerebro.puerta is not None and resultado.cerebro.puerta.ok
    sesion = servicio_consejo().listar("c1")[0].sesion
    assert sesion.estado == "pendiente_confirmacion" and sesion.dictamen is None
    assert NotificationStore(db).list("c1") == []

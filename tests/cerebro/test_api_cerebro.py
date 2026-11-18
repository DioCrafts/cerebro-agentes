"""/agent and /agent/stream with the household brain behind them (and the legacy advisor still served)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from asistente.nucleo.agent.agent import TaxAgent
from asistente.plataforma.api.api import app, get_agent
from tests.agent.fakes import FakeSearcher, ScriptedProvider, corpus, text_turn, tool_turn, verdict
from tests.cerebro.fakes import Grabador, MemoriaFalsa, cerebro

client = TestClient(app)
CONVERSACION = "conv-0a1b2c3d4e5f"


def teardown_function() -> None:
    app.dependency_overrides.pop(get_agent, None)


def _caso() -> str:
    return client.post("/cases", json={"user_id": "u-cerebro-api", "jurisdiction": "ES", "tax_period": "2025"}).json()[
        "case_id"
    ]


def _stream(payload: dict) -> list[dict]:
    respuesta = client.post("/agent/stream", json=payload)
    assert respuesta.status_code == 200
    return [json.loads(linea) for linea in respuesta.text.splitlines() if linea]


def test_the_stream_carries_the_plan_the_answer_and_the_brains_report() -> None:
    memoria = MemoriaFalsa(["Vive en Madrid"])
    fiscal = Grabador(
        [tool_turn(("buscar_fuentes", {"consulta": "plazo reinversión"})), text_turn("El plazo es de dos años [F2].")],
        [verdict("respuesta", ("Plazo dos años", ["F2"], "respaldada"))],
    )
    brain = cerebro(fiscal=fiscal, memoria=memoria)
    app.dependency_overrides[get_agent] = lambda: brain
    case_id = _caso()

    eventos = _stream(
        {
            "query": "¿Qué plazo tengo para reinvertir en vivienda habitual a efectos del IRPF?",
            "case_id": case_id,
            "conversacion_id": CONVERSACION,
        }
    )

    progreso = [e["data"] for e in eventos if e["event"] == "progress"]
    assert progreso[0]["type"] == "plan" and progreso[0]["intencion"]["encargos"][0]["especialista"] == "fiscal"
    assert {"especialista", "tool_call", "text_delta", "verifying"} <= {p["type"] for p in progreso}
    assert [e["event"] for e in eventos if e["event"] != "progress"] == ["result"]
    resultado = eventos[-1]["data"]
    assert resultado["mode"] == "verified" and resultado["answer"] == "El plazo es de dos años [F2]."
    assert resultado["cerebro"]["ruta"] == "paso_directo" and resultado["cerebro"]["conversacion_id"] == CONVERSACION
    assert resultado["cerebro"]["informes"][0]["especialista"] == "fiscal"
    # The memory was asked with the conversation and handed the finished turn.
    assert memoria.consultas[0]["conversacion_id"] == CONVERSACION and memoria.consultas[0]["case_id"] == case_id
    # Read for the same owner the turn is stored under.
    assert memoria.consultas[0]["user_id"] == "u-cerebro-api"
    (turno,) = memoria.turnos
    assert turno.conversacion_id == CONVERSACION and turno.case_id == case_id and turno.user_id == "u-cerebro-api"
    assert turno.especialistas == ["fiscal"] and turno.modo == "verified"
    assert turno.respuesta == "El plazo es de dos años [F2]."
    # The audit trail says what the brain did.
    auditoria = client.get(f"/cases/{case_id}/audit").json()["events"]
    (respuesta,) = [e for e in auditoria if e.get("event_type") == "agent_answer"]
    assert respuesta["cerebro"]["ruta"] == "paso_directo" and respuesta["cerebro"]["especialistas"] == ["fiscal"]


def test_the_plain_endpoint_hands_the_turn_to_the_memory_after_answering() -> None:
    memoria = MemoriaFalsa()
    sintesis = Grabador([text_turn("¡Hola! ¿En qué te ayudo?")])
    brain = cerebro(sintesis=sintesis, memoria=memoria)
    app.dependency_overrides[get_agent] = lambda: brain

    respuesta = client.post("/agent", json={"query": "Hola", "conversacion_id": CONVERSACION})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["cerebro"]["ruta"] == "directa" and cuerpo["cerebro"]["intencion"]["tipo"] == "charla"
    assert [t.pregunta for t in memoria.turnos] == ["Hola"] and memoria.turnos[0].especialistas == []


def test_without_a_conversation_id_nothing_is_remembered() -> None:
    memoria = MemoriaFalsa()
    brain = cerebro(sintesis=Grabador([text_turn("¡Hola!")]), memoria=memoria)
    app.dependency_overrides[get_agent] = lambda: brain
    assert client.post("/agent", json={"query": "Hola"}).status_code == 200
    assert memoria.turnos == [] and memoria.consultas[0]["conversacion_id"] is None


def test_a_malformed_conversation_id_is_refused() -> None:
    app.dependency_overrides[get_agent] = lambda: cerebro()
    assert client.post("/agent", json={"query": "Hola", "conversacion_id": "x"}).status_code == 422
    assert client.post("/agent", json={"query": "Hola", "conversacion_id": "con espacios 123"}).status_code == 422


def test_the_legacy_advisor_is_served_as_before_even_with_a_conversation_id() -> None:
    provider = ScriptedProvider([text_turn("El plazo es de dos años.")], [verdict("respuesta")])
    app.dependency_overrides[get_agent] = lambda: TaxAgent(provider=provider, searcher=FakeSearcher(corpus()))
    cuerpo = client.post("/agent", json={"query": "¿Plazo?", "conversacion_id": CONVERSACION}).json()
    assert cuerpo["cerebro"] is None and cuerpo["answer"] == "El plazo es de dos años."

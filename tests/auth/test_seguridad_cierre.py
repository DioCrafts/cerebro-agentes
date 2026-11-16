"""The findings of docs/seguridad.md closed after the review: the legacy /qa chain and the API documentation are
gone with accounts on (S-14, S-18), the question and the history that reach the model are capped (S-15) and a
failure's text stays in the log (S-11)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.decision.fact_extractor import FactExtractionError
from asistente.plataforma import settings
from asistente.plataforma.api import api as api_module
from asistente.plataforma.api import security
from asistente.plataforma.api.api import app, get_agent, get_fact_extractor, get_qa_chain
from .conftest import CONSENTS, Env

client = TestClient(app)


class _Grabador:
    """An agent / extractor that records what it was given and then fails like a provider (no model, no answer)."""

    name, model = "fake", "fake-1"

    def __init__(self, mensaje: str = "clave sk-XXXX rechazada en /srv/hacienda/state.sqlite3") -> None:
        self.mensaje = mensaje
        self.historiales: list[list[Any]] = []
        self.preguntas: list[str] = []

    def run(self, query: str, *, chat_history: list[Any] | None = None, **_kwargs: Any) -> Any:
        self.preguntas.append(query)
        self.historiales.append(list(chat_history or []))
        raise ProviderError(self.mensaje)

    def extract(self, user_input: str, chat_history: list[Any], current_case_state: Any) -> Any:
        self.preguntas.append(user_input)
        self.historiales.append(list(chat_history))
        raise FactExtractionError(self.mensaje)


@pytest.fixture
def grabador() -> Any:
    fake = _Grabador()
    app.dependency_overrides[get_agent] = lambda: fake
    app.dependency_overrides[get_fact_extractor] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_agent, None)
    app.dependency_overrides.pop(get_fact_extractor, None)


def _historial(n: int, largo: int = 10) -> list[dict[str, str]]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{i:04d}" + "x" * (largo - 4)} for i in range(n)
    ]


# --- S-14: the legacy /qa chain --------------------------------------------------------------------------------


def test_qa_is_gone_with_accounts_even_for_a_session_and_never_builds_the_chain(env: Env) -> None:
    ana = env.register("ana@example.com")

    def no_construir():
        raise AssertionError("the unmetered chain was built")

    app.dependency_overrides[get_qa_chain] = no_construir
    try:
        assert env.client.post("/qa", json={"query": "¿IRPF?"}, headers=ana).status_code == 404
        assert env.client.post("/qa", json={"query": "¿IRPF?"}).status_code == 404
    finally:
        app.dependency_overrides.pop(get_qa_chain, None)


def test_qa_still_answers_without_accounts(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")

    class _Cadena:
        def invoke(self, inputs: dict) -> dict:
            return {"answer": "Respuesta", "context": []}

    app.dependency_overrides[get_qa_chain] = lambda: _Cadena()
    try:
        assert client.post("/qa", json={"query": "¿IRPF?"}).status_code != 404
    finally:
        app.dependency_overrides.pop(get_qa_chain, None)


# --- S-18: the API documentation -------------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", ["/docs", "/redoc", "/openapi.json"])
def test_the_api_documentation_is_hidden_with_accounts_unless_the_operator_opens_it(
    ruta: str, env: Env, monkeypatch
) -> None:
    monkeypatch.delenv("API_DOCS_PUBLICAS", raising=False)
    ana = env.register("ana@example.com")
    assert env.client.get(ruta).status_code == 404
    assert env.client.get(ruta, headers=ana).status_code == 404
    monkeypatch.setenv("API_DOCS_PUBLICAS", "true")
    assert env.client.get(ruta).status_code == 200
    # Without accounts it is there by default, and the operator can still close it.
    monkeypatch.delenv("API_DOCS_PUBLICAS")
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    assert env.client.get(ruta).status_code == 200
    monkeypatch.setenv("API_DOCS_PUBLICAS", "false")
    assert env.client.get(ruta).status_code == 404


def test_the_schema_still_lists_the_api_and_not_its_documentation_routes(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.delenv("API_DOCS_PUBLICAS", raising=False)
    esquema = client.get("/openapi.json").json()
    assert "/agent" in esquema["paths"] and "/docs" not in esquema["paths"]
    assert "/openapi.json" in client.get("/docs").text


# --- S-15: what reaches the model ------------------------------------------------------------------------------


def test_a_question_over_the_limit_is_a_422_in_spanish_before_any_model_call(env: Env, grabador, monkeypatch) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    larga = "¿" + "a" * security.DEFAULT_MAX_QUERY_CHARS + "?"
    for ruta, cuerpo in (
        ("/agent", {"query": larga}),
        ("/agent/stream", {"query": larga}),
        (f"/cases/{case_id}/turn", {"user_input": larga}),
    ):
        respuesta = env.client.post(ruta, json=cuerpo, headers=ana)
        assert respuesta.status_code == 422, ruta
        assert "La pregunta es demasiado larga" in respuesta.json()["detail"] and "20.000" in respuesta.json()["detail"]
    assert grabador.preguntas == []
    # The limit is a setting.
    monkeypatch.setenv("API_MAX_QUERY_CHARS", "10")
    assert env.client.post("/agent", json={"query": "una pregunta de más de diez"}, headers=ana).status_code == 422
    assert env.client.post("/agent", json={"query": "corta"}, headers=ana).status_code == 503
    assert grabador.preguntas == ["corta"]


def test_qa_caps_the_question_too_without_accounts(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    app.dependency_overrides[get_qa_chain] = lambda: object()
    try:
        respuesta = client.post("/qa", json={"query": "a" * (security.DEFAULT_MAX_QUERY_CHARS + 1)})
    finally:
        app.dependency_overrides.pop(get_qa_chain, None)
    assert respuesta.status_code == 422 and "demasiado larga" in respuesta.json()["detail"]


def test_the_history_sent_to_the_model_is_trimmed_oldest_first(env: Env, grabador, monkeypatch) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    historial = _historial(100)
    env.client.post("/agent", json={"query": "hola", "chat_history": historial}, headers=ana)
    env.client.post("/agent/stream", json={"query": "hola", "chat_history": historial}, headers=ana)
    env.client.post(f"/cases/{case_id}/turn", json={"user_input": "hola", "chat_history": historial}, headers=ana)
    assert len(grabador.historiales) == 3
    for recibido in grabador.historiales:
        assert recibido == historial[-security.DEFAULT_MAX_HISTORY_MESSAGES :]  # the newest, in order
        assert recibido[0]["role"] == "user"

    # By characters: 7 messages of 10 000 fit in 60 000 only up to 6; the first kept must be a question.
    grabador.historiales.clear()
    env.client.post("/agent", json={"query": "hola", "chat_history": _historial(7, 10_000)}, headers=ana)
    (recibido,) = grabador.historiales
    assert [m["content"][:4] for m in recibido] == ["0002", "0003", "0004", "0005", "0006"]
    # Both limits are settings.
    monkeypatch.setenv("API_MAX_HISTORY_MESSAGES", "3")
    assert security.recortar_historial(_historial(9)) == _historial(9)[-3:]
    monkeypatch.setenv("API_MAX_HISTORY_CHARS", "25")
    assert security.recortar_historial(_historial(9)) == _historial(9)[-1:]  # «0007» would start with an answer


def test_trimming_never_cuts_a_message_and_keeps_short_conversations_whole() -> None:
    corta = _historial(6)
    assert security.recortar_historial(corta) == corta
    assert security.recortar_historial([{"role": "user", "content": "x" * 70_000}]) == []
    assert security.recortar_historial([]) == []


# --- S-11: a failure's text stays in the log -------------------------------------------------------------------


def test_provider_failures_show_a_fixed_message_and_log_the_detail(env: Env, grabador, caplog, monkeypatch) -> None:
    ana = env.register("ana@example.com", consents={**CONSENTS, "envio_documentos_a_proveedor_llm": True})
    case_id = env.new_case(ana)
    with caplog.at_level(logging.ERROR, logger="asistente.plataforma.api.api"):
        agente = env.client.post("/agent", json={"query": "hola"}, headers=ana)
        turno = env.client.post(f"/cases/{case_id}/turn", json={"user_input": "hola"}, headers=ana)
    assert agente.status_code == turno.status_code == 503
    for respuesta in (agente, turno):
        assert "sk-XXXX" not in respuesta.text and "/srv/" not in respuesta.text
        assert "Inténtalo de nuevo" in respuesta.json()["detail"]
    assert "sk-XXXX" in caplog.text  # the operator still sees it

    def lector_roto():
        raise RuntimeError("ANTHROPIC_API_KEY=sk-XXXX no válida")

    monkeypatch.setattr(api_module, "_build_document_provider", lector_roto)
    documento = {"filename": "c.txt", "content_base64": "UmV0ZW5jaW9uZXM="}
    respuesta = env.client.post(f"/cases/{case_id}/documents", json=documento, headers=ana)
    assert respuesta.status_code == 503 and "sk-XXXX" not in respuesta.text
    assert respuesta.json()["detail"] == "El lector de documentos no está disponible."

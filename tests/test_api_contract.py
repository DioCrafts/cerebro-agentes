from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.decision.fact_extractor import ExtractionPayload
from asistente.plataforma.api.api import app, get_fact_extractor

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_case_and_get_case_and_audit() -> None:
    created = client.post("/cases", json={"user_id": "u1", "jurisdiction": "ES", "tax_period": "2025"})
    assert created.status_code == 200
    case_id = created.json()["case_id"]

    got = client.get(f"/cases/{case_id}")
    assert got.status_code == 200
    assert got.json()["case_id"] == case_id

    audit = client.get(f"/cases/{case_id}/audit")
    assert audit.status_code == 200
    assert audit.json()["case_id"] == case_id


def test_post_turn_contract() -> None:
    created = client.post("/cases", json={"user_id": "u2", "jurisdiction": "ES", "tax_period": "2025"})
    case_id = created.json()["case_id"]
    turn = client.post(
        f"/cases/{case_id}/turn", json={"user_input": "Soy residente en España y tengo dudas de IRPF 2025"}
    )
    assert turn.status_code == 200
    body = turn.json()
    assert body["case_id"] == case_id
    assert "facts" in body
    assert "missing_facts" in body
    assert "candidate_obligation_ids" in body
    assert "next_questions" in body
    # The web UI renders full obligation objects; they must stay in sync
    # with the id list the response also exposes.
    assert isinstance(body["obligations"], list)
    assert [o["obligation_id"] for o in body["obligations"]] == body["candidate_obligation_ids"]


def test_post_turn_updates_case_tax_period_from_extracted_fact() -> None:
    created = client.post("/cases", json={"user_id": "u3", "jurisdiction": "ES", "tax_period": "2025"})
    case_id = created.json()["case_id"]

    turn = client.post(
        f"/cases/{case_id}/turn",
        json={"user_input": "Tengo dudas de IRPF para 2024 y soy residente en España"},
    )
    assert turn.status_code == 200

    got = client.get(f"/cases/{case_id}")
    assert got.status_code == 200
    assert got.json()["tax_period"] == "2024"


def test_post_turn_forwards_chat_history_to_extractor() -> None:
    captured: dict = {}

    class _SpyExtractor:
        def extract(self, user_input, chat_history, current_case_state):
            captured["user_input"] = user_input
            captured["chat_history"] = chat_history
            return ExtractionPayload(intent="iva", intent_confidence=0.5, extracted_facts=[])

    created = client.post("/cases", json={"user_id": "uh", "jurisdiction": "ES", "tax_period": "2025"})
    case_id = created.json()["case_id"]

    history = [
        {"role": "user", "content": "Tengo una empresa"},
        {"role": "assistant", "content": "De acuerdo, cuéntame más."},
    ]
    app.dependency_overrides[get_fact_extractor] = lambda: _SpyExtractor()
    try:
        turn = client.post(
            f"/cases/{case_id}/turn",
            json={"user_input": "¿Y el IVA de 2024?", "chat_history": history},
        )
    finally:
        app.dependency_overrides.pop(get_fact_extractor, None)

    assert turn.status_code == 200
    assert captured["user_input"] == "¿Y el IVA de 2024?"
    assert captured["chat_history"] == history


def test_post_turn_chat_history_defaults_to_empty() -> None:
    created = client.post("/cases", json={"user_id": "uh2", "jurisdiction": "ES", "tax_period": "2025"})
    case_id = created.json()["case_id"]
    # Backward compatible: callers may still omit chat_history entirely.
    turn = client.post(f"/cases/{case_id}/turn", json={"user_input": "Dudas de IRPF"})
    assert turn.status_code == 200


def test_not_found_case() -> None:
    r = client.get("/cases/does-not-exist")
    assert r.status_code == 404


def test_get_case_store_is_cached() -> None:
    from asistente.plataforma.api import api

    api._build_case_store.cache_clear()
    try:
        assert api.get_case_store() is api.get_case_store()
    finally:
        api._build_case_store.cache_clear()


def test_get_fact_extractor_is_cached() -> None:
    from asistente.plataforma.api import api

    api._build_fact_extractor.cache_clear()
    try:
        assert api.get_fact_extractor() is api.get_fact_extractor()
    finally:
        api._build_fact_extractor.cache_clear()


def test_thread_safe_singleton_caches_build_failure() -> None:
    # Negative caching: a broken build must NOT re-run the expensive factory on
    # every call (the multi-GB embedder reload storm). cache_clear() recovers.
    from asistente.plataforma.api.api import _thread_safe_singleton

    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        raise RuntimeError("boom")

    cached = _thread_safe_singleton(factory)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            cached()
    assert calls["n"] == 1  # failure cached: factory ran once, not per call

    cached.cache_clear()
    with pytest.raises(RuntimeError):
        cached()
    assert calls["n"] == 2  # cleared -> rebuilt

"""Feedback API with accounts: consent per answer, owner only, exported and erased with the account."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.agent.agent import TaxAgent
from asistente.nucleo.decision.schemas import Fact, FactValueType
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.evaluacion.captura import CONSENTIMIENTO_VERSION, FeedbackStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.api.api import app, get_agent, get_case_store, get_notification_store
from asistente.plataforma.api.routers.auth import get_login_limiter
from asistente.plataforma.auth.deps import get_auth_store
from asistente.plataforma.auth.ratelimit import LoginRateLimiter
from asistente.plataforma.auth.store import AuthStore
from tests.agent.fakes import FakeSearcher, ScriptedProvider, corpus, text_turn, tool_turn, verdict

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}
PREGUNTA = "¿Cuánto me sale la renta de 2025?"


@dataclass
class Env:
    client: TestClient
    db_path: str
    cases: SQLiteCaseStateStore

    def register(self, email: str) -> dict[str, str]:
        r = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def case_with_answer(self, headers: dict[str, str]) -> str:
        r = self.client.post("/cases", json={"user_id": "x", "tax_period": "2025"}, headers=headers)
        assert r.status_code == 200, r.text
        case_id = r.json()["case_id"]
        case = self.cases.get_case(case_id)
        assert case is not None
        hechos = [
            Fact(
                fact_id="fact_comunidad_autonoma",
                name="comunidad_autonoma",
                value="Madrid",
                value_type=FactValueType.STRING,
                source="usuario",
                confidence=1.0,
            ),
            Fact(
                fact_id="fact_rendimiento_trabajo_bruto",
                name="rendimiento_trabajo_bruto",
                value=30000.0,
                value_type=FactValueType.NUMBER,
                source="usuario",
                confidence=1.0,
            ),
        ]
        self.cases.save_case(case.model_copy(update={"facts": hechos}))
        provider = ScriptedProvider(
            [tool_turn(("calcular_irpf", {"ejercicio": 2025})), text_turn("Te sale a ingresar 9.999,00 € [F1].")],
            [verdict("respuesta", ("9.999,00 €", ["F1"], "no_respaldada"))],
        )
        app.dependency_overrides[get_agent] = lambda: TaxAgent(
            provider=provider, searcher=FakeSearcher(corpus()), max_revisions=0
        )
        r = self.client.post("/agent", json={"query": PREGUNTA, "case_id": case_id}, headers=headers)
        assert r.status_code == 200, r.text
        return case_id


@pytest.fixture
def env(tmp_path, monkeypatch) -> Iterator[Env]:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    db_path = str(tmp_path / "state.sqlite3")
    auth, cases, notifications = AuthStore(db_path), SQLiteCaseStateStore(db_path), NotificationStore(db_path)
    overrides = {
        get_auth_store: lambda: auth,
        get_case_store: lambda: cases,
        get_notification_store: lambda: notifications,
        get_login_limiter: lambda: LoginRateLimiter(50, 900),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield Env(client=TestClient(app), db_path=db_path, cases=cases)
    finally:
        for dependency in [*overrides, get_agent]:
            app.dependency_overrides.pop(dependency, None)


def _negativa(case_id: str, **extra) -> dict:  # type: ignore[no-untyped-def]
    return {
        "case_id": case_id,
        "pregunta": PREGUNTA,
        "respuesta": "Te sale a ingresar 9.999,00 € [F1].",
        "valoracion": "negativa",
        "tipo": "cifra_erronea",
        "comentario": "La calculadora da otra cifra",
        "cifra_correcta": "5.140,12 €",
        **extra,
    }


def test_consent_text_is_public_to_the_ui(env: Env) -> None:
    body = env.client.get("/evaluacion/consentimiento").json()
    assert body["version"] == CONSENTIMIENTO_VERSION and "ENMASCARADA" in body["texto"]
    assert "nunca en el código" in body["texto"] and "se borra con tu cuenta" in body["texto"]
    assert {t["clave"] for t in body["tipos"]} >= {
        "cifra_erronea",
        "modulo_erroneo",
        "afirmacion_inventada",
        "suma_indebida",
    }
    assert body["aprendizaje_version"] and "Aprender de los fallos" in body["aprendizaje_texto"]


def test_a_vote_alone_stores_no_conversation(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.case_with_answer(ana)
    r = env.client.post("/evaluacion/feedback", json={**_negativa(case_id)}, headers=ana)
    assert r.status_code == 201, r.text
    assert r.json()["guardado_para_evaluacion"] is False and r.json()["candidato"] is False
    # The verifier rejected the claim and the agent abstained: both are signals of the turn.
    assert set(r.json()["etiquetas"]) == {"abstencion", "afirmacion_rechazada", "cifra_erronea"}
    (detalle,) = env.client.get(f"/evaluacion/feedback?case_id={case_id}", headers=ana).json()["feedback"]
    assert detalle["contenido"] is None and detalle["registro"]["estado"] == "solo_valoracion"
    eventos = [e for e in env.cases.list_audit_events(case_id) if e["event_type"] == "evaluacion_valoracion"]
    assert len(eventos) == 1 and "caso" not in eventos[0]
    assert "9.999" not in str(eventos[0])


def test_storing_a_case_needs_consent_each_time(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.case_with_answer(ana)
    r = env.client.post("/evaluacion/feedback", json=_negativa(case_id, guardar_para_evaluacion=True), headers=ana)
    assert r.status_code == 422 and "consentimiento" in r.json()["detail"]
    sin_caso = {
        **_negativa(case_id),
        "case_id": None,
        "guardar_para_evaluacion": True,
        "version_consentimiento": CONSENTIMIENTO_VERSION,
    }
    r = env.client.post("/evaluacion/feedback", json=sin_caso, headers=ana)
    assert r.status_code == 422 and "expediente" in r.json()["detail"]
    otra = env.client.post("/evaluacion/feedback", json=_negativa(case_id, pregunta="¿Otra?"), headers=ana)
    assert otra.status_code == 404 and "historial" in otra.json()["detail"]


def test_opted_in_case_is_exported_withdrawable_and_erased_with_the_account(env: Env) -> None:
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    case_id = env.case_with_answer(ana)
    payload = _negativa(case_id, guardar_para_evaluacion=True, version_consentimiento=CONSENTIMIENTO_VERSION)
    r = env.client.post("/evaluacion/feedback", json=payload, headers=ana)
    assert r.status_code == 201 and r.json()["guardado_para_evaluacion"] is True
    feedback_id = r.json()["id"]

    # Nobody else can see, add to or withdraw it.
    assert env.client.get(f"/evaluacion/feedback?case_id={case_id}", headers=luis).status_code == 404
    assert env.client.post("/evaluacion/feedback", json=payload, headers=luis).status_code == 404
    assert env.client.delete(f"/evaluacion/feedback/{feedback_id}", headers=luis).status_code == 404

    (detalle,) = env.client.get(f"/evaluacion/feedback?case_id={case_id}", headers=ana).json()["feedback"]
    contenido = detalle["contenido"]
    assert contenido["correccion"]["cifra_correcta"] == "5.140,12 €"
    assert contenido["instantanea"]["hechos"] == {"comunidad_autonoma": "Madrid", "rendimiento_trabajo_bruto": 30000.0}
    assert [t["tool"] for t in contenido["tool_trace"]] == ["calcular_irpf"]

    # Exported with the account.
    export = env.client.get("/me/export", headers=ana).json()
    eventos = [e for c in export["cases"] for e in c["audit_events"] if e["event_type"] == "evaluacion_valoracion"]
    assert (
        eventos[0]["caso"]["pregunta"] == PREGUNTA and eventos[0]["consentimiento"]["version"] == CONSENTIMIENTO_VERSION
    )

    # Deleting a fact redacts its value from the stored case too.
    assert env.client.delete(f"/cases/{case_id}/facts/rendimiento_trabajo_bruto", headers=ana).status_code == 200
    contenido = env.client.get(f"/evaluacion/feedback?case_id={case_id}", headers=ana).json()["feedback"][0][
        "contenido"
    ]
    assert contenido["instantanea"]["hechos"]["rendimiento_trabajo_bruto"] != 30000.0

    # Withdrawn: the content leaves the audit trail.
    assert env.client.delete(f"/evaluacion/feedback/{feedback_id}", headers=ana).status_code == 204
    assert env.client.get(f"/evaluacion/feedback?case_id={case_id}", headers=ana).json()["feedback"] == []
    restos = [e for e in env.cases.list_audit_events(case_id) if e["event_type"] == "evaluacion_valoracion"]
    assert restos and all("caso" not in e and e.get("retirado_el") for e in restos)

    # A second opted-in case, then the account is erased: nothing is left anywhere.
    assert env.client.post("/evaluacion/feedback", json=payload, headers=ana).status_code == 201
    store = FeedbackStore(env.db_path)
    assert len(store.todos()) == 1
    assert env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    assert store.todos() == []
    assert env.cases.list_audit_events(case_id) == []

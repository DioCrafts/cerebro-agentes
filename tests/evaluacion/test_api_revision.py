"""The review queue over HTTP: owner or operator only (anybody else's candidate is a 404), the standing consent,
acceptance with expected behaviour, rejection, and the account's export and erasure."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json

from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.decision.schemas import Fact, FactValueType
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.evaluacion import privado, revision
from asistente.nucleo.evaluacion.captura import CONSENTIMIENTO_VERSION
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.api.api import app, get_case_store, get_notification_store
from asistente.plataforma.api.routers.auth import get_login_limiter
from asistente.plataforma.auth.deps import get_auth_store
from asistente.plataforma.auth.ratelimit import LoginRateLimiter
from asistente.plataforma.auth.store import AuthStore
from tests.evaluacion.test_revision import PREGUNTA, PRESTAMO, RESPUESTA, informe_cerebro

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}
OPERADOR = "clave-del-operador-larga"


@dataclass
class Env:
    client: TestClient
    db_path: str
    cases: SQLiteCaseStateStore

    def register(self, email: str) -> dict[str, str]:
        r = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def caso_con_turno(self, headers: dict[str, str], pregunta: str = PREGUNTA, **extra) -> str:  # type: ignore[no-untyped-def]
        r = self.client.post("/cases", json={"user_id": "x", "tax_period": "2026"}, headers=headers)
        assert r.status_code == 200, r.text
        case_id = r.json()["case_id"]
        case = self.cases.get_case(case_id)
        assert case is not None
        hecho = Fact(
            fact_id="fact_hipoteca_prestamo",
            name="hipoteca_prestamo",
            value=json.dumps(PRESTAMO),
            value_type=FactValueType.STRING,
            source="usuario",
            confidence=1.0,
        )
        self.cases.save_case(case.model_copy(update={"facts": [hecho]}))
        evento = {
            "event_type": "agent_answer",
            "input": pregunta,
            "mode": "partially_verified",
            "tool_trace": [
                {"tool": "hipoteca_cuadro", "agent": "especialista_hipoteca", "arguments": {"fecha": "2026-03-20"}}
            ],
            "claims": [],
            "cerebro": informe_cerebro(),
            **extra,
        }
        self.cases.append_audit_event(case_id, evento)
        return case_id

    def negativa(  # type: ignore[type-arg]
        self, headers: dict[str, str], case_id: str, *, guardar: bool = True, pregunta: str = PREGUNTA
    ) -> dict:
        payload = {
            "case_id": case_id,
            "pregunta": pregunta,
            "respuesta": RESPUESTA,
            "valoracion": "negativa",
            "tipo": "cifra_erronea",
            "cifra_correcta": "474,21 €",
            "guardar_para_evaluacion": guardar,
            "version_consentimiento": CONSENTIMIENTO_VERSION if guardar else None,
        }
        r = self.client.post("/evaluacion/feedback", json=payload, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]


@pytest.fixture
def env(tmp_path, monkeypatch) -> Iterator[Env]:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    monkeypatch.setenv("HACIENDA_OPERATOR_KEY", OPERADOR)
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
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


EDICION = {
    "categoria": "procedencia",
    "esperado": {
        "especialistas_obligatorios": ["hipoteca", "luz"],
        "cifras_prohibidas": ["532,00 €"],
        "cifra_esperada": {
            "herramienta": "hipoteca_cuadro",
            "argumentos": {"fecha": "2026-03-20"},
            "especialista": "hipoteca",
        },
        "puerta_sin_violaciones": True,
    },
}


def test_the_owner_reviews_their_own_candidates_and_nobody_else_sees_them(env: Env) -> None:
    ana, luis = env.register("ana.perez@example.com"), env.register("luis@example.com")
    case_id = env.caso_con_turno(ana)
    respuesta = env.negativa(ana, case_id)
    assert respuesta["candidato"] is True and "Revisión de casos" in respuesta["mensaje"]

    cola = env.client.get("/evaluacion/revision", headers=ana).json()
    (grupo,) = cola["grupos"]
    assert grupo["etiqueta_fallo"] == "Cifra equivocada" and grupo["frecuencia"] == 1 and not cola["operador"]
    assert "Ana" not in grupo["pregunta"] and "[nombre]" in grupo["pregunta"]  # the account's name, masked
    assert {c["clave"] for c in cola["categorias"]} >= {"intencion", "procedencia", "memoria", "regresion"}
    candidato_id = grupo["id"]

    # Somebody else: an empty queue, and 404 on the candidate, its acceptance and its rejection.
    assert env.client.get("/evaluacion/revision", headers=luis).json()["grupos"] == []
    assert env.client.get(f"/evaluacion/revision/{candidato_id}", headers=luis).status_code == 404
    assert (
        env.client.post(f"/evaluacion/revision/{candidato_id}/aceptar", json=EDICION, headers=luis).status_code == 404
    )
    assert (
        env.client.post(
            f"/evaluacion/revision/{candidato_id}/rechazar", json={"motivo": "no vale"}, headers=luis
        ).status_code
        == 404
    )
    assert env.client.get("/evaluacion/revision").status_code == 401

    detalle = env.client.get(f"/evaluacion/revision/{candidato_id}", headers=ana).json()
    candidato = detalle["candidato"]
    assert candidato["contenido"]["cerebro"]["intencion"]["resumen"] == "Consulto a Hipoteca y a Luz en paralelo"
    assert candidato["contenido"]["modos_especialistas"] == {"hipoteca": "verified", "luz": None}
    assert candidato["contenido"]["enmascarado"] and detalle["etiqueta_fallo"] == "Cifra equivocada"

    # Acceptance with problems: a 422 listing them; then a good one.
    malo = {**EDICION, "pregunta": "Mi DNI es 12345678Z"}
    r = env.client.post(f"/evaluacion/revision/{candidato_id}/aceptar", json=malo, headers=ana)
    assert r.status_code == 422 and "datos personales" in r.json()["detail"]
    r = env.client.post(f"/evaluacion/revision/{candidato_id}/aceptar", json=EDICION, headers=ana)
    assert r.status_code == 200, r.text
    aceptado = r.json()
    assert aceptado["categoria"] == "procedencia" and {e["tipo"] for e in aceptado["expectativas"]} >= {
        "cifra",
        "cifra_atribuida",
        "cifra_ausente",
        "especialistas",
        "puerta",
    }
    assert env.client.get("/evaluacion/revision", headers=ana).json()["grupos"] == []
    assert [g["id"] for g in env.client.get("/evaluacion/revision?estado=aceptado", headers=ana).json()["grupos"]] == [
        candidato_id
    ]
    assert [c["id"] for c in env.client.get("/evaluacion/revision/aceptados", headers=ana).json()["casos"]] == [
        aceptado["id"]
    ]
    assert env.client.get("/evaluacion/revision/aceptados", headers=luis).json()["casos"] == []
    assert env.client.delete(f"/evaluacion/revision/aceptados/{aceptado['id']}", headers=luis).status_code == 404


def test_the_operator_sees_everyones_queue_and_rejects_with_a_reason(env: Env) -> None:
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    pregunta = "¿Cuánto pago entre la hipoteca y la luz este mes?"
    env.negativa(ana, env.caso_con_turno(ana, pregunta), pregunta=pregunta)
    env.negativa(luis, env.caso_con_turno(luis, pregunta.upper()), pregunta=pregunta.upper())
    operador = {"X-Operator-Key": OPERADOR}
    cola = env.client.get("/evaluacion/revision", headers=operador).json()
    # Same masked question, same failure: one group, seen twice.
    (grupo,) = cola["grupos"]
    assert cola["operador"] and grupo["frecuencia"] == 2
    assert env.client.get("/evaluacion/revision", headers={"X-Operator-Key": "otra"}).status_code == 401
    sin_motivo = env.client.post(f"/evaluacion/revision/{grupo['id']}/rechazar", json={"motivo": ""}, headers=operador)
    assert sin_motivo.status_code == 422
    r = env.client.post(
        f"/evaluacion/revision/{grupo['id']}/rechazar", json={"motivo": "La cifra era correcta"}, headers=operador
    )
    assert r.json() == {"rechazados": 2}
    assert env.client.get("/evaluacion/revision", headers=ana).json()["grupos"] == []


def test_the_standing_consent_turns_votes_and_weak_signals_into_candidates(env: Env) -> None:
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    case_id = env.caso_con_turno(ana)
    estado = env.client.get(f"/evaluacion/aprendizaje?case_id={case_id}", headers=ana).json()
    assert estado["activo"] is False and "Aprender de los fallos" in estado["texto"]
    assert env.client.get(f"/evaluacion/aprendizaje?case_id={case_id}", headers=luis).status_code == 404
    sin_version = env.client.put("/evaluacion/aprendizaje", json={"case_id": case_id, "activo": True}, headers=ana)
    assert sin_version.status_code == 422
    r = env.client.put(
        "/evaluacion/aprendizaje", json={"case_id": case_id, "activo": True, "version": estado["version"]}, headers=ana
    )
    assert r.json()["activo"] is True
    assert (
        env.client.put(
            "/evaluacion/aprendizaje",
            json={"case_id": case_id, "activo": True, "version": estado["version"]},
            headers=luis,
        ).status_code
        == 404
    )
    # A turn after the consent with the figures gate unhappy, and a plain 👎 on it.
    env.cases.append_audit_event(
        case_id,
        {
            "event_type": "agent_answer",
            "input": "¿Y en total este mes?",
            "mode": "partially_verified",
            "cerebro": informe_cerebro(),
        },
    )
    voto = env.negativa(ana, case_id, guardar=False)
    assert voto["candidato"] is True and voto["guardado_para_evaluacion"] is False
    cola = env.client.get("/evaluacion/revision", headers=ana).json()
    origenes = sorted(g["origen"] for g in cola["grupos"])
    assert "voto" in origenes and "senal" in origenes
    # Withdrawing it deletes the pending candidates it produced; the audit trail says so.
    r = env.client.put("/evaluacion/aprendizaje", json={"case_id": case_id, "activo": False}, headers=ana)
    assert r.json()["activo"] is False and r.json()["candidatos_pendientes"] == 0
    assert any(e["event_type"] == "evaluacion_aprendizaje" for e in env.cases.list_audit_events(case_id))


def test_withdrawing_a_feedback_removes_its_candidate_and_accepted_case(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.caso_con_turno(ana)
    feedback_id = env.negativa(ana, case_id)["id"]
    (grupo,) = env.client.get("/evaluacion/revision", headers=ana).json()["grupos"]
    assert env.client.post(f"/evaluacion/revision/{grupo['id']}/aceptar", json=EDICION, headers=ana).status_code == 200
    assert privado.store_de(env.db_path).todos()
    assert env.client.delete(f"/evaluacion/feedback/{feedback_id}", headers=ana).status_code == 204
    assert revision.candidatos_para(env.db_path).todos() == [] and privado.store_de(env.db_path).todos() == []


def test_export_and_account_erasure_include_the_learning_loop(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = env.caso_con_turno(ana)
    env.negativa(ana, case_id)
    (grupo,) = env.client.get("/evaluacion/revision", headers=ana).json()["grupos"]
    aceptado = env.client.post(f"/evaluacion/revision/{grupo['id']}/aceptar", json=EDICION, headers=ana).json()
    export = env.client.get("/me/export", headers=ana).json()
    bloque = export["evaluacion"]
    assert [c["id"] for c in bloque["candidatos"]] == [grupo["id"]]
    assert [c["id"] for c in bloque["casos_aceptados"]] == [aceptado["id"]]
    # The private file is a separate database: erasing the account reaches it too.
    ruta = privado.ruta_por_defecto(env.db_path)
    assert ruta.exists() and ruta.parent == privado.ruta_por_defecto(env.db_path).parent
    assert env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    assert privado.store_de(env.db_path).todos() == []
    assert revision.candidatos_para(env.db_path).todos() == []

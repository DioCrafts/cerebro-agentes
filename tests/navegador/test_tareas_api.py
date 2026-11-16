"""Browser tasks through the API (accounts on): kill switch, consent, rate limits, results, privacy.

Real headless Chromium against the local mock sites; the luz module receives the CSV through the module
tool path; a procedure ends as a pending action that submits nothing when confirmed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from hacienda_gpt import settings
from hacienda_gpt.api.api import app, get_case_store, get_notification_store
from hacienda_gpt.api.routers import navegador as navegador_router
from hacienda_gpt.api.routers.auth import get_login_limiter
from hacienda_gpt.auth.deps import get_auth_store
from hacienda_gpt.auth.ratelimit import LoginRateLimiter
from hacienda_gpt.auth.store import AuthStore
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.modulos import registro as registro_mod
from hacienda_gpt.navegador import tareas, vigilancia
from hacienda_gpt.navegador.guion import guion_activo
from hacienda_gpt.navegador.tareas import TERMINADOS, TEXTO_CONSENTIMIENTO_VERSION, GestorTareas
from hacienda_gpt.ops import formatos
from hacienda_gpt.proactive.store import NotificationStore
from tests.navegador.conftest import login_clave, login_datadis, sesion_de_usuario
from tests.navegador.simulacion import USUARIO_DATADIS, Sitios
from tests.navegador.test_ejecutor import PARAMS_DATADIS, PARAMS_SEDE

PASSWORD = "una-clave-larga-1"
CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}
ACUSE = {"acepto": True, "texto_version": TEXTO_CONSENTIMIENTO_VERSION}


@dataclass
class Env:
    client: TestClient
    sitios: Sitios
    cases: SQLiteCaseStateStore
    gestor: GestorTareas
    chromium: str | None

    def register(self, email: str, *, modulos: tuple[str, ...] = ("navegador", "luz")) -> dict[str, str]:
        r = self.client.post("/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS})
        assert r.status_code == 201, r.text
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        for clave in modulos:
            assert self.client.put(f"/me/modulos/{clave}", json={"activo": True}, headers=headers).status_code == 200
        return headers

    def new_case(self, headers: dict[str, str]) -> str:
        r = self.client.post("/cases", json={"user_id": "x", "tax_period": "2026"}, headers=headers)
        assert r.status_code == 200, r.text
        return str(r.json()["case_id"])

    def esperar(self, tarea_id: str, headers: dict[str, str], timeout: float = 90) -> dict[str, Any]:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            r = self.client.get(f"/navegador/tareas/{tarea_id}", headers=headers)
            assert r.status_code == 200, r.text
            if r.json()["estado"] in TERMINADOS:
                tarea = self.gestor.get(tarea_id)
                if tarea is not None:
                    self.gestor.esperar(tarea, 30)
                return dict(self.client.get(f"/navegador/tareas/{tarea_id}", headers=headers).json())
            time.sleep(0.3)
        raise AssertionError("la tarea no terminó a tiempo")


@pytest.fixture
def env(tmp_path, monkeypatch, entorno: Sitios, chromium: str | None) -> Iterator[Env]:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    db_path = str(tmp_path / "state.sqlite3")
    auth, cases, notifications = AuthStore(db_path), SQLiteCaseStateStore(db_path), NotificationStore(db_path)
    gestor = GestorTareas(
        opciones_ejecutor={
            "headless": True,
            "ejecutable": chromium,
            "pausa_ms": 0,
            "espera_login_s": 15,
            "timeout_paso_ms": 4_000,
            "entrega_max_s": 0,
        }
    )
    monkeypatch.setattr(tareas, "_gestor", [gestor])
    registro_mod.reiniciar()
    overrides = {
        get_auth_store: lambda: auth,
        get_case_store: lambda: cases,
        get_notification_store: lambda: notifications,
        get_login_limiter: lambda: LoginRateLimiter(50, 900),
        navegador_router._reparador_por_defecto: lambda: None,
    }
    app.dependency_overrides.update(overrides)
    try:
        yield Env(client=TestClient(app), sitios=entorno, cases=cases, gestor=gestor, chromium=chromium)
    finally:
        for tarea in list(gestor._tareas.values()):
            gestor.cerrar(tarea)
            gestor.esperar(tarea, 30)
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
        registro_mod.reiniciar()


def _crear(env: Env, headers: dict[str, str], case_id: str, **extra: Any) -> Any:
    cuerpo = {"case_id": case_id, "guion": "datadis_consumo", "parametros": PARAMS_DATADIS, **extra}
    return env.client.post("/navegador/tareas", json=cuerpo, headers=headers)


def _eventos_navegador(env: Env, case_id: str) -> list[dict[str, Any]]:
    return [
        e
        for e in env.cases.list_audit_events(case_id)
        if str(e.get("event_type", "")).startswith(("navegador_", "module_tool_run_by_navegador"))
    ]


def test_the_kill_switch_answers_503_and_hides_the_tasks(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = env.register("ana@example.com")
    case_id = env.new_case(headers)
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", False)
    estado = env.client.get("/navegador/estado", headers=headers).json()
    assert estado["activo"] is False and estado["tareas"] == [] and estado["modos"] == []
    assert _crear(env, headers, case_id, iniciar=True, modo="ventana", **ACUSE).status_code == 503
    assert env.client.get(f"/navegador/tareas?case_id={case_id}", headers=headers).status_code == 503
    r = env.client.post(
        "/modulos/navegador/herramientas/navegador_descargar_consumo_datadis",
        json={"case_id": case_id, "argumentos": {}},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["resultado"]["es_error"] is True
    assert "desactivado" in r.json()["resultado"]["contenido"]


def test_starting_needs_consent_the_current_text_and_the_luz_module(env: Env) -> None:
    sin_modulo = env.register("sin@example.com", modulos=("luz",))
    case_id = env.new_case(sin_modulo)
    estado = env.client.get("/navegador/estado", headers=sin_modulo).json()
    assert estado["activo"] is True and estado["consentimiento"] is False
    assert {t["clave"] for t in estado["tareas"]} == {"datadis_consumo", "sede_ejemplo"}
    r = _crear(env, sin_modulo, case_id, iniciar=True, modo="ventana", **ACUSE)
    assert r.status_code == 403 and "consentimiento" in r.json()["detail"]

    con = env.register("con@example.com", modulos=("navegador",))
    case_id = env.new_case(con)
    assert _crear(env, con, case_id, iniciar=True, modo="ventana").status_code == 422  # no acknowledgement
    r = _crear(env, con, case_id, iniciar=True, modo="ventana", acepto=True, texto_version="antiguo")
    assert r.status_code == 422
    r = _crear(env, con, case_id, iniciar=True, modo="ventana", **ACUSE)
    assert r.status_code == 409 and "Luz" in r.json()["detail"]  # nowhere to import the CSV
    assert env.sitios.registro.peticiones == []  # no browser ever opened


def test_datadis_with_a_handed_over_session_ends_in_the_luz_module(env: Env) -> None:
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    case_id = env.new_case(ana)
    sesion = sesion_de_usuario(env.chromium, env.sitios)
    cookie = next(c["value"] for c in sesion["cookies"] if c["name"] == "sesion_datadis")

    r = _crear(env, ana, case_id, iniciar=True, modo="sesion", sesion=sesion, **ACUSE)
    assert r.status_code == 201, r.text
    assert r.json()["parametros"]["cups"].endswith("01AA") and "ES0021" not in r.json()["parametros"]["cups"]
    tarea_id = r.json()["id"]
    assert env.client.get(f"/navegador/tareas/{tarea_id}", headers=luis).status_code == 404
    tarea = env.esperar(tarea_id, ana)

    assert tarea["estado"] == "importado", tarea
    assert "Leídas 72 horas" in tarea["importacion"]
    assert [p["estado"] for p in tarea["pasos"]] == ["ok"] * 8
    assert tarea["origenes_bloqueados"] == [env.sitios.origenes["tracker"]]
    consumo = next(f for f in env.cases.get_case(case_id).facts if f.name == "luz_consumo")  # type: ignore[union-attr]
    valor = json.loads(consumo.value) if isinstance(consumo.value, str) else consumo.value
    assert sorted(valor["dias"]) == ["2026-09-01", "2026-09-02", "2026-09-03"]

    captura = env.client.get(f"/navegador/tareas/{tarea_id}/captura", headers=ana)
    assert captura.status_code == 200 and captura.headers["cache-control"] == "no-store"
    assert env.client.get(f"/navegador/tareas/{tarea_id}/captura", headers=luis).status_code == 404

    eventos = _eventos_navegador(env, case_id)
    assert [e["event_type"] for e in eventos] == [
        "navegador_tarea_iniciada",
        "module_tool_run_by_navegador",
        "navegador_tarea_terminada",
    ]
    assert eventos[0]["parametros_recibidos"] == ["cups", "desde", "hasta"]
    assert eventos[1]["argumentos_recibidos"] == ["csv"]
    volcado = json.dumps(eventos, ensure_ascii=False)
    for valor in ("ES0021000000000001AA", "2026-09-01", cookie, USUARIO_DATADIS[1], "Consumo_kWh"):
        assert valor not in volcado  # steps are audited without values

    exportado = env.client.get("/me/export", headers=ana).json()
    tipos = {e["event_type"] for c in exportado["cases"] for e in c["audit_events"]}
    assert {"navegador_tarea_iniciada", "navegador_tarea_terminada"} <= tipos
    assert any(c["modulo"] == "navegador" and c["activo"] for c in exportado["module_choices"])

    # Closing discards the screenshot at once.
    env.client.post(f"/navegador/tareas/{tarea_id}/cerrar", headers=ana)
    assert env.client.get(f"/navegador/tareas/{tarea_id}/captura", headers=ana).status_code == 404


def test_the_chat_tool_prepares_and_the_user_starts_with_their_own_login(env: Env) -> None:
    env.gestor.opciones_ejecutor["usuario_simulado"] = login_datadis
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    r = env.client.post(
        "/modulos/navegador/herramientas/navegador_descargar_consumo_datadis",
        json={
            "case_id": case_id,
            "argumentos": {"desde": "2026-09-01", "hasta": "2026-09-02", "cups": PARAMS_DATADIS["cups"]},
        },
        headers=ana,
    )
    assert r.status_code == 200, r.text
    resultado = r.json()["resultado"]
    assert not resultado["es_error"]
    assert "iniciar sesión tú" in resultado["contenido"] and "ES0021000000000001AA" not in resultado["contenido"]
    assert env.sitios.registro.peticiones == []  # preparing never opens a browser

    lista = env.client.get(f"/navegador/tareas?case_id={case_id}", headers=ana).json()["tareas"]
    assert [t["estado"] for t in lista] == ["preparada"]
    r = env.client.post(f"/navegador/tareas/{lista[0]['id']}/iniciar", json={"modo": "ventana", **ACUSE}, headers=ana)
    assert r.status_code == 200, r.text
    tarea = env.esperar(lista[0]["id"], ana)
    assert tarea["estado"] == "importado", tarea
    assert "Inicia sesión tú" in " ".join(e["texto"] for e in tarea["eventos"])
    r = env.client.post(f"/navegador/tareas/{lista[0]['id']}/iniciar", json={"modo": "ventana", **ACUSE}, headers=ana)
    assert r.status_code == 409  # a task runs once


def test_a_procedure_becomes_a_pending_action_that_submits_nothing(env: Env) -> None:
    env.gestor.opciones_ejecutor["usuario_simulado"] = login_clave
    ana = env.register("ana@example.com", modulos=("navegador",))
    case_id = env.new_case(ana)
    r = env.client.post(
        "/navegador/tareas",
        json={
            "case_id": case_id,
            "guion": "sede_ejemplo",
            "parametros": PARAMS_SEDE,
            "iniciar": True,
            "modo": "ventana",
            **ACUSE,
        },
        headers=ana,
    )
    assert r.status_code == 201, r.text
    tarea = env.esperar(r.json()["id"], ana)

    assert tarea["estado"] == "detenido_antes_de_enviar", tarea
    assert [f["etiqueta"] for f in tarea["resumen_envio"]] == [
        "Nombre y apellidos",
        "NIF",
        "Correo electrónico",
        "Motivo",
        "Expone",
    ]
    assert tarea["tiene_captura"]
    acciones = env.client.get(f"/acciones?case_id={case_id}", headers=ana).json()["acciones"]
    assert len(acciones) == 1 and acciones[0]["id"] == tarea["accion_id"]
    accion = acciones[0]
    assert accion["estado"] == "pendiente" and accion["herramienta"] == "navegador_presentar_tramite"
    assert "NO firma" in accion["resumen"] and "NIF" in accion["resumen"]
    for valor in ("12345678Z", "María", "maria@example.org"):
        assert valor not in json.dumps(accion, ensure_ascii=False)  # the pending action keeps labels only

    confirmada = env.client.post(f"/acciones/{accion['id']}/confirmar", headers=ana)
    assert confirmada.status_code == 200
    assert confirmada.json()["estado"] == "fallida" and "No implementado" in confirmada.json()["resultado"]
    assert env.sitios.registro.envios_sede == []  # nothing was ever submitted
    volcado = json.dumps(_eventos_navegador(env, case_id), ensure_ascii=False)
    assert "12345678Z" not in volcado and "María" not in volcado


def test_rate_limits_one_running_task_per_user_and_a_daily_budget(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    # Nobody logs in: the first task waits for the login.
    primera = _crear(env, ana, case_id, iniciar=True, modo="ventana", **ACUSE)
    assert primera.status_code == 201
    segunda = _crear(env, ana, case_id, iniciar=True, modo="ventana", **ACUSE)
    assert segunda.status_code == 429 and "en curso" in segunda.json()["detail"]
    env.client.post(f"/navegador/tareas/{primera.json()['id']}/detener", headers=ana)
    assert env.esperar(primera.json()["id"], ana)["estado"] == "cancelada"

    monkeypatch.setattr(settings, "NAVEGADOR_MAX_TAREAS_DIA", 1)
    tercera = _crear(env, ana, case_id, iniciar=True, modo="ventana", **ACUSE)
    assert tercera.status_code == 429 and "24 horas" in tercera.json()["detail"]
    assert env.client.get("/navegador/estado", headers=ana).json()["usadas_hoy"] == 1
    monkeypatch.setattr(settings, "NAVEGADOR_MODOS", "sesion")
    monkeypatch.setattr(settings, "NAVEGADOR_MAX_TAREAS_DIA", 5)
    assert _crear(env, ana, case_id, iniciar=True, modo="ventana", **ACUSE).status_code == 422


def test_a_broken_page_is_reported_diagnosed_redacted_and_erased_with_the_account(env: Env) -> None:
    env.gestor.opciones_ejecutor["usuario_simulado"] = login_datadis
    env.sitios.registro.variante = "selector_cambiado"
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    case_id = env.new_case(ana)
    tarea = env.esperar(_crear(env, ana, case_id, iniciar=True, modo="ventana", **ACUSE).json()["id"], ana)
    assert tarea["estado"] == "fallida" and "No se ha enviado nada" in tarea["mensaje"]

    estado = {e.fuente: e for e in formatos.resumen()}["nav_datadis_consumo"]
    assert estado.estado == "rota" and estado.fallos[0].canario == "paso_suministro"
    assert any("nav_datadis_consumo" in p for p in formatos.problemas())

    diagnosticos = vigilancia.diagnosticos_de(case_id)
    assert len(diagnosticos) == 1 and diagnosticos[0]["paso"] == "suministro"
    volcado = json.dumps(diagnosticos, ensure_ascii=False)
    assert "María" not in volcado and "ES0021000000000001AA" not in volcado and "Suministro (CUPS)" in volcado

    assert env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    case_luis = env.new_case(luis)
    env.client.get(f"/navegador/tareas?case_id={case_luis}", headers=luis)  # any task call purges orphans
    assert vigilancia.diagnosticos_de(case_id) == []


def test_a_repaired_selector_becomes_a_reviewable_proposal(env: Env) -> None:
    class Reparador:
        def proponer(self, paso: Any, esqueleto: list[dict[str, Any]]) -> str | None:
            return "#suministro" if paso.id == "suministro" else None

    app.dependency_overrides[navegador_router._reparador_por_defecto] = lambda: Reparador()
    env.gestor.opciones_ejecutor["usuario_simulado"] = login_datadis
    env.sitios.registro.variante = "selector_cambiado"
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    tarea = env.esperar(_crear(env, ana, case_id, iniciar=True, modo="ventana", **ACUSE).json()["id"], ana)
    assert tarea["estado"] == "importado", tarea

    propuestas = vigilancia.propuestas_de(["datadis_consumo"])
    assert len(propuestas) == 1 and propuestas[0].estado == "propuesta" and propuestas[0].ensayo.ok  # type: ignore[union-attr]
    estado = {e.fuente: e for e in formatos.resumen()}["nav_datadis_consumo"]
    assert estado.fallos == [] or all(f.gravedad == "leve" for f in estado.fallos)
    assert guion_activo("datadis_consumo").version == 1  # nothing changes until a person applies it

    aplicada = vigilancia.aplicar_reparacion(propuestas[0].id, por="revisor")
    assert aplicada.estado == "aplicada"
    activo = guion_activo("datadis_consumo")
    assert activo.version == 2 and activo.reglas.pasos[2].selector == "#suministro"  # type: ignore[union-attr]
    with pytest.raises(formatos.FormatoError):
        vigilancia.aplicar_reparacion(propuestas[0].id)


def test_consent_without_accounts_is_the_operators_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "fiscal")
    assert tareas.consentimiento(None, None) is False
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "fiscal,navegador")
    assert tareas.consentimiento(None, None) is True
    monkeypatch.setattr(settings, "MODULOS_DESACTIVADOS", "navegador")
    assert tareas.consentimiento(None, None) is False
    # With accounts, a default is not consent: only the user's own choice counts.
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "MODULOS_DESACTIVADOS", "")
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "todos")

    class Auth:
        def module_choices(self, user_id: str) -> dict[str, Any]:
            return {}

    assert tareas.consentimiento("u1", Auth()) is False

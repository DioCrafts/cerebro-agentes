"""The tasks over HTTP with accounts on: ownership, the action's own button, export and erasure with the account."""

from __future__ import annotations

from collections.abc import Iterator
import sqlite3
from typing import Any

import pytest

from hacienda_gpt import settings
from hacienda_gpt.agent.providers.base import ProviderError
from hacienda_gpt.api.api import app
from hacienda_gpt.api.routers.tareas import proveedor_planificador
from hacienda_gpt.modulos import registro as registro_mod
from tests.auth.conftest import PASSWORD, Env, env, fast_scrypt  # noqa: F401
from .conftest import Entorno

PLAN = [
    {"id": "leer", "titulo": "Lee el contador", "tipo": "calculo", "herramienta": "agua_lectura"},
    {
        "id": "cambiar",
        "titulo": "Cambia la tarifa",
        "tipo": "accion",
        "herramienta": "agua_cambiar_tarifa",
        "argumentos": {"importe": 10},
        "depende_de": ["leer"],
    },
    {"id": "fin", "titulo": "Comprueba la factura", "tipo": "confirmacion_usuario", "depende_de": ["cambiar"]},
]


@pytest.fixture
def api(env: Env, entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:  # noqa: F811
    """Accounts on, over the API's database; the fake modules of ``entorno`` are the registry."""
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", env.db_path)
    yield env
    app.dependency_overrides.pop(proveedor_planificador, None)


def _usuario(api: Env, email: str) -> tuple[dict, str]:
    cabeceras = api.register(email)
    case_id = api.new_case(cabeceras)
    respuesta = api.client.put("/me/modulos/agua", json={"activo": True}, headers=cabeceras)
    assert respuesta.status_code == 200, respuesta.text
    return cabeceras, case_id


def _filas(api: Env, tabla: str, case_id: str) -> int:
    with sqlite3.connect(api.db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE case_id = ?", (case_id,)).fetchone()[0])


def test_a_plan_runs_only_after_acceptance_and_the_action_only_with_its_button(api: Env, entorno: Entorno) -> None:
    ana, case_id = _usuario(api, "ana@example.com")
    plantillas = api.client.get("/tareas/plantillas", headers=ana).json()["plantillas"]
    assert {"renovar_pasaporte", "preparar_renta", "cambiar_tarifa_luz"} <= {p["id"] for p in plantillas}

    creada = api.client.post(
        "/tareas/plan",
        json={"case_id": case_id, "titulo": "Tarifa del agua", "objetivo": "pagar menos de agua", "pasos": PLAN},
        headers=ana,
    )
    assert creada.status_code == 200, creada.text
    tarea = creada.json()["tarea"]
    assert tarea["estado"] == "propuesta" and entorno.llamadas.registro == []
    tarea_id = tarea["id"]

    aceptada = api.client.post(f"/tareas/{tarea_id}/aceptar", headers=ana).json()
    assert aceptada["tarea"]["estado"] == "esperando_usuario"
    accion_id = aceptada["resumen"]["esperando"][0]["accion_id"]
    assert "agua_cambiar_tarifa" not in entorno.llamadas.nombres()
    assert api.client.post(f"/tareas/{tarea_id}/pasos/cambiar/confirmar", json={}, headers=ana).status_code == 409

    # The pending action's own button (modulos router) runs it once; the task then goes on.
    assert api.client.post(f"/acciones/{accion_id}/confirmar", headers=ana).json()["estado"] == "ejecutada"
    assert entorno.llamadas.nombres().count("agua_cambiar_tarifa") == 1
    detalle = api.client.get(f"/tareas/{tarea_id}", headers=ana).json()
    pasos = {p["id"]: p["estado"] for p in detalle["tarea"]["pasos"]}
    assert pasos == {"leer": "hecho", "cambiar": "hecho", "fin": "esperando"}
    final = api.client.post(f"/tareas/{tarea_id}/pasos/fin/confirmar", json={"nota": "todo bien"}, headers=ana).json()
    assert final["tarea"]["estado"] == "completada"
    assert [e["evento"] for e in final["historial"]][:2] == ["propuesta", "aceptada"]
    listado = api.client.get(f"/tareas?case_id={case_id}", headers=ana).json()["tareas"]
    assert [(t["id"], t["estado"]) for t in listado] == [(tarea_id, "completada")]


def test_another_users_task_reads_as_missing(api: Env) -> None:
    ana, case_ana = _usuario(api, "ana@example.com")
    bob, case_bob = _usuario(api, "bob@example.com")
    tarea_id = api.client.post(
        "/tareas",
        json={"case_id": case_ana, "plantilla": "renovar_pasaporte", "objetivo": "renovar el pasaporte"},
        headers=ana,
    ).json()["tarea"]["id"]
    for metodo, url in [
        ("get", f"/tareas/{tarea_id}"),
        ("post", f"/tareas/{tarea_id}/aceptar"),
        ("post", f"/tareas/{tarea_id}/cancelar"),
        ("post", f"/tareas/{tarea_id}/pasos/cita/confirmar"),
        ("get", f"/tareas?case_id={case_ana}"),
    ]:
        kwargs: dict[str, Any] = {"headers": bob}
        if metodo == "post":
            kwargs["json"] = {}
        assert getattr(api.client, metodo)(url, **kwargs).status_code == 404, url
    assert (
        api.client.post(
            "/tareas", json={"case_id": case_ana, "plantilla": "renovar_pasaporte", "objetivo": "x"}, headers=bob
        ).status_code
        == 404
    )
    assert api.client.get(f"/tareas?case_id={case_bob}", headers=bob).json()["tareas"] == []
    assert api.client.get("/tareas/plantillas").status_code == 401


def test_bad_plans_and_templates_are_a_readable_422(api: Env) -> None:
    ana, case_id = _usuario(api, "ana@example.com")
    inyeccion = [{**PLAN[0], "estado": "hecho"}, {**PLAN[1], "tipo": "calculo"}]
    respuesta = api.client.post(
        "/tareas/plan", json={"case_id": case_id, "titulo": "Agua", "objetivo": "agua", "pasos": inyeccion}, headers=ana
    )
    assert respuesta.status_code == 422 and "El plan no es válido" in respuesta.json()["detail"]
    respuesta = api.client.post(
        "/tareas",
        json={
            "case_id": case_id,
            "plantilla": "preparar_renta",
            "objetivo": "la renta",
            "parametros": {"ejercicio": 1999},
        },
        headers=ana,
    )
    assert respuesta.status_code == 422 and "ejercicio" in respuesta.json()["detail"]
    assert (
        api.client.post(
            "/tareas", json={"case_id": case_id, "plantilla": "no_existe", "objetivo": "x"}, headers=ana
        ).status_code
        == 422
    )


def test_start_pause_resume_cancel_with_a_reason_from_the_buttons(api: Env) -> None:
    ana, case_id = _usuario(api, "ana@example.com")
    creada = api.client.post(
        "/tareas",
        json={
            "case_id": case_id,
            "plantilla": "renovar_pasaporte",
            "objetivo": "renovar el pasaporte",
            "aceptar": True,
        },
        headers=ana,
    ).json()
    tarea_id = creada["tarea"]["id"]
    assert creada["tarea"]["estado"] == "esperando_usuario"
    assert api.client.post(f"/tareas/{tarea_id}/pausar", headers=ana).json()["tarea"]["estado"] == "pausada"
    assert api.client.post(f"/tareas/{tarea_id}/pausar", headers=ana).status_code == 409
    assert api.client.post(f"/tareas/{tarea_id}/reanudar", headers=ana).json()["tarea"]["estado"] == "esperando_usuario"
    cancelada = api.client.post(f"/tareas/{tarea_id}/cancelar", json={"motivo": "Ya lo renové"}, headers=ana).json()
    assert cancelada["tarea"]["estado"] == "cancelada" and cancelada["tarea"]["vinculos"]["decisiones"]
    decisiones = api.client.get(f"/cases/{case_id}/decisiones", headers=ana).json()["decisiones"]
    assert [(d["ambito"], d["fuente"]) for d in decisiones] == [("tareas:renovar_pasaporte", "boton")]


class _SinModelo:
    name = "caido"
    model = "caido-1"

    def complete_json(self, **_kw: Any) -> dict[str, Any]:
        raise ProviderError("sin conexión", retryable=True)


class _Modelo:
    name = "falso"
    model = "falso-1"

    def complete_json(self, **_kw: Any) -> dict[str, Any]:
        return {"titulo": "Tarifa del agua", "pasos": PLAN}


def test_a_model_proposes_a_plan_that_waits_for_acceptance(api: Env) -> None:
    ana, case_id = _usuario(api, "ana@example.com")
    app.dependency_overrides[proveedor_planificador] = _SinModelo
    sin = api.client.post("/tareas/proponer", json={"case_id": case_id, "objetivo": "pagar menos agua"}, headers=ana)
    assert sin.status_code == 503
    app.dependency_overrides[proveedor_planificador] = _Modelo
    respuesta = api.client.post(
        "/tareas/proponer", json={"case_id": case_id, "objetivo": "pagar menos agua"}, headers=ana
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["tarea"]["estado"] == "propuesta" and respuesta.json()["tarea"]["origen"] == "plan"


def test_tasks_are_exported_and_erased_with_the_account(api: Env) -> None:
    ana, case_id = _usuario(api, "ana@example.com")
    tarea_id = api.client.post(
        "/tareas/plan",
        json={"case_id": case_id, "titulo": "Tarifa del agua", "objetivo": "pagar menos de agua", "pasos": PLAN},
        headers=ana,
    ).json()["tarea"]["id"]
    api.client.post(f"/tareas/{tarea_id}/aceptar", headers=ana)

    exportado = api.client.get("/me/export", headers=ana).json()
    (caso,) = exportado["cases"]
    assert [t["id"] for t in caso["tareas"]] == [tarea_id]
    assert caso["tareas"][0]["objetivo"] == "pagar menos de agua" and caso["tareas"][0]["historial"]
    assert any(n["kind"] == "modulo:tareas" for n in caso["notifications"])

    for tabla in ("tareas", "tareas_historial"):
        assert _filas(api, tabla, case_id) > 0
    borrado = api.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana)
    assert borrado.status_code == 204
    for tabla in ("tareas", "tareas_historial", "tareas_senales", "acciones_pendientes", "notifications"):
        assert _filas(api, tabla, case_id) == 0, tabla
    with sqlite3.connect(api.db_path) as conn:
        assert "pagar menos de agua" not in str(conn.execute("SELECT * FROM tareas").fetchall())


def test_the_registry_is_the_fake_one(api: Env) -> None:
    assert "agua" in registro_mod.registro().modulos

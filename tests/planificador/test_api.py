"""POST /planificar: the planner over HTTP with the accounts on — the owner's case only, modules by consent,
nothing stored, readable errors."""

from __future__ import annotations

import json

import pytest

from tests.auth.conftest import Env
from .test_planificador import FAMILIA, HIPOTECA_FIJA

PETICION = {
    "ejercicio": 2026,
    "fecha": "2026-11-16",
    "importe_disponible": 20000,
    "horizonte_anos": 5,
    "rentabilidad_alternativa_pct": 2,
    "gastos_mensuales": 2000,
    "liquidez_actual": 6000,
}


def _caso_con_hipoteca(env: Env, headers: dict) -> str:
    case_id = env.new_case(headers)
    assert env.client.put("/me/modulos/hipoteca", json={"activo": True}, headers=headers).status_code == 200
    for nombre, valor in FAMILIA.items():
        valor = json.dumps(HIPOTECA_FIJA) if nombre == "hipoteca_prestamo" else valor
        respuesta = env.client.put(f"/cases/{case_id}/facts/{nombre}", json={"value": valor}, headers=headers)
        assert respuesta.status_code == 200, respuesta.text
    return case_id


def test_the_owner_gets_the_plan_from_the_case_and_nothing_is_stored(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = _caso_con_hipoteca(env, ana)
    antes = env.cases.get_case(case_id)
    eventos = len(env.cases.list_audit_events(case_id))
    respuesta = env.client.post("/planificar", json={**PETICION, "case_id": case_id}, headers=ana)
    assert respuesta.status_code == 200, respuesta.text
    plan = respuesta.json()
    assert plan["valoracion"]["asignacion"]["amortizar_hipoteca"] not in ("0", "0.00")
    assert plan["busqueda"]["metodo"] == "exhaustiva"
    despues = env.cases.get_case(case_id)
    assert despues is not None and antes is not None
    assert despues.facts == antes.facts and despues.version == antes.version
    assert len(env.cases.list_audit_events(case_id)) == eventos


def test_someone_elses_case_is_not_found(env: Env) -> None:
    ana = env.register("ana@example.com")
    luis = env.register("luis@example.com")
    case_id = _caso_con_hipoteca(env, ana)
    assert env.client.post("/planificar", json={**PETICION, "case_id": case_id}, headers=luis).status_code == 404
    assert env.client.post("/planificar", json=PETICION).status_code == 401


def test_with_the_module_switched_off_the_loan_is_not_used(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = _caso_con_hipoteca(env, ana)
    assert env.client.put("/me/modulos/hipoteca", json={"activo": False}, headers=ana).status_code == 200
    plan = env.client.post("/planificar", json={**PETICION, "case_id": case_id}, headers=ana).json()
    causas = {n["id"]: n["causa"] for n in plan["no_usadas"]}
    assert causas.get("amortizar_hipoteca") in ("modulo_inactivo", "faltan_datos")
    assert plan["valoracion"]["asignacion"]["amortizar_hipoteca"] in ("0", "0.00")


@pytest.mark.parametrize(
    ("cuerpo", "texto"),
    [
        ({**PETICION, "horizonte_anos": 0}, "horizonte_anos"),
        ({**PETICION, "desconocido": 1}, "campo desconocido"),
        ({**PETICION, "valorar_pensiones": "definitivo"}, "tipo_rescate_pct"),
    ],
)
def test_bad_input_is_a_spanish_422(env: Env, cuerpo: dict, texto: str) -> None:
    ana = env.register("ana@example.com")
    respuesta = env.client.post("/planificar", json=cuerpo, headers=ana)
    assert respuesta.status_code == 422
    assert isinstance(respuesta.json()["detail"], str) and texto in respuesta.json()["detail"]

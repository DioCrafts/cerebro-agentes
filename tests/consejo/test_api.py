"""``/cases/{case_id}/consejo``: convene (estimate, nothing runs), confirm once, read the ruling with authorship revealed
only at the end, reject / cancel / delete, the detector; somebody else's case or session is a 404; the account export
carries the sessions and the account erasure removes them."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.consejo.servicio import ServicioConsejo, configurar_servicio
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma.api.routers.consejo import build_router
from tests.auth.conftest import CONSENTS, PASSWORD, Env, env, fast_scrypt  # noqa: F401
from .fakes import HOY, FabricaFalsa, MotorFalso

PREGUNTA = "¿Me conviene amortizar la hipoteca o invertir en un fondo indexado?"


@pytest.fixture
def servicio(db: str) -> Iterator[ServicioConsejo]:
    servicio = ServicioConsejo(db, fabrica=FabricaFalsa(), en_segundo_plano=False, hoy=lambda: HOY)
    configurar_servicio(db, servicio)
    yield servicio
    configurar_servicio(db, None)


@pytest.fixture
def cliente(db: str, servicio: ServicioConsejo) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(get_case_store=lambda: SQLiteCaseStateStore(db)))
    return TestClient(app)


def test_convene_confirm_run_and_read_the_ruling(cliente: TestClient, servicio: ServicioConsejo) -> None:
    creada = cliente.post("/cases/c1/consejo", json={"pregunta": PREGUNTA, "preferencia": "Me inclino por amortizar."})
    assert creada.status_code == 200, creada.text
    sesion = creada.json()
    assert sesion["estado"] == "pendiente_confirmacion" and sesion["origen"] == "usuario"
    assert float(sesion["estimacion_usd"]) > 0 and float(sesion["tope_usd"]) >= float(sesion["estimacion_usd"])
    assert sesion["minutos_estimados"] >= 1 and sesion["dictamen"] is None and not sesion["autoria_revelada"]
    lista = cliente.get("/cases/c1/consejo").json()
    assert lista["disponible"] and [s["id"] for s in lista["sesiones"]] == [sesion["id"]]

    primera = cliente.post(f"/cases/c1/consejo/{sesion['id']}/confirmar", json={}).json()
    assert primera["estado"] == "en_cola" and primera["confirmada_ahora"] is True
    segunda = cliente.post(f"/cases/c1/consejo/{sesion['id']}/confirmar", json={}).json()
    assert segunda["confirmada_ahora"] is False

    servicio.ejecutar_pendientes()
    final = cliente.get(f"/cases/c1/consejo/{sesion['id']}").json()
    assert final["estado"] == "terminada" and final["autoria_revelada"]
    assert final["dictamen"]["recomendacion"] == "o1" and final["dictamen"]["autoria"]
    assert {p["miembro_id"] for p in final["propuestas"]} == {"prudente", "fiscal", "abogado"}
    assert final["expediente"]["preferencia_usuario"] == "Me inclino por amortizar."
    assert final["expediente"]["anonimizado"] is not None
    assert final["recomendacion"].startswith("Amortizar") and final["consenso"] == "la mayoría coincide"
    assert final["notificacion_id"]
    resumen = cliente.get("/cases/c1/consejo").json()["sesiones"][0]
    assert resumen["recomendacion"] == final["recomendacion"]


def test_authorship_stays_hidden_until_the_end(cliente: TestClient, servicio: ServicioConsejo) -> None:
    servicio._fabrica = FabricaFalsa(motor=MotorFalso(estado="fallida"))
    sesion = cliente.post("/cases/c1/consejo", json={"pregunta": PREGUNTA}).json()
    cliente.post(f"/cases/c1/consejo/{sesion['id']}/confirmar", json={})
    servicio.ejecutar_pendientes()
    vista = cliente.get(f"/cases/c1/consejo/{sesion['id']}").json()
    assert vista["estado"] == "fallida" and not vista["autoria_revelada"] and vista["dictamen"] is None
    ids = {p["miembro_id"] for p in vista["propuestas"]} | {c["evaluador_id"] for c in vista["criticas"]}
    assert ids == {"miembro_a", "miembro_b", "miembro_c"}


def test_reject_cancel_delete_and_errors(cliente: TestClient) -> None:
    corta = cliente.post("/cases/c1/consejo", json={"pregunta": "¿Y?"})
    assert corta.status_code == 422
    sesion = cliente.post("/cases/c1/consejo", json={"pregunta": PREGUNTA}).json()
    rechazada = cliente.post(f"/cases/c1/consejo/{sesion['id']}/rechazar", json={})
    assert rechazada.status_code == 200 and rechazada.json()["estado"] == "rechazada"
    assert cliente.post(f"/cases/c1/consejo/{sesion['id']}/confirmar", json={}).status_code == 409
    otra = cliente.post("/cases/c1/consejo", json={"pregunta": PREGUNTA}).json()
    assert cliente.post(f"/cases/c1/consejo/{otra['id']}/cancelar", json={}).json()["estado"] == "cancelada"
    assert cliente.delete(f"/cases/c1/consejo/{otra['id']}").status_code == 204
    assert cliente.get(f"/cases/c1/consejo/{otra['id']}").status_code == 404
    # A session of another case is «not found», and so is a case that does not exist.
    assert cliente.get(f"/cases/c2/consejo/{sesion['id']}").status_code == 404
    assert cliente.post(f"/cases/c2/consejo/{sesion['id']}/confirmar", json={}).status_code == 404
    assert cliente.get("/cases/nope/consejo").status_code == 404


def test_the_detector_endpoint(cliente: TestClient) -> None:
    dificil = cliente.post("/cases/c1/consejo/detectar", json={"pregunta": PREGUNTA}).json()
    assert dificil["dificil"] and dificil["opciones_detectadas"] == [
        "Amortizar la hipoteca",
        "Invertir en un fondo indexado",
    ]
    factual = cliente.post("/cases/c1/consejo/detectar", json={"pregunta": "¿Cuándo se presenta el 303?"}).json()
    assert not factual["dificil"] and factual["factual"]


def _app_con_servicio(env: Env) -> ServicioConsejo:  # noqa: F811
    servicio = ServicioConsejo(env.db_path, fabrica=FabricaFalsa(), en_segundo_plano=False, hoy=lambda: HOY)
    configurar_servicio(env.db_path, servicio)
    return servicio


def test_another_users_sessions_are_404_and_export_and_erasure_cover_them(env: Env) -> None:  # noqa: F811
    servicio = _app_con_servicio(env)
    try:
        ana = env.register("ana@example.com")
        luis = env.register("luis@example.com")
        case_id = env.new_case(ana)
        creada = env.client.post(f"/cases/{case_id}/consejo", json={"pregunta": PREGUNTA}, headers=ana)
        assert creada.status_code == 200, creada.text
        sesion_id = creada.json()["id"]
        assert creada.json()["origen"] == "usuario"
        for metodo, ruta in (
            ("get", f"/cases/{case_id}/consejo/{sesion_id}"),
            ("post", f"/cases/{case_id}/consejo/{sesion_id}/confirmar"),
            ("delete", f"/cases/{case_id}/consejo/{sesion_id}"),
        ):
            respuesta: Any = env.client.request(metodo, ruta, json={} if metodo == "post" else None, headers=luis)
            assert respuesta.status_code == 404, ruta
        # Luis's own case cannot reach Ana's session either.
        suyo = env.new_case(luis)
        assert env.client.get(f"/cases/{suyo}/consejo/{sesion_id}", headers=luis).status_code == 404
        assert servicio.obtener(sesion_id, case_id=case_id).sesion.estado == "pendiente_confirmacion"

        exportado = env.client.get("/me/export", headers=ana).json()
        caso_exportado = next(c for c in exportado["cases"] if c["case"]["case_id"] == case_id)
        assert [s["id"] for s in caso_exportado["consejo"]] == [sesion_id]

        borrado = env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana)
        assert borrado.status_code == 204
        assert servicio.almacen.listar(case_id) == []
    finally:
        configurar_servicio(env.db_path, None)

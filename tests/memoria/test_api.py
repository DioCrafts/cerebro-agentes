"""«Lo que sé de tus preferencias» over the API: visible, editable, isolated per user, exported and erased."""

from __future__ import annotations

import sqlite3

import pytest

from asistente.nucleo.memoria.almacen import REDACTADO
from asistente.nucleo.modulos.acciones import AccionPendiente, AccionStore
from asistente.nucleo.proactive.store import Notification
from asistente.plataforma import settings
from .conftest import PASSWORD, Env


@pytest.fixture
def api(env: Env, monkeypatch: pytest.MonkeyPatch) -> Env:
    # The agent reads the decisions from the configured database: the same file as every other store here.
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", env.db_path)
    return env


def _filas(env: Env, tabla: str, case_id: str) -> int:
    with sqlite3.connect(env.db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE case_id = ?", (case_id,)).fetchone()[0])


def _aviso(env: Env, case_id: str, key: str = "modulo:agua:tarifa:barata", **kw) -> Notification:
    datos = {"case_id": case_id, "kind": "modulo:agua", "key": key, "title": "Cambia a la tarifa barata"}
    datos.update(body="Ahorrarías.", severity="aviso", **kw)
    nota = Notification(**datos)
    assert env.notifications.add(nota)
    return nota


def test_the_user_writes_sees_edits_and_deletes_a_decision(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)

    creada = api.client.post(
        f"/cases/{case_id}/decisiones",
        json={"ambito": "fiscal:plan_pensiones", "tipo": "restriccion", "razon": "Necesito liquidez"},
        headers=ana,
    )
    assert creada.status_code == 200, creada.text
    decision = creada.json()["decision"]
    assert decision["fuente"] == "ajustes" and decision["estado"] == "vigente"
    assert decision["etiqueta"] == "Aportar a planes de pensiones" and decision["caducidad"] == "2027-11-16"
    assert decision["user_id"] == ana["_user_id"]

    listado = api.client.get(f"/cases/{case_id}/decisiones", headers=ana).json()
    assert [d["id"] for d in listado["decisiones"]] == [decision["id"]]
    assert {"ambito": "luz:cambio_tarifa", "etiqueta": "Cambiar de tarifa de la luz"} in listado["catalogo"]
    assert [h["evento"] for h in listado["historial"]] == ["creada"]

    editada = api.client.put(
        f"/cases/{case_id}/decisiones/{decision['id']}",
        json={"razon": "Necesito liquidez hasta vender el piso", "caducidad": None},
        headers=ana,
    )
    assert editada.status_code == 200 and editada.json()["decision"]["caducidad"] is None
    assert editada.json()["decision"]["razon"] == "Necesito liquidez hasta vender el piso"

    assert api.client.delete(f"/cases/{case_id}/decisiones/{decision['id']}", headers=ana).status_code == 204
    despues = api.client.get(f"/cases/{case_id}/decisiones", headers=ana).json()
    assert despues["decisiones"] == []
    # The history remembers that there was a decision, never its words.
    assert [h["evento"] for h in despues["historial"]] == ["creada", "editada", "borrada"]
    assert all(h["razon"] == REDACTADO for h in despues["historial"])
    with sqlite3.connect(api.db_path) as conn:
        textos = " ".join(r[0] for r in conn.execute("SELECT payload_json FROM decisiones_historial"))
    assert "liquidez" not in textos


def test_bad_input_is_a_readable_422(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    url = f"/cases/{case_id}/decisiones"
    assert api.client.post(url, json={"ambito": "Fiscal plan", "razon": "x"}, headers=ana).status_code == 422
    assert api.client.post(url, json={"ambito": "fiscal:donativo"}, headers=ana).status_code == 422
    vencida = api.client.post(
        url, json={"ambito": "fiscal:donativo", "razon": "x", "caducidad": "2026-01-01"}, headers=ana
    )
    assert vencida.status_code == 422 and "posterior a hoy" in vencida.json()["detail"]


def test_no_me_interesa_on_a_notice_and_the_inbox_never_shows_it_again(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    nota = _aviso(api, case_id)

    respuesta = api.client.post(
        f"/cases/{case_id}/decisiones/aviso/{nota.id}",
        json={"boton": "no_me_interesa", "razon": "tengo permanencia"},
        headers=ana,
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["avisos_retirados"] == 1
    assert respuesta.json()["decision"]["razon"] == "No me interesa: tengo permanencia"
    assert api.client.get(f"/cases/{case_id}/notifications", headers=ana).json()["notifications"] == []
    # The module writing it again tomorrow changes nothing.
    assert not api.notifications.add(nota.model_copy(update={"id": "otra"}))
    # Answering it twice is a 404 (it is gone).
    otra = api.client.post(f"/cases/{case_id}/decisiones/aviso/{nota.id}", json={"boton": "ya_lo_hice"}, headers=ana)
    assert otra.status_code == 404


def test_a_lever_of_the_optimiser_can_be_rejected_from_the_plan(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    for nombre, valor in (("rendimiento_trabajo_bruto", 45000), ("comunidad_autonoma", "Madrid")):
        assert api.client.put(f"/cases/{case_id}/facts/{nombre}", json={"value": valor}, headers=ana).status_code == 200
    body = {"case_id": case_id, "ejercicio": 2026, "fecha": "2026-11-16"}
    antes = api.client.post("/optimize", json=body, headers=ana).json()
    assert [p["id"] for p in antes["palancas"]] == ["plan_pensiones_contribuyente"]

    rechazo = api.client.post(
        f"/cases/{case_id}/decisiones",
        json={"palanca": "plan_pensiones_contribuyente", "boton": "no_me_interesa"},
        headers=ana,
    )
    assert rechazo.status_code == 200 and rechazo.json()["decision"]["ambito"] == "fiscal:plan_pensiones:contribuyente"
    despues = api.client.post("/optimize", json=body, headers=ana).json()
    assert despues["palancas"] == [] and "«No me interesa»" in despues["supuestos"][0]


def test_one_user_never_sees_or_changes_anothers_decisions(api: Env) -> None:
    ana = api.register("ana@example.com")
    luis = api.register("luis@example.com")
    caso_ana, caso_luis = api.new_case(ana), api.new_case(luis)
    decision = api.client.post(
        f"/cases/{caso_ana}/decisiones", json={"ambito": "fiscal:donativo", "razon": "no quiero donar"}, headers=ana
    ).json()["decision"]

    assert api.client.get(f"/cases/{caso_ana}/decisiones", headers=luis).status_code == 404
    # Luis's own case does not reach Ana's decision either.
    url = f"/cases/{caso_luis}/decisiones/{decision['id']}"
    assert api.client.put(url, json={"razon": "x"}, headers=luis).status_code == 404
    assert api.client.delete(url, headers=luis).status_code == 404
    assert api.client.get(f"/cases/{caso_luis}/decisiones", headers=luis).json()["decisiones"] == []
    assert api.client.get(f"/cases/{caso_ana}/decisiones", headers=ana).json()["decisiones"][0]["razon"] == (
        "no quiero donar"
    )
    # Ana's rejection does not hold back Luis's notices.
    _aviso(api, caso_luis, key="modulo:agua:x")
    api.client.post(f"/cases/{caso_ana}/decisiones", json={"ambito": "agua", "razon": "nada de agua"}, headers=ana)
    assert len(api.client.get(f"/cases/{caso_luis}/notifications", headers=luis).json()["notifications"]) == 1


def test_a_rejected_pending_action_is_remembered(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    acciones = AccionStore(api.db_path)
    accion = acciones.add(
        AccionPendiente(
            case_id=case_id, modulo="agua", herramienta="agua_cambiar_tarifa", resumen="Agua — Cambiar tarifa."
        )
    )
    assert api.client.post(f"/acciones/{accion.id}/rechazar", headers=ana).status_code == 200
    (decision,) = api.client.get(f"/cases/{case_id}/decisiones", headers=ana).json()["decisiones"]
    assert (decision["ambito"], decision["tipo"], decision["fuente"]) == (
        "agua:accion:cambiar_tarifa",
        "rechazo",
        "accion",
    )
    assert "Agua — Cambiar tarifa." in decision["razon"]


def test_decisions_are_exported_and_erased_with_the_account(api: Env) -> None:
    ana = api.register("ana@example.com")
    luis = api.register("luis@example.com")
    case_id, otro = api.new_case(ana), api.new_case(luis)
    for caso, quien, razon in ((case_id, ana, "necesito liquidez"), (otro, luis, "lo de Luis")):
        api.client.post(
            f"/cases/{caso}/decisiones", json={"ambito": "fiscal:plan_pensiones", "razon": razon}, headers=quien
        )

    exportado = api.client.get("/me/export", headers=ana).json()
    (caso,) = exportado["cases"]
    (decision,) = caso["decisiones"]["decisiones"]
    assert decision["razon"] == "necesito liquidez" and decision["case_id"] == case_id
    assert [h["evento"] for h in caso["decisiones"]["historial"]] == ["creada"]
    assert "lo de Luis" not in str(exportado)

    assert api.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    assert _filas(api, "decisiones", case_id) == 0 and _filas(api, "decisiones_historial", case_id) == 0
    # Only Ana's: Luis keeps his.
    assert _filas(api, "decisiones", otro) == 1


def test_erasing_a_case_by_any_path_erases_its_decisions(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    api.client.post(f"/cases/{case_id}/decisiones", json={"ambito": "fiscal:donativo", "razon": "no"}, headers=ana)
    assert _filas(api, "decisiones", case_id) == 1
    assert api.cases.delete_case(case_id)
    assert _filas(api, "decisiones", case_id) == 0 and _filas(api, "decisiones_historial", case_id) == 0


def test_forgotten_words_do_not_stay_in_the_database_file(api: Env) -> None:
    from pathlib import Path

    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    palabras = "porque estoy pagando la residencia de mi madre"
    decision = api.client.post(
        f"/cases/{case_id}/decisiones", json={"ambito": "fiscal:donativo", "razon": palabras}, headers=ana
    ).json()["decision"]
    assert api.client.delete(f"/cases/{case_id}/decisiones/{decision['id']}", headers=ana).status_code == 204
    for fichero in (Path(api.db_path), Path(api.db_path + "-wal")):
        if fichero.exists():
            assert palabras.encode() not in fichero.read_bytes(), fichero


def test_erasing_a_modules_data_erases_its_decisions_only(api: Env) -> None:
    ana = api.register("ana@example.com")
    case_id = api.new_case(ana)
    url = f"/cases/{case_id}/decisiones"
    api.client.post(url, json={"ambito": "luz:cambio_tarifa", "razon": "tengo permanencia"}, headers=ana)
    api.client.post(url, json={"ambito": "fiscal:donativo", "razon": "no quiero donar"}, headers=ana)
    assert api.client.put("/me/modulos/luz", json={"activo": True}, headers=ana).status_code == 200
    apagado = api.client.put("/me/modulos/luz", json={"activo": False, "borrar_datos": True}, headers=ana)
    assert apagado.status_code == 200, apagado.text
    quedan = api.client.get(url, headers=ana).json()["decisiones"]
    assert [d["ambito"] for d in quedan] == ["fiscal:donativo"]

"""«Lo que sé de ti» over the API: owner-only, correctable, erasable, in the account export and erased with it."""

from __future__ import annotations

import sqlite3

import pytest

from asistente.nucleo.cerebro.contrato import TurnoConversacion
from asistente.nucleo.recuerdos.almacen import Titular
from asistente.nucleo.recuerdos.servicio import ServicioMemoria, servicio
from asistente.plataforma import settings
from tests.auth.conftest import PASSWORD, Env
from .conftest import ProveedorFalso, Reloj, candidato, salida


def _hablar(memoria: ServicioMemoria, titular: Titular, conversacion: str, pregunta: str) -> None:
    from asistente.nucleo.recuerdos import almacen

    memoria.registrar_turno(
        TurnoConversacion(
            conversacion_id=conversacion,
            case_id=titular.case_id or None,
            user_id=titular.user_id or None,
            fecha=almacen.ahora(),
            pregunta=pregunta,
            respuesta="Entendido.",
        )
    )


@pytest.fixture
def ana(env: Env, reloj: Reloj) -> dict:
    """Ana, her case, two conversations summarised into two items (one superseded by a newer one)."""
    cabeceras = env.register("ana@example.com")
    case_id = env.new_case(cabeceras)
    titular = Titular(case_id, cabeceras["_user_id"])
    memoria = servicio(env.db_path)
    _hablar(memoria, titular, "conv-ana-0001", "Vivo en Sevilla y prefiero respuestas cortas")
    reloj.avanzar(hours=1)
    memoria.resumir_pendientes(
        ProveedorFalso(
            [
                salida(
                    candidato("dato", "ciudad", "Vive en Sevilla", "Vivo en Sevilla"),
                    candidato("preferencia", "estilo", "Prefiere respuestas cortas", "prefiero respuestas cortas"),
                    titulo="Mudanza y estilo",
                    resumen="Habló de dónde vive y de cómo quiere las respuestas.",
                )
            ]
        ),
        lotes=False,
    )
    reloj.avanzar(days=2)
    _hablar(memoria, titular, "conv-ana-0002", "Ya no vivo en Sevilla, ahora vivo en Madrid")
    reloj.avanzar(hours=1)
    memoria.resumir_pendientes(
        ProveedorFalso(
            [salida(candidato("dato", "ciudad", "Vive en Madrid", "ahora vivo en Madrid"), titulo="Mudanza")]
        ),
        lotes=False,
    )
    return {"h": cabeceras, "case_id": case_id, "titular": titular}


def test_the_memory_needs_a_session(env: Env) -> None:
    assert env.client.get("/recuerdos").status_code == 401
    assert env.client.get("/conversaciones").status_code == 401
    assert env.client.delete("/recuerdos").status_code == 401


def test_the_user_sees_every_item_with_its_origin(env: Env, ana: dict) -> None:
    respuesta = env.client.get(f"/recuerdos?case_id={ana['case_id']}", headers=ana["h"])
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["disponible"] and datos["activa"] and not datos["sin_cuenta"] and datos["retencion_dias"] == 30
    assert "contraseñas" in datos["explicacion"] and datos["conversaciones"] == 2
    textos = {r["texto"]: r for r in datos["recuerdos"]}
    assert set(textos) == {"Vive en Madrid", "Prefiere respuestas cortas"}
    madrid = textos["Vive en Madrid"]
    assert madrid["origen"] == {
        "conversacion_id": "conv-ana-0002",
        "fecha": "2026-11-14",
        "cita": "ahora vivo en Madrid",
        "titulo_conversacion": "Mudanza",
    }
    assert [h["texto"] for h in datos["historial"]] == ["Vive en Sevilla"]
    assert datos["historial"][0]["sustituido_por"] == madrid["id"]


def test_correct_forget_one_and_forget_all(env: Env, ana: dict) -> None:
    base, h = f"?case_id={ana['case_id']}", ana["h"]
    items = env.client.get(f"/recuerdos{base}", headers=h).json()["recuerdos"]
    estilo = next(r for r in items if r["tipo"] == "preferencia")
    corregido = env.client.patch(
        f"/recuerdos/{estilo['id']}{base}", json={"texto": "Prefiere respuestas cortas y con ejemplos"}, headers=h
    )
    assert corregido.status_code == 200 and corregido.json()["corregido"]
    assert corregido.json()["origen"]["cita"] == "prefiero respuestas cortas"
    malo = env.client.patch(f"/recuerdos/{estilo['id']}{base}", json={"texto": "mi PIN es 4821"}, headers=h)
    assert malo.status_code == 422 and "PIN" in malo.json()["detail"]
    assert env.client.patch(f"/recuerdos/r_noexiste{base}", json={"texto": "x"}, headers=h).status_code == 404

    assert env.client.delete(f"/recuerdos/{estilo['id']}{base}", headers=h).status_code == 204
    assert env.client.delete(f"/recuerdos/{estilo['id']}{base}", headers=h).status_code == 404
    todo = env.client.delete(f"/recuerdos{base}", headers=h)
    assert todo.status_code == 200 and todo.json() == {
        "recuerdos": 2,
        "conversaciones": 2,
        "mensajes": 4,
    }  # Madrid and its history
    vacio = env.client.get(f"/recuerdos{base}", headers=h).json()
    assert vacio["recuerdos"] == [] and vacio["historial"] == [] and vacio["conversaciones"] == 0


def test_another_user_gets_404_for_everything_of_anas(env: Env, ana: dict) -> None:
    bob = env.register("bob@example.com")
    caso_bob = env.new_case(bob)
    ajeno = f"?case_id={ana['case_id']}"
    assert env.client.get(f"/recuerdos{ajeno}", headers=bob).status_code == 404
    assert env.client.get(f"/conversaciones{ajeno}", headers=bob).status_code == 404
    assert env.client.delete(f"/recuerdos{ajeno}", headers=bob).status_code == 404
    assert env.client.put(f"/recuerdos/interruptor{ajeno}", json={"activa": False}, headers=bob).status_code == 404
    item = env.client.get(f"/recuerdos{ajeno}", headers=ana["h"]).json()["recuerdos"][0]
    propio = f"?case_id={caso_bob}"
    # Ana's ids under Bob's own case: as if they did not exist.
    assert env.client.patch(f"/recuerdos/{item['id']}{propio}", json={"texto": "x"}, headers=bob).status_code == 404
    assert env.client.delete(f"/recuerdos/{item['id']}{propio}", headers=bob).status_code == 404
    assert env.client.get(f"/conversaciones/conv-ana-0001{propio}", headers=bob).status_code == 404
    assert env.client.delete(f"/conversaciones/conv-ana-0001{propio}", headers=bob).status_code == 404
    assert env.client.get(f"/recuerdos{propio}", headers=bob).json()["recuerdos"] == []
    # Nothing of Ana's changed.
    assert len(env.client.get(f"/recuerdos{ajeno}", headers=ana["h"]).json()["recuerdos"]) == 2


def test_the_switch(env: Env, ana: dict) -> None:
    base, h = f"?case_id={ana['case_id']}", ana["h"]
    assert env.client.get(f"/recuerdos/interruptor{base}", headers=h).json()["activa"] is True
    pausa = env.client.put(f"/recuerdos/interruptor{base}", json={"activa": False}, headers=h)
    assert pausa.status_code == 200 and pausa.json()["activa"] is False
    datos = env.client.get(f"/recuerdos{base}", headers=h).json()
    assert datos["activa"] is False and len(datos["recuerdos"]) == 2  # paused, nothing deleted
    # The switch is the user's: the same without a case.
    assert env.client.get("/recuerdos/interruptor", headers=h).json()["activa"] is False
    bloque = servicio(env.db_path).recuperar(
        case_id=ana["case_id"], user_id=ana["titular"].user_id, consulta="¿dónde vivo?"
    )
    assert bloque.vacio_por == "desactivada"


def test_conversations_list_detail_and_delete(env: Env, ana: dict) -> None:
    base, h = f"?case_id={ana['case_id']}", ana["h"]
    lista = env.client.get(f"/conversaciones{base}", headers=h).json()
    assert [c["id"] for c in lista["conversaciones"]] == ["conv-ana-0002", "conv-ana-0001"]
    primera = lista["conversaciones"][1]
    assert primera["titulo"] == "Mudanza y estilo" and primera["resumen"].startswith("Habló de dónde vive")
    assert primera["retenidos"] == 2 and primera["recuerdos"] == 2
    detalle = env.client.get(f"/conversaciones/conv-ana-0001{base}", headers=h).json()
    assert [m["rol"] for m in detalle["mensajes"]] == ["user", "assistant"]
    assert {r["texto"] for r in detalle["recuerdos"]} == {"Vive en Sevilla", "Prefiere respuestas cortas"}
    assert env.client.delete(f"/conversaciones/conv-ana-0001{base}", headers=h).status_code == 204
    assert env.client.get(f"/conversaciones/conv-ana-0001{base}", headers=h).status_code == 404
    textos = [r["texto"] for r in env.client.get(f"/recuerdos{base}", headers=h).json()["recuerdos"]]
    assert textos == ["Vive en Madrid"]


def test_without_a_case_the_signed_in_users_own_memory(env: Env, reloj: Reloj) -> None:
    h = env.register("eva@example.com")
    memoria = servicio(env.db_path)
    _hablar(memoria, Titular("", h["_user_id"]), "conv-eva-0001", "Hola, soy Eva")
    lista = env.client.get("/conversaciones", headers=h).json()["conversaciones"]
    assert [c["id"] for c in lista] == ["conv-eva-0001"]


def test_without_accounts_and_without_a_case_there_is_nobody(env: Env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    datos = env.client.get("/recuerdos").json()
    assert datos["sin_cuenta"] and datos["recuerdos"] == []
    assert env.client.delete("/recuerdos").status_code == 404
    assert env.client.put("/recuerdos/interruptor", json={"activa": False}).status_code == 404


def test_the_account_export_contains_the_memory(env: Env, ana: dict) -> None:
    _hablar(servicio(env.db_path), Titular("", ana["titular"].user_id), "conv-ana-sin-caso", "Pregunta sin expediente")
    exportado = env.client.get("/me/export", headers=ana["h"]).json()
    [caso] = exportado["cases"]
    memoria = caso["recuerdos"]
    assert {r["texto"] for r in memoria["recuerdos"]} == {
        "Vive en Madrid",
        "Vive en Sevilla",
        "Prefiere respuestas cortas",
    }
    conversacion = next(c for c in memoria["conversaciones"] if c["id"] == "conv-ana-0001")
    assert [m["texto"] for m in conversacion["mensajes_retenidos"]][0] == "Vivo en Sevilla y prefiero respuestas cortas"
    assert [c["id"] for c in exportado["recuerdos"]["conversaciones"]] == ["conv-ana-sin-caso"]
    assert "recuerdos" in exportado["not_included"]


def test_deleting_the_account_erases_the_memory(env: Env, ana: dict) -> None:
    _hablar(servicio(env.db_path), Titular("", ana["titular"].user_id), "conv-ana-sin-caso", "Pregunta sin expediente")
    respuesta = env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana["h"])
    assert respuesta.status_code == 204, respuesta.text
    with sqlite3.connect(env.db_path) as conn:
        for tabla in (
            "recuerdos",
            "recuerdos_mensajes",
            "recuerdos_conversaciones",
            "recuerdos_lotes",
            "recuerdos_interruptor",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0, tabla

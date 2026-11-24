"""«Lo que sé de ti» over the API: owner-only, correctable, erasable, in the account export and erased with it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from asistente.nucleo.cerebro.contrato import TurnoConversacion
from asistente.nucleo.recuerdos.servicio import Titular, servicio
from asistente.plataforma import settings
from tests.auth.conftest import PASSWORD, Env
from tests.recuerdos.falso import HindsightFalso


def _hablar(env: Env, titular: Titular, conversacion: str, texto: str, minuto: int = 0) -> None:
    servicio(env.db_path).registrar_turno(
        TurnoConversacion(
            conversacion_id=conversacion,
            case_id=titular.case_id or None,
            user_id=titular.user_id or None,
            fecha=datetime(2026, 11, 12, 9, minuto, tzinfo=UTC),
            pregunta=texto,
            respuesta="Entendido.",
        )
    )


@pytest.fixture
def ana(env: Env, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(settings, "MEMORIA_CONVERSACIONES", True)
    cabeceras = env.register("ana@example.com")
    case_id = env.new_case(cabeceras)
    titular = Titular.de(case_id, cabeceras["_user_id"])
    _hablar(env, titular, "conv-ana-0001", "Vivo en Sevilla y prefiero respuestas cortas", 1)
    _hablar(env, titular, "conv-ana-0002", "Tengo la hipoteca en ING desde 2019", 2)
    return {"h": cabeceras, "case_id": case_id, "titular": titular, "base": f"?case_id={case_id}"}


def test_the_memory_needs_a_session(env: Env) -> None:
    assert env.client.get("/recuerdos").status_code == 401
    assert env.client.get("/conversaciones").status_code == 401
    assert env.client.delete("/recuerdos").status_code == 401


def test_the_owner_sees_what_is_remembered_with_its_conversation(env: Env, ana: dict) -> None:
    datos = env.client.get(f"/recuerdos{ana['base']}", headers=ana["h"]).json()
    assert datos["activa"] and datos["disponible"] and not datos["sin_cuenta"]
    assert datos["conversaciones"] == 2
    [hipoteca, sevilla] = datos["recuerdos"]
    assert hipoteca["texto"] == "Tengo la hipoteca en ING desde 2019" and hipoteca["tipo"] == "hecho"
    assert sevilla["titulo_conversacion"] == "Vivo en Sevilla y prefiero respuestas cortas"
    assert "nunca mis respuestas" in datos["explicacion"]


def test_correct_forget_and_forget_everything(env: Env, ana: dict, hindsight: HindsightFalso) -> None:
    base, h = ana["base"], ana["h"]
    hipoteca = next(
        r for r in env.client.get(f"/recuerdos{base}", headers=h).json()["recuerdos"] if "ING" in r["texto"]
    )
    corregido = env.client.patch(
        f"/recuerdos/{hipoteca['id']}{base}", json={"texto": "Tengo la hipoteca en Openbank desde 2023"}, headers=h
    )
    assert corregido.status_code == 200, corregido.text
    [nuevo] = corregido.json()["recuerdos"]
    assert nuevo["corregido"] and "Openbank" in nuevo["texto"]
    malo = env.client.patch(f"/recuerdos/{nuevo['id']}{base}", json={"texto": "mi PIN es 4821"}, headers=h)
    assert malo.status_code == 422
    assert env.client.patch(f"/recuerdos/no-existe{base}", json={"texto": "algo largo"}, headers=h).status_code == 404
    assert env.client.delete(f"/recuerdos/{nuevo['id']}{base}", headers=h).status_code == 204
    assert env.client.delete(f"/recuerdos/{nuevo['id']}{base}", headers=h).status_code == 404
    assert env.client.delete(f"/recuerdos{base}", headers=h).json() == {"recuerdos": 1, "conversaciones": 2}
    assert ana["titular"].banco not in hindsight.bancos
    assert env.client.get(f"/recuerdos{base}", headers=h).json()["recuerdos"] == []


def test_conversations_list_detail_and_delete(env: Env, ana: dict) -> None:
    base, h = ana["base"], ana["h"]
    lista = env.client.get(f"/conversaciones{base}", headers=h).json()["conversaciones"]
    assert [(c["id"], c["mensajes"]) for c in lista] == [("conv-ana-0002", 1), ("conv-ana-0001", 1)]
    detalle = env.client.get(f"/conversaciones/conv-ana-0001{base}", headers=h).json()
    assert [m["texto"] for m in detalle["mensajes"]] == ["Vivo en Sevilla y prefiero respuestas cortas"]
    assert len(detalle["recuerdos"]) == 1
    assert env.client.delete(f"/conversaciones/conv-ana-0001{base}", headers=h).status_code == 204
    assert env.client.get(f"/conversaciones/conv-ana-0001{base}", headers=h).status_code == 404


def test_another_users_memory_is_404(env: Env, ana: dict) -> None:
    bob = env.register("bob@example.com")
    ajeno = ana["base"]
    assert env.client.get(f"/recuerdos{ajeno}", headers=bob).status_code == 404
    assert env.client.get(f"/conversaciones{ajeno}", headers=bob).status_code == 404
    assert env.client.delete(f"/recuerdos{ajeno}", headers=bob).status_code == 404
    # Bob's own memory outside any case is empty: Ana's bank is not his.
    assert env.client.get("/recuerdos", headers=bob).json()["recuerdos"] == []


def test_pause_and_resume(env: Env, ana: dict, hindsight: HindsightFalso) -> None:
    base, h = ana["base"], ana["h"]
    assert env.client.put(f"/recuerdos/interruptor{base}", json={"activa": False}, headers=h).json()["activa"] is False
    _hablar(env, ana["titular"], "conv-ana-0003", "Me he comprado un coche eléctrico nuevo", 3)
    assert all("eléctrico" not in t for t in hindsight.textos(ana["titular"].banco))
    assert len(env.client.get(f"/recuerdos{base}", headers=h).json()["recuerdos"]) == 2  # nothing deleted
    assert env.client.put(f"/recuerdos/interruptor{base}", json={"activa": True}, headers=h).json()["activa"] is True


def test_hindsight_down_is_a_503_that_says_so(env: Env, ana: dict, hindsight: HindsightFalso) -> None:
    hindsight.caido = True
    respuesta = env.client.get(f"/recuerdos{ana['base']}", headers=ana["h"])
    assert respuesta.status_code == 503 and "no responde" in respuesta.json()["detail"]


def test_the_account_export_contains_the_memory(env: Env, ana: dict) -> None:
    _hablar(env, Titular.de(None, ana["titular"].user_id), "conv-ana-sin-caso", "Prefiero que me tutees siempre", 4)
    exportado = env.client.get("/me/export", headers=ana["h"]).json()
    [caso] = exportado["cases"]
    assert {r["texto"] for r in caso["recuerdos"]["recuerdos"]} == {
        "Vivo en Sevilla y prefiero respuestas cortas",
        "Tengo la hipoteca en ING desde 2019",
    }
    assert [c["id"] for c in exportado["recuerdos"]["conversaciones"]] == ["conv-ana-sin-caso"]
    assert "recuerdos" in exportado["not_included"]


def test_deleting_the_account_erases_every_bank(env: Env, ana: dict, hindsight: HindsightFalso) -> None:
    sin_caso = Titular.de(None, ana["titular"].user_id)
    _hablar(env, sin_caso, "conv-ana-sin-caso", "Prefiero que me tutees siempre", 4)
    respuesta = env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana["h"])
    assert respuesta.status_code == 204, respuesta.text
    assert ana["titular"].banco not in hindsight.bancos and sin_caso.banco not in hindsight.bancos


def test_the_account_stays_when_hindsight_cannot_erase_the_memory(env: Env, ana: dict, hindsight) -> None:
    hindsight.caido = True
    with pytest.raises(Exception):  # noqa: B017 - the erasure must stop before the account goes
        env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana["h"])
    hindsight.caido = False
    assert env.client.get("/auth/me", headers=ana["h"]).status_code == 200

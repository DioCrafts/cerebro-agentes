"""The central agent's task tools: proposals only, and every commitment checked against the user's own words."""

from __future__ import annotations

import json
from typing import Any

from asistente.nucleo.memoria.servicio import decisiones_del_caso
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.tareas.herramientas import TareasAgente
from .conftest import Entorno


def _agente(entorno: Entorno, query: str, historial: list[dict[str, Any]] | None = None) -> TareasAgente:
    case = entorno.casos.get_case("c1")
    agente = TareasAgente.para(case, query=query, chat_history=historial or [])
    assert agente is not None
    agente._motor_factory = lambda: entorno.motor(ejecutar_consultas=False)  # noqa: SLF001 - test wiring
    return agente


def _llamar(agente: TareasAgente, nombre: str, **args: Any) -> Any:
    manejador = next(h for spec, h in agente.herramientas() if spec.name == nombre)
    return manejador(args)


def test_no_tools_without_a_case() -> None:
    assert TareasAgente.para(None, query="hola") is None


def test_create_accept_confirm_from_the_chat(entorno: Entorno) -> None:
    entorno.caso()
    pide = "Quiero renovar el pasaporte, que me voy de viaje"
    agente = _agente(entorno, pide)
    # The goal must be the user's words.
    assert _llamar(
        agente, "crear_tarea", objetivo="renovación urgente del pasaporte", plantilla="renovar_pasaporte"
    ).is_error
    creada = _llamar(agente, "crear_tarea", objetivo="renovar el pasaporte", plantilla="renovar_pasaporte")
    assert not creada.is_error and "PROPUESTA" in creada.content and "NO ha empezado" in creada.content
    estado = json.loads(_llamar(agente, "estado_tareas").content)
    tarea = estado["tareas"][0]
    assert tarea["estado"] == "propuesta"
    assert "renovar_pasaporte" in {p["id"] for p in estado["plantillas"]}
    ref = tarea["ref"]

    historial = [{"role": "user", "content": pide}, {"role": "assistant", "content": "Te propongo esto: …"}]
    # The assistant did not show the task: not accepted.
    respuesta = _llamar(
        _agente(entorno, "sí, adelante", historial), "avanzar_tarea", tarea=ref, cita_del_usuario="sí, adelante"
    )
    assert respuesta.is_error and "debe ver antes" in respuesta.content
    historial[-1]["content"] = f"Te propongo la tarea «Renovar el pasaporte» ({ref}) con estos pasos… ¿La empezamos?"
    # A «no», or words the user did not write, do not accept it.
    no = _llamar(
        _agente(entorno, "no, ahora no", historial), "avanzar_tarea", tarea=ref, cita_del_usuario="no, ahora no"
    )
    assert no.is_error and "negación" in no.content
    inventado = _llamar(
        _agente(entorno, "vale", historial), "avanzar_tarea", tarea=ref, cita_del_usuario="sí, empieza ya"
    )
    assert inventado.is_error and "literalmente" in inventado.content
    aceptada = _llamar(_agente(entorno, "Sí, empiézala", historial), "avanzar_tarea", tarea=ref, cita_del_usuario="Sí")
    assert not aceptada.is_error and json.loads(aceptada.content)["estado"] == "esperando_usuario"

    # Confirming a step needs the assistant to have named it, and the user's yes (with the date they wrote).
    historial += [
        {"role": "user", "content": "Sí, empiézala"},
        {"role": "assistant", "content": "¿Has hecho ya el paso «Pide la cita previa»?"},
    ]
    agente = _agente(entorno, "Sí, ya la tengo para el 9/12/2026", historial)
    mal = _llamar(
        agente, "confirmar_paso", tarea=ref, paso="cita", decision="hecho", cita_del_usuario="Sí", fecha="2026-12-10"
    )
    assert mal.is_error and "fecha" in mal.content
    bien = _llamar(
        agente,
        "confirmar_paso",
        tarea=ref,
        paso="cita",
        decision="hecho",
        cita_del_usuario="Sí, ya la tengo",
        fecha="2026-12-09",
    )
    assert not bien.is_error
    detalle = json.loads(_llamar(agente, "estado_tareas", tarea=ref).content)
    pasos = {p["id"]: p for p in detalle["pasos"]}
    assert pasos["cita"]["estado"] == "hecho" and pasos["cita"]["fecha"] == "2026-12-09"
    assert pasos["recordatorio"]["estado"] == "esperando"


def test_an_action_is_never_confirmed_in_the_chat(entorno: Entorno) -> None:
    entorno.caso()
    pide = "quiero cambiar la tarifa del agua"
    agente = _agente(entorno, pide)
    creada = _llamar(
        agente,
        "crear_tarea",
        objetivo="cambiar la tarifa del agua",
        titulo="Tarifa del agua",
        pasos=[{"id": "cambiar", "titulo": "Cambia la tarifa", "tipo": "accion", "herramienta": "agua_cambiar_tarifa"}],
    )
    assert not creada.is_error
    ref = json.loads(_llamar(agente, "estado_tareas").content)["tareas"][0]["ref"]
    historial = [
        {"role": "user", "content": pide},
        {"role": "assistant", "content": f"¿Empiezo «Tarifa del agua» ({ref})?"},
    ]
    _llamar(_agente(entorno, "sí", historial), "avanzar_tarea", tarea=ref, cita_del_usuario="sí")
    historial += [{"role": "user", "content": "sí"}, {"role": "assistant", "content": "¿Confirmas «Cambia la tarifa»?"}]
    respuesta = _llamar(
        _agente(entorno, "sí, confirmo", historial),
        "confirmar_paso",
        tarea=ref,
        paso="cambiar",
        decision="hecho",
        cita_del_usuario="sí, confirmo",
    )
    assert respuesta.is_error and "botón" in respuesta.content
    assert "agua_cambiar_tarifa" not in entorno.llamadas.nombres()
    assert AccionStore(entorno.db).list("c1", estado="pendiente")


def test_a_gate_error_goes_back_to_the_model(entorno: Entorno) -> None:
    entorno.caso()
    agente = _agente(entorno, "quiero ahorrar en el agua")
    respuesta = _llamar(
        agente,
        "crear_tarea",
        objetivo="ahorrar en el agua",
        titulo="Agua",
        pasos=[{"id": "x", "titulo": "Paga", "tipo": "calculo", "herramienta": "agua_cambiar_tarifa"}],
    )
    assert respuesta.is_error and "no es válido" in respuesta.content
    assert json.loads(_llamar(agente, "estado_tareas").content)["tareas"] == []


def test_pause_and_cancel_need_the_users_words_and_a_reason_is_remembered(entorno: Entorno) -> None:
    entorno.caso()
    pide = "quiero pagar menos de luz"
    agente = _agente(entorno, pide)
    _llamar(agente, "crear_tarea", objetivo=pide, plantilla="cambiar_tarifa_luz")
    ref = json.loads(_llamar(agente, "estado_tareas").content)["tareas"][0]["ref"]
    historial = [
        {"role": "user", "content": pide},
        {"role": "assistant", "content": "¿Empezamos «Cambiar de tarifa de la luz»?"},
    ]
    _llamar(_agente(entorno, "vale", historial), "avanzar_tarea", tarea=ref, cita_del_usuario="vale")

    pausa = _agente(entorno, "páusala de momento")
    assert _llamar(pausa, "pausar_tarea", tarea=ref, cita_del_usuario="espera un poco").is_error
    assert (
        json.loads(_llamar(pausa, "pausar_tarea", tarea=ref, cita_del_usuario="páusala").content)["estado"] == "pausada"
    )

    texto = "cancélala, no quiero cambiar porque tengo permanencia"
    cancela = _agente(entorno, texto)
    assert _llamar(cancela, "cancelar_tarea", tarea=ref, cita_del_usuario="cancélala", motivo="me sale caro").is_error
    hecha = _llamar(cancela, "cancelar_tarea", tarea=ref, cita_del_usuario="cancélala", motivo="tengo permanencia")
    assert json.loads(hecha.content)["estado"] == "cancelada"
    decisiones = decisiones_del_caso(entorno.db, "c1")
    assert [(d.ambito, d.fuente, d.razon) for d in decisiones] == [("luz:cambio_tarifa", "chat", "tengo permanencia")]

    # Proposing it again shows the user's earlier «no».
    otra = _llamar(_agente(entorno, pide), "crear_tarea", objetivo=pide, plantilla="cambiar_tarifa_luz")
    assert "tengo permanencia" in otra.content

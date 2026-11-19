"""Confirming from the chat: never a plain «sí», only the one-time code of THAT item, through the app's own gates."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from asistente.nucleo.agent.agent import AgentMode, AgentResult
from asistente.nucleo.consejo.servicio import servicio_para
from asistente.nucleo.modulos import registro
from asistente.nucleo.modulos.acciones import AccionPendiente, AccionStore
from asistente.plataforma import settings
from asistente.plataforma.mensajeria import confirmaciones
from asistente.plataforma.mensajeria.confirmaciones import TEXTO_CODIGO_NO_VALE
from tests.modulos_nucleo.fakes import Llamadas, agua, instalar
from .conftest import CASE_ID, Montaje

DECISION = "¿Me conviene amortizar la hipoteca o invertir los 20.000 € que tengo ahorrados?"


@pytest.fixture
def llamadas(monkeypatch: pytest.MonkeyPatch) -> Iterator[Llamadas]:
    """The fake «agua» module on, with its acting tool ``agua_cambiar_tarifa`` (records every run)."""
    registradas = Llamadas()
    instalar(agua(registradas))
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "fiscal,agua")
    yield registradas
    registro.reiniciar()


class AgenteQuePropone:
    """Answers, and (once) proposes the acting tool as a pending action — as a module specialist does."""

    def __init__(self) -> None:
        self.propuesto = False
        self.consultas: list[str] = []

    def run(self, query: str, *, case: Any = None, **_: Any) -> AgentResult:
        self.consultas.append(query)
        acciones = []
        if not self.propuesto:
            self.propuesto = True
            acciones = [_accion()]
        return AgentResult(
            answer="Te lo dejo preparado.",
            mode=AgentMode.UNVERIFIED,
            provider="fake",
            model="fake-1",
            acciones_propuestas=acciones,
        )


def _accion() -> AccionPendiente:
    return AccionPendiente(
        case_id=CASE_ID,
        modulo="agua",
        herramienta="agua_cambiar_tarifa",
        argumentos={"x": 1},
        resumen="Agua — Cambiar la tarifa. Datos: x: 1.",
    )


def _codigo(texto: str, despues: str = "/confirmar ") -> str:
    inicio = texto.index(despues) + len(despues)
    return texto[inicio : inicio + 4]


def _estado_accion(montaje: Montaje, accion_id: str) -> str:
    accion = AccionStore(montaje.db).get(accion_id)
    assert accion is not None
    return accion.estado


def test_a_turn_that_proposes_an_action_offers_its_one_time_code(montaje: Montaje, llamadas: Llamadas) -> None:
    montaje.agente.agente = AgenteQuePropone()
    montaje.emparejar()
    (texto,) = montaje.decir("Cámbiame la tarifa del agua")
    assert texto.startswith("Te lo dejo preparado.")
    assert "*Pendiente de tu confirmación*" in texto and "Agua — Cambiar la tarifa" in texto
    codigo = _codigo(texto)
    assert codigo.isdigit() and f"/rechazar {codigo}" in texto
    assert llamadas.nombres() == []  # nothing ran

    assert montaje.decir(f"/confirmar {codigo}")[0].startswith("Hecho: Agua — Cambiar la tarifa")
    assert llamadas.nombres() == ["agua_cambiar_tarifa"]


def test_a_plain_yes_or_a_forwarded_command_confirms_nothing(montaje: Montaje, llamadas: Llamadas) -> None:
    from .test_conversacion import AgenteLento

    agente = AgenteLento()
    montaje.agente.agente = agente
    montaje.emparejar()
    accion = AccionStore(montaje.db).add(_accion())
    (pendientes,) = montaje.decir("/pendientes")
    codigo = _codigo(pendientes)

    montaje.decir("sí")
    montaje.decir("Sí, confírmalo")
    montaje.decir(f"/confirmar {codigo}", reenviado=True)
    montaje.decir("ok", cita=f"/confirmar {codigo}")

    assert _estado_accion(montaje, accion.id) == "pendiente" and llamadas.nombres() == []
    assert len(agente.consultas) == 4  # they were questions for the brain (the forwarded ones as data), nothing else


def test_a_wrong_expired_or_reused_code_does_nothing_and_the_right_one_confirms_exactly_its_item(
    montaje: Montaje, llamadas: Llamadas, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MENSAJERIA_CONTACTOS_POR_USUARIO", 2)
    vinculo = montaje.emparejar("c1")
    otro = montaje.emparejar("c2")
    acciones = AccionStore(montaje.db)
    primera, segunda = acciones.add(_accion()), acciones.add(_accion())
    sesion = servicio_para(montaje.db).convocar(case=montaje.casos.get_case(CASE_ID), pregunta=DECISION)

    (pendientes,) = montaje.decir("/pendientes")
    lineas = pendientes.split("\n")
    assert len([linea for linea in lineas if "/confirmar" in linea]) == 3
    codigos = [_codigo(linea) for linea in lineas if "/confirmar" in linea]
    assert len(set(codigos)) == 3
    assert "Consejo del hogar sobre" in pendientes and "coste estimado" in pendientes

    # A code that was never issued, and a code issued to ANOTHER chat for the same item: nothing.
    falso = next(c for c in ("1111", "2222", "3333") if c not in codigos)
    assert montaje.decir(f"/confirmar {falso}") == [TEXTO_CODIGO_NO_VALE]
    ajeno = confirmaciones.emitir(montaje.almacen, otro, "accion", primera.id, datetime.now(UTC))
    if ajeno not in codigos:
        assert montaje.decir(f"/confirmar {ajeno}") == [TEXTO_CODIGO_NO_VALE]
    # An expired one: nothing.
    viejo = confirmaciones.emitir(
        montaje.almacen, vinculo, "accion", segunda.id, datetime.now(UTC) - timedelta(hours=2)
    )
    assert montaje.decir(f"/confirmar {viejo}") == [TEXTO_CODIGO_NO_VALE]
    assert llamadas.nombres() == [] and _estado_accion(montaje, primera.id) == "pendiente"

    # The right code of the first action confirms that action and nothing else.
    (pendientes,) = montaje.decir("/pendientes")  # fresh codes (the expired one voided the second action's)
    por_item = {linea: _codigo(linea) for linea in pendientes.split("\n") if "/confirmar" in linea}
    assert len(por_item) == 3
    codigo_primera = _codigo(pendientes)  # the newest action is listed first: that is the second one
    (texto,) = montaje.decir(f"/confirmar {codigo_primera}")
    assert texto.startswith("Hecho:") and llamadas.nombres() == ["agua_cambiar_tarifa"]
    estados = sorted(_estado_accion(montaje, a.id) for a in (primera, segunda))
    assert estados == ["ejecutada", "pendiente"]
    registro_sesion = servicio_para(montaje.db).obtener(sesion.id, case_id=CASE_ID)
    assert registro_sesion.sesion.estado == "pendiente_confirmacion"
    # Reused: nothing more runs.
    assert montaje.decir(f"/confirmar {codigo_primera}") == [TEXTO_CODIGO_NO_VALE]
    assert llamadas.nombres() == ["agua_cambiar_tarifa"]
    # The confirmation is audited (kind and item, no text).
    eventos = [e for e in montaje.casos.list_audit_events(CASE_ID) if e.get("accion") == "confirmacion"]
    assert len(eventos) == 1 and eventos[0]["tipo"] == "accion"
    assert any(e.get("event_type") == "accion_confirmada" for e in montaje.casos.list_audit_events(CASE_ID))


def test_rejecting_with_the_code_means_it_never_runs(montaje: Montaje, llamadas: Llamadas) -> None:
    montaje.emparejar()
    accion = AccionStore(montaje.db).add(_accion())
    (pendientes,) = montaje.decir("/pendientes")
    codigo = _codigo(pendientes)
    (texto,) = montaje.decir(f"/rechazar {codigo}")
    assert texto.startswith("Rechazada:")
    assert _estado_accion(montaje, accion.id) == "rechazada"
    (pendientes,) = montaje.decir("/pendientes")
    assert pendientes == "No tienes nada pendiente de confirmar."
    assert montaje.decir(f"/confirmar {codigo}") == [TEXTO_CODIGO_NO_VALE] and llamadas.nombres() == []


def test_the_council_is_convened_with_its_estimate_and_runs_only_after_its_code(montaje: Montaje) -> None:
    montaje.emparejar()
    (texto,) = montaje.decir(f"/consejo {DECISION}")
    assert "*el consejo no ha empezado*" in texto and "coste estimado" in texto and "minuto" in texto
    servicio = servicio_para(montaje.db)
    (registro_,) = servicio.listar(CASE_ID)
    assert registro_.sesion.estado == "pendiente_confirmacion" and registro_.extra.origen == "usuario"
    assert montaje.decir("sí, adelante") and servicio.listar(CASE_ID)[0].sesion.estado == "pendiente_confirmacion"

    (confirmado,) = montaje.decir(f"/confirmar {_codigo(texto)}")
    assert confirmado.startswith("Confirmado: el consejo empieza a estudiarlo")
    assert servicio.listar(CASE_ID)[0].sesion.estado == "en_cola"
    # Convening the same decision again does not create a second session.
    (otra_vez,) = montaje.decir(f"/consejo {DECISION}")
    assert "Ya hay una sesión del consejo" in otra_vez and len(servicio.listar(CASE_ID)) == 1


def test_a_too_short_council_question_is_refused(montaje: Montaje) -> None:
    montaje.emparejar()
    assert montaje.decir("/consejo") == [
        "Escribe la decisión después del comando, por ejemplo: /consejo ¿Amortizo la hipoteca o invierto?"
    ]
    assert montaje.decir("/consejo ¿Sí?") == ["Escribe la decisión que quieres que estudie el consejo."]


def test_kinds_left_out_of_the_setting_are_confirmed_in_the_app_only(
    montaje: Montaje, llamadas: Llamadas, monkeypatch: pytest.MonkeyPatch
) -> None:
    vinculo = montaje.emparejar()
    accion = AccionStore(montaje.db).add(_accion())
    antiguo = confirmaciones.emitir(montaje.almacen, vinculo, "accion", accion.id, datetime.now(UTC))
    monkeypatch.setattr(settings, "MENSAJERIA_CONFIRMAR_EN_CHAT", "consejo")

    (pendientes,) = montaje.decir("/pendientes")
    assert "Confírmalo o recházalo en la app («Módulos»)" in pendientes and "/confirmar" not in pendientes
    # A code shown before the operator changed the setting does not confirm either.
    assert montaje.decir(f"/confirmar {antiguo}") == ["Esto solo se puede confirmar en la app. No he hecho nada."]
    assert _estado_accion(montaje, accion.id) == "pendiente" and llamadas.nombres() == []


def test_a_confirmation_command_without_a_code_explains_how(montaje: Montaje) -> None:
    montaje.emparejar()
    assert montaje.decir("/confirmar") == [confirmaciones.TEXTO_SIN_CODIGO]
    assert montaje.decir("/confirmar abcd") == [TEXTO_CODIGO_NO_VALE]

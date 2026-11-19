"""The chat's commands: help, figures and sources of the last answer, state, pausing notices, unpairing."""

from __future__ import annotations

from typing import Any

import pytest

from asistente.plataforma.mensajeria import comandos
from asistente.plataforma.mensajeria.comandos import TEXTO_AYUDA, parsear
from asistente.plataforma.mensajeria.emparejamiento import TEXTO_PRIVADO
from asistente.plataforma.mensajeria.formato import a_canal
from asistente.plataforma.mensajeria.pasarela import TEXTO_DESPEDIDA, UltimaRespuesta
from tests.agent.fakes import text_turn, tool_turn, verdict
from tests.cerebro.fakes import Grabador, MemoriaFalsa, cerebro
from .conftest import CASE_ID, PREGUNTA_FISCAL, Montaje
from .test_formato import _informe

AYUDA = a_canal(TEXTO_AYUDA, "simplex")


def test_commands_are_parsed_from_the_users_own_slash_text() -> None:
    assert parsear("/ayuda") == comandos.Comando("ayuda", "")
    assert parsear("  /Confirmar   4821 ") == comandos.Comando("confirmar", "4821")
    assert parsear("/help@bot") == comandos.Comando("ayuda", "")
    assert parsear("/consejo ¿Amortizo o invierto?") == comandos.Comando("consejo", "¿Amortizo o invierto?")
    assert parsear("/xyz") == comandos.Comando("desconocido", "")
    for texto in ("hola /ayuda", "/", "/ 1234", "/12", "", "sí"):
        assert parsear(texto) is None


def test_help_and_unknown_commands_answer_with_the_help(montaje: Montaje) -> None:
    montaje.emparejar()
    assert montaje.decir("/ayuda") == [AYUDA]
    assert montaje.decir("/loquesea") == [AYUDA]
    for comando in ("/nueva", "/cifras", "/fuentes", "/pendientes", "/confirmar", "/rechazar", "/consejo", "/estado"):
        assert comando in AYUDA
    assert "*Lo que puedo hacer aquí*" in AYUDA and "/pausa" in AYUDA and "/desemparejar" in AYUDA
    assert montaje.agente.construido == 0  # commands never call the brain


def test_figures_and_sources_of_the_last_answer(montaje: Montaje) -> None:
    vinculo = montaje.emparejar()
    assert montaje.decir("/cifras")[0].startswith("Aún no tengo una respuesta reciente")
    fiscal = Grabador(
        [tool_turn(("buscar_fuentes", {"consulta": "plazo reinversión"})), text_turn("El plazo es de dos años [F2].")],
        [verdict("respuesta", ("Plazo dos años", ["F2"], "respaldada"))],
    )
    montaje.agente.agente = cerebro(fiscal=fiscal, memoria=MemoriaFalsa())
    montaje.decir(PREGUNTA_FISCAL)

    (fuentes,) = montaje.decir("/fuentes")
    assert fuentes.startswith("*Fuentes* de mi última respuesta:") and "• [F2] Consulta V0058-25" in fuentes
    assert montaje.decir("/cifras") == ["La última respuesta no tiene cifras calculadas que explicar."]
    # A synthesis with figures: each one with its origin (the figures gate's attributions).
    montaje.pasarela._memoria.fijar_ultima(vinculo.id, UltimaRespuesta(_informe(), []))
    (cifras,) = montaje.decir("/cifras")
    assert "• 812,40 € — Hipoteca: cuota mensual (cálculo `hipoteca_calcular`) [F3]" in cifras


def test_state_pause_and_resume(montaje: Montaje) -> None:
    vinculo = montaje.emparejar()
    (estado,) = montaje.decir("/estado")
    assert f"Vinculado a tu hogar desde el {vinculo.creado:%d/%m/%Y}." in estado
    assert "Avisos: activos." in estado and "cifrado de extremo a extremo" in estado
    assert "Nada pendiente de tu confirmación." in estado

    assert montaje.decir("/pausa")[0].startswith("Avisos en pausa")
    pausado = montaje.almacen.vinculo_de("prueba", "c1")
    assert pausado is not None and pausado.pausado
    assert "Avisos: en pausa" in montaje.decir("/estado")[0]
    assert montaje.decir("/reanudar")[0].startswith("Avisos reanudados")
    reanudado = montaje.almacen.vinculo_de("prueba", "c1")
    assert reanudado is not None and not reanudado.pausado


def test_the_quiet_hours_are_stated(montaje: Montaje, monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "MENSAJERIA_HORAS_SILENCIO", "22:00-08:00")
    montaje.emparejar()
    assert "Avisos: activos, salvo en las horas de silencio (22:00 a 08:00)." in montaje.decir("/estado")[0]


def test_unpairing_from_the_chat_needs_its_code_then_forgets_the_chat(montaje: Montaje) -> None:
    vinculo = montaje.emparejar()
    (aviso,) = montaje.decir("/desemparejar")
    assert "Para hacerlo escribe: /desemparejar " in aviso
    codigo = aviso.rsplit(" ", 1)[1]
    assert montaje.decir("/desemparejar 0000" if codigo != "0000" else "/desemparejar 1111")[0].startswith(
        "Ese código no vale"
    )
    assert montaje.almacen.vinculo_de("prueba", "c1") is not None
    # A confirmation code is not an unpairing code (and the other way round).
    assert montaje.decir(f"/confirmar {codigo}")[0].startswith("Ese código no vale")

    assert montaje.decir(f"/desemparejar {codigo}") == [a_canal(TEXTO_DESPEDIDA, "simplex")]
    assert montaje.almacen.vinculo_de("prueba", "c1") is None
    assert montaje.canal.desconectados == ["c1"]
    assert montaje.pasarela.ultima_respuesta(vinculo) is None
    eventos = [e for e in montaje.casos.list_audit_events(CASE_ID) if e.get("accion") == "desemparejado"]
    assert eventos and eventos[0]["motivo"] == "chat"
    # From now on it is a stranger.
    assert montaje.decir("Hola") == [a_canal(TEXTO_PRIVADO, "simplex")]


def test_when_the_channel_cannot_remove_the_contact_the_service_retries(montaje: Montaje) -> None:
    montaje.emparejar()
    montaje.canal.desconexion_falla = True
    codigo = montaje.decir("/desemparejar")[0].rsplit(" ", 1)[1]
    montaje.decir(f"/desemparejar {codigo}")
    assert montaje.almacen.vinculo_de("prueba", "c1") is None
    (pendiente,) = montaje.almacen.desconexiones()
    assert pendiente.motivo == "chat"
    montaje.canal.desconexion_falla = False
    assert montaje.pasarela.procesar_desconexiones() == 1 and montaje.canal.desconectados == ["c1"]


def test_a_failing_command_is_an_honest_message(montaje: Montaje, monkeypatch: pytest.MonkeyPatch) -> None:
    montaje.emparejar()

    def rompe(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(montaje.pasarela, "texto_estado", rompe)
    assert montaje.decir("/estado") == [
        "No he podido hacerlo por un error interno. No se ha cambiado nada; inténtalo de nuevo."
    ]

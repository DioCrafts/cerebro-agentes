"""Pairing a chat: a live one-time code pairs; expired, reused, guessed or forwarded codes never do; strangers get one
fixed sentence a day and nothing else (no brain, no cost, no data)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from asistente.plataforma import settings
from asistente.plataforma.mensajeria import emparejamiento
from asistente.plataforma.mensajeria.emparejamiento import (
    TEXTO_BIENVENIDA,
    TEXTO_CODIGO_INVALIDO,
    TEXTO_LIMITE,
    TEXTO_PRIVADO,
    codigos_en,
    generar_codigo,
    normalizar,
)
from tests.cerebro.fakes import Grabador, cerebro
from .conftest import CASE_ID, Montaje


def _render(texto: str) -> str:
    from asistente.plataforma.mensajeria.formato import a_canal

    return a_canal(texto, "simplex")


# --- the code itself ---------------------------------------------------------------------------------------------


def test_codes_have_high_entropy_and_are_never_mistaken_for_words() -> None:
    codigos = {generar_codigo() for _ in range(200)}
    assert len(codigos) == 200
    for codigo in codigos:
        assert len(codigo) == 14 and codigo[4] == codigo[9] == "-"
        assert normalizar(codigo) is not None
    # Typed loosely (lower case, spaces, Crockford's look-alikes) it is still the same code.
    codigo = "7K3M-9QXP-2D0R"
    assert normalizar("7k3m 9qxp 2dOr") == normalizar(codigo) == "7K3M9QXP2D0R"
    assert codigos_en(f"Hola! mi código: {codigo.lower()}, gracias") == ["7K3M9QXP2D0R"]
    # Not glued to a word (accented letters included), and overlapping runs are all seen.
    assert codigos_en(f"mi código {codigo}") == ["7K3M9QXP2D0R"]
    assert "7K3M9QXP2D0R" in codigos_en(f"Hola ABCD 7K3M {codigo}")
    assert codigos_en(f"x{codigo}") == []
    # Ordinary twelve-letter words are not codes (a code has at least two digits and two letters).
    assert codigos_en("electricidad y declaración") == []
    assert codigos_en("123456789012") == []


# --- pairing from the chat ------------------------------------------------------------------------------------------


def test_a_live_code_pairs_the_chat_to_its_owners_case_and_is_spent(montaje: Montaje) -> None:
    codigo = montaje.codigo()
    textos = montaje.decir(f"Hola, mi código es {codigo}", nombre_mostrado="  Ana   móvil ")

    assert textos == [_render(TEXTO_BIENVENIDA)]
    assert "tu hogar" in textos[0] and "/desemparejar" in textos[0] and "/ayuda" in textos[0]
    vinculo = montaje.almacen.vinculo_de("prueba", "c1")
    assert vinculo is not None and vinculo.case_id == CASE_ID and vinculo.user_id is None
    assert vinculo.nombre_mostrado == "Ana móvil" and vinculo.conversacion_id.startswith("msj_")
    # Audited on the case (no message text).
    (evento,) = [e for e in montaje.casos.list_audit_events(CASE_ID) if e.get("event_type") == "mensajeria"]
    assert evento["accion"] == "emparejado" and evento["canal"] == "prueba" and "texto" not in evento
    # Spent: another chat sending the same code gets nothing paired.
    assert montaje.decir(f"mi código {codigo}", contacto="c2") == [_render(TEXTO_CODIGO_INVALIDO)]
    assert montaje.almacen.vinculo_de("prueba", "c2") is None
    # The code is stored hashed only.
    import sqlite3

    with sqlite3.connect(montaje.db) as conn:
        volcado = "\n".join(conn.iterdump())
    assert normalizar(codigo) not in volcado and codigo not in volcado


def test_an_expired_code_does_not_pair(montaje: Montaje) -> None:
    hace = datetime.now(UTC) - timedelta(minutes=emparejamiento.minutos_codigo() + 1)
    viejo = emparejamiento.nuevo_codigo(montaje.almacen, canal="prueba", user_id=None, case_id=CASE_ID, momento=hace)
    assert montaje.decir(viejo.codigo) == [_render(TEXTO_CODIGO_INVALIDO)]
    assert montaje.almacen.vinculo_de("prueba", "c1") is None


def test_a_new_code_voids_the_previous_one(montaje: Montaje) -> None:
    primero = montaje.codigo()
    segundo = montaje.codigo()
    assert montaje.decir(primero) == [_render(TEXTO_CODIGO_INVALIDO)]
    assert montaje.decir(segundo) == [_render(TEXTO_BIENVENIDA)]


def test_a_forwarded_or_quoted_code_never_pairs(montaje: Montaje) -> None:
    codigo = montaje.codigo()
    assert montaje.decir(codigo, reenviado=True) == [_render(TEXTO_PRIVADO)]
    assert montaje.decir("mira esto", cita=codigo) == []  # the daily sentence was already said
    assert montaje.almacen.vinculo_de("prueba", "c1") is None
    # Typed by the user, it pairs (it was never spent).
    assert montaje.decir(codigo) == [_render(TEXTO_BIENVENIDA)]


def test_brute_force_stops_at_the_per_contact_limit_and_the_global_one(
    montaje: Montaje, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MENSAJERIA_INTENTOS_CONTACTO_HORA", 3)
    monkeypatch.setattr(settings, "MENSAJERIA_INTENTOS_GLOBAL_HORA", 5)
    for _ in range(3):
        assert montaje.decir(generar_codigo()) == [_render(TEXTO_CODIGO_INVALIDO)]
    # Past the per-contact limit, even the right code is not checked.
    bueno = montaje.codigo()
    assert montaje.decir(bueno) == [_render(TEXTO_PRIVADO)]  # the daily sentence, once
    assert montaje.decir(bueno) == []
    assert montaje.almacen.vinculo_de("prueba", "c1") is None
    # The global limit: two more failures from another contact exhaust it for everybody.
    for _ in range(2):
        assert montaje.decir(generar_codigo(), contacto="c2") == [_render(TEXTO_CODIGO_INVALIDO)]
    assert montaje.decir(bueno, contacto="c3") == [_render(TEXTO_PRIVADO)]
    assert montaje.almacen.vinculo_de("prueba", "c3") is None


def test_a_stranger_gets_one_fixed_sentence_a_day_and_the_brain_is_never_called(montaje: Montaje) -> None:
    sintesis = Grabador(nombre="sintesis")
    montaje.agente.agente = cerebro(sintesis=sintesis)
    adjunto = montaje.canal.adjunto(_tmp(montaje), "factura.pdf", b"%PDF-1.4 datos")
    primero = montaje.decir("Hola, ¿cuánto pago de IRPF?", adjuntos=[adjunto])
    assert primero == [_render(TEXTO_PRIVADO)]
    assert montaje.decir("¿Hola? Dime algo de mi hipoteca") == []
    assert montaje.decir("/ayuda") == []  # not even the commands
    # Nothing ran: no agent built, no model called; the stranger's file was deleted unread.
    assert montaje.agente.construido == 0 and sintesis.llamadas == 0
    from pathlib import Path

    assert not Path(adjunto.ruta_local).exists()
    # Nothing about them stored but hashes (no contact id, no text).
    import sqlite3

    with sqlite3.connect(montaje.db) as conn:
        volcado = "\n".join(conn.iterdump())
    assert "IRPF" not in volcado and "'c1'" not in volcado


def test_the_owner_pairs_at_most_the_configured_number_of_chats(montaje: Montaje) -> None:
    montaje.emparejar("c1")
    codigo = montaje.codigo()
    assert montaje.decir(codigo, contacto="c2") == [_render(TEXTO_LIMITE)]
    assert montaje.almacen.vinculo_de("prueba", "c2") is None
    # The code was not spent: once the first chat is unpaired, it pairs the second.
    vinculo = montaje.almacen.vinculo_de("prueba", "c1")
    assert vinculo is not None
    montaje.almacen.desvincular(vinculo.id, motivo="app", encolar=False, momento=datetime.now(UTC))
    assert montaje.decir(codigo, contacto="c2") == [_render(TEXTO_BIENVENIDA)]


def test_a_redelivered_pairing_message_runs_once(montaje: Montaje) -> None:
    codigo = montaje.codigo()
    mensaje = montaje.canal.mensaje("c1", codigo, mensaje_id="m-1")
    montaje.canal.entregar(mensaje)
    montaje.canal.entregar(mensaje)
    assert montaje.pasarela.esperar(10)
    assert montaje.canal.textos("c1") == [_render(TEXTO_BIENVENIDA)]


def _tmp(montaje: Montaje):  # the adapter's private directory, next to the database
    from pathlib import Path

    carpeta = Path(montaje.db).parent / "adjuntos"
    carpeta.mkdir(exist_ok=True)
    return carpeta

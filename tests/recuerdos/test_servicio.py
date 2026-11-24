"""The conversation memory on Hindsight: what is retained, what the brain recalls, and what the user can undo."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from asistente.nucleo.cerebro.contrato import TurnoConversacion
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.recuerdos import daemon
from asistente.nucleo.recuerdos.cliente import HindsightError
from asistente.nucleo.recuerdos.servicio import (
    CorreccionInvalidaError,
    ServicioMemoria,
    Titular,
    banco_de,
    borrar_de_usuario,
    exportar_recuerdos,
    preparar,
    servicio,
    servicio_memoria,
)
from asistente.plataforma import settings
from tests.recuerdos.falso import HindsightFalso

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
ANA = Titular.de("caso-ana", "ana")


@pytest.fixture
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "MEMORIA_CONVERSACIONES", True)
    path = str(tmp_path / "estado.sqlite3")
    SQLiteCaseStateStore(path).close()
    return path


@pytest.fixture
def memoria(db: str) -> ServicioMemoria:
    return servicio(db)


def _turno(texto: str, *, conversacion: str = "conv-ana-0001", titular: Titular = ANA, n: int = 0) -> TurnoConversacion:
    return TurnoConversacion(
        conversacion_id=conversacion,
        case_id=titular.case_id or None,
        user_id=titular.user_id or None,
        fecha=T0.replace(minute=n),
        pregunta=texto,
        respuesta="Respuesta del asistente con 4.000 euros que nunca debe guardarse.",
    )


# -- what is retained ------------------------------------------------------------------------------------------


def test_only_the_users_words_are_retained_masked_in_the_owners_own_bank(
    memoria: ServicioMemoria, hindsight: HindsightFalso
) -> None:
    memoria.registrar_turno(_turno("Cobro la nómina en ING, IBAN ES9121000418450200051332, unos 2.300 € al mes"))
    [texto] = hindsight.textos(ANA.banco)
    assert "IBAN terminado en 1332" in texto and "ES9121000418450200051332" not in texto
    assert "2.300" in texto
    assert all("Respuesta del asistente" not in t for b in hindsight.bancos for t in hindsight.textos(b))
    banco = hindsight.bancos[ANA.banco]
    assert banco.config["memory_defense"]["enabled"] is True
    assert banco.perfil["enable_observations"] is True
    [doc] = banco.documentos.values()
    assert doc["tags"] == ["conversacion:conv-ana-0001"]
    assert doc["document_metadata"] == {"conversacion_id": "conv-ana-0001"}


def test_the_bank_name_reveals_neither_the_case_nor_the_account() -> None:
    banco = banco_de("caso-ana", "ana")
    assert banco.startswith("cerebro-") and "ana" not in banco
    assert banco != banco_de("caso-ana", None) != banco_de(None, "ana")


@pytest.mark.parametrize(
    "texto",
    [
        "mi contraseña es Hx7!pq2z",  # nothing but a credential
        "ok",  # too short to carry a fact
        "Tengo diabetes desde hace dos años",  # special category, not asked to remember
    ],
)
def test_some_messages_are_never_retained(texto: str) -> None:
    assert preparar(texto) is None


def test_special_category_data_is_kept_when_the_user_asks_for_it() -> None:
    assert preparar("Recuerda que tengo diabetes desde hace dos años") is not None


def test_pasted_text_is_not_the_users_words() -> None:
    limpio = preparar("¿Qué me dices de esto?\n> Su tarifa sube a 0,19 €/kWh desde enero\n> Atentamente, la eléctrica")
    assert limpio is not None and "0,19" not in limpio


def test_a_paused_or_switched_off_memory_retains_and_recalls_nothing(
    memoria: ServicioMemoria, hindsight: HindsightFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    memoria.fijar_activa(ANA, False)
    memoria.registrar_turno(_turno("Vivo en Sevilla con mi pareja y dos hijos"))
    assert hindsight.textos(ANA.banco) == []
    assert memoria.recuperar(case_id=ANA.case_id, user_id=ANA.user_id, consulta="Sevilla").vacio_por == "desactivada"
    memoria.fijar_activa(ANA, True)
    monkeypatch.setattr(settings, "MEMORIA_CONVERSACIONES", False)
    memoria.registrar_turno(_turno("Vivo en Sevilla con mi pareja y dos hijos"))
    assert hindsight.textos(ANA.banco) == []
    assert type(servicio_memoria()).__name__ == "MemoriaNula"


# -- what the brain recalls ------------------------------------------------------------------------------------


def test_recall_returns_the_owners_facts_with_the_users_words_as_quote(memoria: ServicioMemoria) -> None:
    memoria.registrar_turno(_turno("Tengo la hipoteca con ING a tipo variable, me quedan 92.000 euros"))
    memoria.registrar_turno(
        _turno("Vivo de alquiler en Valencia", conversacion="conv-beto-001", titular=Titular.de("caso-b", "beto"))
    )
    bloque = memoria.recuperar(
        case_id=ANA.case_id, user_id=ANA.user_id, consulta="¿Cuánto me queda de hipoteca?", conversacion_id="otra-conv"
    )
    [recuerdo] = bloque.recuerdos
    assert recuerdo.tipo == "hecho" and "92.000" in recuerdo.texto
    assert recuerdo.origen.cita and "92.000" in recuerdo.origen.cita
    assert recuerdo.origen.conversacion_id == "conv-ana-0001" and recuerdo.origen.fecha == T0.date()
    assert "Valencia" not in bloque.como_contexto()


def test_recall_skips_the_current_conversation(memoria: ServicioMemoria) -> None:
    memoria.registrar_turno(_turno("Tengo la hipoteca con ING a tipo variable"))
    bloque = memoria.recuperar(
        case_id=ANA.case_id, user_id=ANA.user_id, consulta="hipoteca variable", conversacion_id="conv-ana-0001"
    )
    assert bloque.recuerdos == [] and bloque.vacio_por == "sin_resultados"


def test_recall_never_raises_and_never_waits_for_the_daemon(
    db: str, hindsight: HindsightFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    arranques: list[bool] = []
    monkeypatch.setattr(daemon, "calentar", lambda: arranques.append(True))
    sin_arrancar = ServicioMemoria(db, fabrica_cliente=hindsight.cliente, listo=lambda: False)
    bloque = sin_arrancar.recuperar(case_id=ANA.case_id, user_id=ANA.user_id, consulta="hipoteca")
    assert bloque.vacio_por == "no_disponible" and arranques == [True]
    hindsight.caido = True
    caido = ServicioMemoria(db, fabrica_cliente=hindsight.cliente, listo=lambda: True)
    assert caido.recuperar(case_id=ANA.case_id, user_id=ANA.user_id, consulta="hipoteca").vacio_por == "no_disponible"
    caido.registrar_turno(_turno("Tengo la hipoteca con ING a tipo variable"))  # logged, not raised


def test_without_case_or_account_there_is_no_memory(memoria: ServicioMemoria) -> None:
    assert memoria.recuperar(case_id=None, user_id=None, consulta="x").vacio_por == "sin_cuenta"


# -- what the user can do ----------------------------------------------------------------------------------------


def test_a_correction_is_retained_as_the_users_words_and_the_old_fact_goes(
    memoria: ServicioMemoria, hindsight: HindsightFalso
) -> None:
    memoria.registrar_turno(_turno("Tengo la hipoteca en ING desde 2019"))
    [viejo] = memoria.listar(ANA)
    [nuevo] = memoria.corregir(ANA, viejo.id, texto="Tengo la hipoteca en Openbank desde 2023") or []
    assert nuevo.corregido and "Openbank" in nuevo.texto and nuevo.conversacion_id == "conv-ana-0001"
    assert [r.texto for r in memoria.listar(ANA)] == ["Tengo la hipoteca en Openbank desde 2023"]
    assert memoria.corregir(ANA, "no-existe", texto="algo bastante largo") is None
    with pytest.raises(CorreccionInvalidaError):
        memoria.corregir(ANA, nuevo.id, texto="mi PIN es 4821")


def test_forgetting_an_item_deletes_the_message_it_came_from(memoria: ServicioMemoria, hindsight) -> None:
    memoria.registrar_turno(_turno("Tengo la hipoteca en ING desde 2019", n=1))
    memoria.registrar_turno(_turno("Mi coche es un Seat Ibiza de 2018", n=2))
    hipoteca = next(r for r in memoria.listar(ANA) if "ING" in r.texto)
    assert memoria.olvidar(ANA, hipoteca.id)
    assert [r.texto for r in memoria.listar(ANA)] == ["Mi coche es un Seat Ibiza de 2018"]
    assert all("ING" not in t for t in hindsight.textos(ANA.banco))
    assert not memoria.olvidar(ANA, hipoteca.id)


def test_conversations_are_rebuilt_from_the_users_messages(memoria: ServicioMemoria) -> None:
    memoria.registrar_turno(_turno("Quiero cambiar de tarifa de luz este invierno", n=1))
    memoria.registrar_turno(_turno("Mi contrato actual es con Iberdrola", n=2))
    memoria.registrar_turno(_turno("Busco piso en Getafe de tres habitaciones", conversacion="conv-ana-0002", n=3))
    conversaciones = memoria.conversaciones(ANA)
    assert [c.id for c in conversaciones] == ["conv-ana-0002", "conv-ana-0001"]
    titulos = memoria.titulos(ANA, conversaciones)
    assert titulos["conv-ana-0001"] == "Quiero cambiar de tarifa de luz este invierno"
    conversacion, recuerdos = memoria.conversacion(ANA, "conv-ana-0001") or (None, [])
    assert (
        conversacion is not None
        and [m.texto for m in conversacion.mensajes][1] == "Mi contrato actual es con Iberdrola"
    )
    assert len(recuerdos) == 2
    assert memoria.borrar_conversacion(ANA, "conv-ana-0001")
    assert [c.id for c in memoria.conversaciones(ANA)] == ["conv-ana-0002"]
    assert memoria.conversacion(ANA, "conv-ana-0001") is None


def test_forget_everything_deletes_the_bank(memoria: ServicioMemoria, hindsight: HindsightFalso) -> None:
    memoria.registrar_turno(_turno("Tengo la hipoteca en ING desde 2019"))
    assert memoria.olvidar_todo(ANA) == {"recuerdos": 1, "conversaciones": 1}
    assert ANA.banco not in hindsight.bancos
    memoria.registrar_turno(_turno("Ahora vivo en Madrid, en Chamberí"))  # the bank is created again
    assert hindsight.textos(ANA.banco) == ["Ahora vivo en Madrid, en Chamberí"]


def test_the_export_and_the_account_erasure_cover_every_bank_of_the_account(
    db: str, memoria: ServicioMemoria, hindsight: HindsightFalso
) -> None:
    sin_caso = Titular.de(None, "ana")
    memoria.registrar_turno(_turno("Tengo la hipoteca en ING desde 2019"))
    memoria.registrar_turno(_turno("Prefiero respuestas cortas y sin tecnicismos", titular=sin_caso))
    memoria.fijar_activa(sin_caso, False)
    exportado = exportar_recuerdos(db, case_id=ANA.case_id, user_id="ana")
    assert exportado["motor"] == "hindsight" and exportado["pausada"] is False
    assert [r["texto"] for r in exportado["recuerdos"]] == ["Tengo la hipoteca en ING desde 2019"]
    assert exportado["conversaciones"][0]["mensajes"][0]["texto"] == "Tengo la hipoteca en ING desde 2019"
    assert exportar_recuerdos(db, case_id=None, user_id="ana")["pausada"] is True
    assert borrar_de_usuario(db, user_id="ana", case_ids=[ANA.case_id, "caso-vacio"]) == 3
    assert ANA.banco not in hindsight.bancos and sin_caso.banco not in hindsight.bancos
    assert memoria.activa(sin_caso)


def test_the_account_erasure_stops_when_hindsight_is_down(db: str, hindsight: HindsightFalso) -> None:
    hindsight.caido = True
    with pytest.raises(HindsightError):
        borrar_de_usuario(db, user_id="ana", case_ids=[])


def test_without_the_models_key_the_daemon_is_not_even_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "HINDSIGHT_URL", "")
    monkeypatch.setattr(settings, "HINDSIGHT_LLM_PROVEEDOR", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HINDSIGHT_API_LLM_API_KEY", raising=False)
    assert not daemon.tiene_clave()
    daemon.calentar()  # returns at once, no thread
    assert daemon._calentando is None
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        daemon.url()


def test_the_key_reaches_the_daemon_through_the_environment_never_its_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-prueba")
    monkeypatch.delenv("HINDSIGHT_API_LLM_API_KEY", raising=False)
    daemon._heredar_clave()
    import os

    assert os.environ["HINDSIGHT_API_LLM_API_KEY"] == "sk-ant-prueba"
    assert all("KEY" not in k for k in daemon.configuracion_daemon())
    assert daemon.configuracion_daemon()["HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE"] == "spanish"


def test_the_users_requests_never_wait_for_the_daemon_to_start(
    db: str, hindsight: HindsightFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon, "calentar", lambda: None)
    arrancando = ServicioMemoria(db, fabrica_cliente=hindsight.cliente, listo=lambda: False)
    with pytest.raises(HindsightError, match="todavía no está listo"):
        arrancando.listar(ANA)
    arrancando.registrar_turno(_turno("Tengo la hipoteca con ING a tipo variable"))  # not retained, not raised
    assert hindsight.textos(ANA.banco) == []


def test_the_export_still_goes_out_when_hindsight_is_down(db: str, hindsight: HindsightFalso) -> None:
    hindsight.caido = True
    exportado = exportar_recuerdos(db, case_id=ANA.case_id, user_id="ana")
    assert "error" in exportado and "recuerdos" not in exportado

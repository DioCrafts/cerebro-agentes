"""The central agent with the user's decisions (scripted provider: no network, no model)."""

from __future__ import annotations

from hacienda_gpt.agent.agent import TaxAgent
from hacienda_gpt.memoria.almacen import tabla_existe
from hacienda_gpt.memoria.servicio import decisiones_del_caso, registrar
from hacienda_gpt.modulos.fiscal import MODULO as FISCAL
from tests.agent.fakes import FakeSearcher, corpus, text_turn, tool_turn, verdict
from tests.modulos_nucleo.fakes import Llamadas, agua, instalar, seguros
from tests.modulos_nucleo.test_especialistas import RecordingProvider
from tests.proactive import make_case

PEDIDO = "No quiero planes de pensiones porque necesito liquidez. ¿Qué más puedo hacer antes de fin de año?"


def _agente(provider) -> TaxAgent:
    return TaxAgent(provider=provider, searcher=FakeSearcher(corpus()))


def _resultado(provider, indice: int = 0) -> str:
    return provider.tool_results[indice].content


def test_the_tool_records_what_the_user_said_with_their_words(db) -> None:
    provider = RecordingProvider(
        [
            tool_turn(
                (
                    "registrar_decision",
                    {
                        "ambito": "fiscal:plan_pensiones",
                        "tipo": "rechazo",
                        "cita_del_usuario": "no quiero planes de pensiones porque necesito liquidez",
                        "razon": "necesito liquidez",
                    },
                )
            ),
            text_turn("Anotado: no te propondré planes de pensiones."),
        ],
        [verdict("pregunta_aclaratoria")],
    )
    _agente(provider).run(PEDIDO, case=make_case("c1"))

    assert not provider.tool_results[0].is_error, _resultado(provider)
    assert "Guardado" in _resultado(provider) and "16/11/2027" in _resultado(provider)
    (decision,) = decisiones_del_caso(db, "c1")
    assert (decision.ambito, decision.tipo, decision.fuente, decision.razon) == (
        "fiscal:plan_pensiones",
        "rechazo",
        "chat",
        "necesito liquidez",
    )
    assert decision.user_id == "user-c1"


def test_the_tool_refuses_a_decision_the_user_did_not_express(db) -> None:
    provider = RecordingProvider(
        [
            tool_turn(
                # The model infers a preference from the user's situation: never stored.
                (
                    "registrar_decision",
                    {
                        "ambito": "fiscal:plan_pensiones",
                        "tipo": "rechazo",
                        "cita_del_usuario": "no quiero planes de pensiones",
                    },
                ),
                # The words are the user's, the reason is the model's: refused too.
                (
                    "registrar_decision",
                    {
                        "ambito": "fiscal:donativo",
                        "tipo": "rechazo",
                        "cita_del_usuario": "gano 45.000 euros",
                        "razon": "no le interesan los donativos",
                    },
                ),
                # Too short to be a decision.
                ("registrar_decision", {"ambito": "fiscal:donativo", "tipo": "rechazo", "cita_del_usuario": "gano"}),
            ),
            text_turn("¿Quieres que tenga en cuenta algo?"),
        ],
        [verdict("pregunta_aclaratoria")],
    )
    _agente(provider).run(
        "Gano 45.000 euros al año, ¿qué hago antes de fin de año?",
        chat_history=[{"role": "assistant", "content": "¿No quiero planes de pensiones, dices?"}],
        case=make_case("c1"),
    )

    assert [r.is_error for r in provider.tool_results] == [True, True, True]
    assert "literalmente" in _resultado(provider, 0) and "no las deduzcas" in _resultado(provider, 0)
    assert decisiones_del_caso(db, "c1") == []


def test_words_from_an_earlier_user_turn_count(db) -> None:
    provider = RecordingProvider(
        [
            tool_turn(
                (
                    "registrar_decision",
                    {
                        "ambito": "fiscal:tributacion:conjunta",
                        "tipo": "preferencia",
                        "cita_del_usuario": "Prefiero la declaración CONJUNTA",
                    },
                )
            ),
            text_turn("Hecho."),
        ],
        [verdict("pregunta_aclaratoria")],
    )
    _agente(provider).run(
        "Apúntalo, por favor.",
        chat_history=[{"role": "user", "content": "prefiero la declaracion conjunta, siempre."}],
        case=make_case("c1"),
    )
    assert not provider.tool_results[0].is_error
    (decision,) = decisiones_del_caso(db, "c1")
    assert decision.tipo == "preferencia" and decision.caducidad is None


def test_the_agent_gets_the_decisions_in_its_context_and_the_rule_in_its_prompt(db) -> None:
    registrar(
        db, case_id="c1", ambito="fiscal:plan_pensiones", tipo="rechazo", razon="necesito liquidez", fuente="chat"
    )
    registrar(db, case_id="otro", ambito="fiscal:donativo", tipo="rechazo", razon="lo de otro usuario", fuente="chat")
    provider = RecordingProvider([text_turn("¿En qué comunidad vives?")], [verdict("pregunta_aclaratoria")])
    _agente(provider).run("¿Cómo pago menos?", case=make_case("c1"))
    (inicio,) = provider.starts

    mensaje = inicio["user_message"]
    assert "<preferencias_del_usuario>" in mensaje and "«necesito liquidez»" in mensaje
    assert "`fiscal:plan_pensiones`" in mensaje and "vigente" in mensaje
    assert "lo de otro usuario" not in mensaje
    assert mensaje.endswith("¿Cómo pago menos?")
    assert "No vuelvas a proponer lo que el usuario rechazó" in inicio["system"]
    assert "Nunca la deduzcas" in inicio["system"]
    assert {"registrar_decision", "leer_preferencias"} <= {t.name for t in inicio["tools"]}


def test_without_a_case_there_are_no_decision_tools_and_nothing_is_created(tmp_path, monkeypatch) -> None:
    from hacienda_gpt import settings

    path = str(tmp_path / "nada.sqlite3")
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", path)
    provider = RecordingProvider([text_turn("Hola.")], [verdict("abstencion")])
    _agente(provider).run("Hola")
    assert not {"registrar_decision", "leer_preferencias"} & {t.name for t in provider.starts[0]["tools"]}
    # With a case but no decisions the context is unchanged and the database is never created.
    provider = RecordingProvider([text_turn("Hola.")], [verdict("abstencion")])
    _agente(provider).run("Hola", case=make_case("c1"))
    assert "<preferencias_del_usuario>" not in provider.starts[0]["user_message"]
    assert not tabla_existe(path)


def test_leer_preferencias_explains_why_a_notice_was_not_sent(db) -> None:
    registrar(
        db, case_id="c1", ambito="luz:cambio_tarifa", tipo="rechazo", razon="permanencia hasta 03/2027", fuente="boton"
    )
    provider = RecordingProvider(
        [tool_turn(("leer_preferencias", {})), text_turn("No te aviso porque me dijiste que tienes permanencia.")],
        [verdict("pregunta_aclaratoria")],
    )
    _agente(provider).run("¿Por qué ya no me avisas de las tarifas de luz?", case=make_case("c1"))
    contenido = _resultado(provider)
    assert '"ambito": "luz:cambio_tarifa"' in contenido and '"estado": "vigente"' in contenido
    assert "permanencia hasta 03/2027" in contenido and '"user_id"' not in contenido


def test_a_specialist_sees_only_its_own_modules_decisions(db) -> None:
    llamadas = Llamadas()
    modulos = (agua(llamadas), seguros(llamadas))
    instalar(*modulos)
    registrar(db, case_id="c1", ambito="agua:tarifa", tipo="rechazo", razon="no cambio el agua", fuente="chat")
    registrar(db, case_id="c1", ambito="seguros:renovacion", tipo="rechazo", razon="lo del seguro", fuente="chat")
    registrar(db, case_id="c1", ambito="fiscal:donativo", tipo="rechazo", razon="lo fiscal", fuente="chat")
    provider = RecordingProvider(
        [
            tool_turn(("consultar_agua", {"pregunta": "¿Me conviene otra tarifa de agua?"})),
            text_turn("Informe: el usuario no quiere cambiar de tarifa."),
            text_turn("No te propongo cambiar de tarifa, como me pediste."),
        ],
        [verdict("pregunta_aclaratoria")],
    )
    _agente(provider).run("¿Y el agua?", case=make_case("c1"), modulos=[FISCAL, *modulos])

    principal, especialista = provider.starts
    assert "no cambio el agua" in especialista["user_message"]
    assert "<decisiones_del_usuario_en_tu_ambito>" in especialista["user_message"]
    assert "lo del seguro" not in especialista["user_message"] and "lo fiscal" not in especialista["user_message"]
    # The central agent sees all of them.
    assert all(t in principal["user_message"] for t in ("no cambio el agua", "lo del seguro", "lo fiscal"))

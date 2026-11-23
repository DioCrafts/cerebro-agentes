"""The household brain end to end (asistente/nucleo/cerebro/cerebro.py), every model scripted per purpose.

Routes: paso directo (the tax advisor's answer as it is), the brain alone (small talk, memory, a clarification, a
switched-off module), one household specialist, several in parallel, a plan with a dependency, replanning; and the
gates: the figures gate (revision, then an honest partial answer), the mode cap, the budget.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from asistente.nucleo.agent import agent as agent_mod
from asistente.nucleo.agent.agent import AgentMode, TaxAgent, TeamAgent
from asistente.nucleo.agent.providers.base import ModelTurn, ProviderError
from asistente.nucleo.cerebro import cerebro as cerebro_mod
from asistente.nucleo.cerebro.cerebro import MARCA_RETIRADA, Cerebro, tope_de_informes
from asistente.nucleo.cerebro.contrato import (
    EVENTO_ESPECIALISTA,
    EVENTO_PLAN,
    EVENTO_PUERTA,
    EVENTO_SINTESIS,
    InformeEspecialista,
    Intencion,
)
from asistente.plataforma import settings
from asistente.plataforma.ops.consumo import PresupuestoAgotadoError
from tests.agent.fakes import FakeSearcher, corpus, text_turn, tool_turn, verdict
from tests.cerebro.fakes import (
    HIPOTECA,
    LUZ,
    EspecialistaFalso,
    Grabador,
    MemoriaFalsa,
    caso,
    cerebro,
    eventos,
    modulos,
)

PREGUNTA_FISCAL = "¿Qué plazo tengo para reinvertir en otra vivienda habitual sin tributar en el IRPF?"


def _hipoteca(**kw: Any) -> EspecialistaFalso:
    return EspecialistaFalso(
        "hipoteca",
        "Hipoteca",
        calculo="Amortizando 10.000 € ahorras 1.200,00 € al año en intereses.",
        cifras=[("ahorro anual en intereses", "1200.00", "EUR/año"), ("importe amortizado", "10000", "EUR")],
        **kw,
    )


def _luz(**kw: Any) -> EspecialistaFalso:
    return EspecialistaFalso(
        "luz",
        "Luz",
        calculo="Con el PVPC pagarías 41,20 € al mes.",
        cifras=[("pago mensual con el PVPC", "41.20", "EUR/mes")],
        **kw,
    )


def _fid(e: EspecialistaFalso) -> str:
    """The fragment id the fake specialist registered (read when the scripted turn is due)."""
    contexto = e.contextos[0]
    return next(f.fragment_id for f in contexto.turno.ledger.all() if f.title.startswith(e.nombre))


def _plan(*encargos: tuple[str, str, list[str]], tipo: str = "cruzada", confianza: float = 0.9) -> dict[str, Any]:
    return {
        "tipo": tipo,
        "encargos": [
            {"id": f"e{i}", "especialista": c, "pregunta": p, "depende_de": d, "motivo": "plan"}
            for i, (c, p, d) in enumerate(encargos, start=1)
        ],
        "necesita_memoria": False,
        "confianza": confianza,
        "resumen": "Consulto a varios especialistas",
        "aclaracion": "",
    }


# --------------------------------------------------------------------------------------------------------------
# Paso directo: the tax advisor's own answer
# --------------------------------------------------------------------------------------------------------------


def _guion_fiscal() -> tuple[list[Any], list[Any]]:
    return (
        [
            tool_turn(("buscar_fuentes", {"consulta": "plazo reinversión vivienda habitual"})),
            text_turn("El plazo es de dos años [F2]."),
        ],
        [verdict("respuesta", ("Plazo dos años", ["F2"], "respaldada"))],
    )


def test_paso_directo_is_the_advisors_answer_with_no_extra_model_call() -> None:
    turnos, juicios = _guion_fiscal()
    fiscal = Grabador(turnos, juicios)
    intencion, sintesis, verificador = Grabador(), Grabador(), Grabador()
    brain = cerebro(fiscal=fiscal, intencion=intencion, sintesis=sintesis, verificador=verificador)
    recibidos, on_event = eventos()

    resultado = brain.run(PREGUNTA_FISCAL, on_event=on_event, modulos=modulos(HIPOTECA, LUZ))

    turnos, juicios = _guion_fiscal()
    legado = TaxAgent(provider=Grabador(turnos, juicios), searcher=FakeSearcher(corpus())).run(PREGUNTA_FISCAL)
    assert resultado.answer == legado.answer == "El plazo es de dos años [F2]."
    assert resultado.mode is legado.mode is AgentMode.VERIFIED
    assert resultado.claims == legado.claims and resultado.citations == legado.citations
    # Zero extra model calls: no understanding model, no synthesis, no second verifier.
    assert intencion.llamadas == sintesis.llamadas == verificador.llamadas == 0
    assert fiscal.llamadas == 3  # search, answer, its own verifier
    assert resultado.cerebro is not None and resultado.cerebro.ruta == "paso_directo"
    assert [i.especialista for i in resultado.cerebro.informes] == ["fiscal"]
    assert resultado.cerebro.informes[0].modo == "verified" and resultado.cerebro.intencion.origen == "reglas"
    # The advisor runs without the household tools (the brain coordinates them) and with its own.
    herramientas = {t.name for t in fiscal.inicios[0]["tools"]}
    assert "buscar_fuentes" in herramientas and "calcular_irpf" in herramientas
    assert not herramientas & {"consultar_hipoteca", "consultar_luz", "crear_tarea", "planificar_hogar", "pedir_dato"}
    assert fiscal.inicios[0]["user_message"].endswith(PREGUNTA_FISCAL)
    # Its events stream as they always did (the draft included), framed by the brain's plan.
    tipos = [e["type"] for e in recibidos]
    assert tipos[0] == EVENTO_PLAN and "text_delta" in tipos and "verifying" in tipos
    assert [e["estado"] for e in recibidos if e["type"] == EVENTO_ESPECIALISTA] == ["start", "done"]


def test_paso_directo_propagates_a_provider_failure_like_the_advisor() -> None:
    class Caido(Grabador):
        def start(self, **kwargs: Any):  # type: ignore[override]
            raise ProviderError("caído")

    caido = cerebro(fiscal=Caido())
    with pytest.raises(ProviderError):
        caido.run(PREGUNTA_FISCAL, modulos=modulos())


# --------------------------------------------------------------------------------------------------------------
# The brain alone
# --------------------------------------------------------------------------------------------------------------


def test_small_talk_is_one_call_without_tools() -> None:
    sintesis = Grabador([text_turn("¡Hola! ¿En qué te ayudo con tu casa?")])
    intencion, fiscal = Grabador(), Grabador()
    resultado = cerebro(sintesis=sintesis, intencion=intencion, fiscal=fiscal, especialistas={"luz": _luz()}).run(
        "Hola", modulos=modulos(LUZ)
    )
    assert resultado.answer.startswith("¡Hola!") and resultado.mode is AgentMode.UNVERIFIED
    assert sintesis.llamadas == 1 and sintesis.inicios[0]["tools"] == [] and sintesis.allow_tools_log == [False]
    assert intencion.llamadas == fiscal.llamadas == 0
    assert resultado.cerebro is not None and resultado.cerebro.ruta == "directa"
    assert resultado.cerebro.intencion.tipo == "charla" and resultado.warnings == []
    # The brain's identity, not the tax advisor's.
    assert "asistente del hogar" in sintesis.inicios[0]["system"] and "Luz" in sintesis.inicios[0]["system"]


def test_a_clarification_costs_no_model_call() -> None:
    sintesis, intencion = Grabador(), Grabador([], [ProviderError("caído")])
    resultado = cerebro(sintesis=sintesis, intencion=intencion).run("¿Cuánto me queda?", modulos=modulos(HIPOTECA))
    assert resultado.mode is AgentMode.CLARIFICATION and resultado.answer.startswith("¿Me lo concretas")
    assert sintesis.llamadas == 0 and intencion.llamadas == 1
    assert resultado.cerebro is not None and resultado.cerebro.ruta == "directa"
    assert resultado.cerebro.intencion.aclaracion == resultado.answer


def test_a_switched_off_module_is_said_without_a_model() -> None:
    sintesis = Grabador()
    resultado = cerebro(sintesis=sintesis).run("¿Qué tarifa de luz me conviene?", modulos=modulos(HIPOTECA))
    assert "Luz" in resultado.answer and "Módulos" in resultado.answer
    assert resultado.mode is AgentMode.ABSTAINED and sintesis.llamadas == 0


def test_a_memory_question_reads_the_memory_block() -> None:
    memoria = MemoriaFalsa(["Tiene una hipoteca variable con el Banco X"])
    sintesis = Grabador([text_turn("Me dijiste que tu hipoteca es variable, con el Banco X (el 01/10/2026).")])
    resultado = cerebro(sintesis=sintesis, memoria=memoria).run(
        "¿Qué te dije de mi hipoteca?", case=caso(), modulos=modulos(HIPOTECA), conversacion_id="conv-12345678"
    )
    assert "<memoria_del_usuario>" in sintesis.inicios[0]["user_message"]
    assert "Banco X" in sintesis.inicios[0]["user_message"]
    assert memoria.consultas == [
        {
            "case_id": "case-cerebro",
            "consulta": "¿Qué te dije de mi hipoteca?",
            "conversacion_id": "conv-12345678",
            "user_id": "u-cerebro",  # without a signed-in account: the case's owner
        }
    ]
    assert resultado.cerebro is not None and resultado.cerebro.recuerdos_usados == ["R1"]
    assert resultado.cerebro.conversacion_id == "conv-12345678" and resultado.cerebro.intencion.tipo == "memoria"


def test_a_broken_memory_never_fails_the_turn() -> None:
    sintesis = Grabador([text_turn("¡Hola!")])
    resultado = cerebro(sintesis=sintesis, memoria=MemoriaFalsa(falla=True)).run("Hola", modulos=modulos())
    assert resultado.answer == "¡Hola!" and resultado.cerebro is not None and resultado.cerebro.recuerdos_usados == []


# --------------------------------------------------------------------------------------------------------------
# Specialists
# --------------------------------------------------------------------------------------------------------------


def test_one_household_specialist_then_a_light_synthesis() -> None:
    luz = _luz()
    sintesis = Grabador([lambda: text_turn(f"Según Luz, con el PVPC pagarías 41,20 € al mes [{_fid(luz)}].")])
    verificador = Grabador([], [verdict("respuesta", ("pago PVPC", ["F1"], "respaldada"))])
    intencion = Grabador()
    resultado = cerebro(
        sintesis=sintesis, verificador=verificador, intencion=intencion, especialistas={"luz": luz}
    ).run("¿Qué tarifa de luz me conviene?", modulos=modulos(LUZ))
    assert intencion.llamadas == 0 and len(luz.contextos) == 1 and sintesis.llamadas == 1
    assert resultado.mode is AgentMode.VERIFIED and resultado.answer.startswith("Según Luz")
    assert resultado.citations[0].fragment_id == "F1" and resultado.citations[0].authority == "CALCULADORA"
    informe = resultado.cerebro
    assert informe is not None and informe.ruta == "sintesis" and informe.puerta is not None and informe.puerta.ok
    assert [(a.especialista, a.literal) for a in informe.puerta.atribuciones] == [("luz", "41,20 € al mes")]
    # The report reached the synthesis as data, with its figures table.
    mensaje = sintesis.inicios[0]["user_message"]
    assert "Con el PVPC pagarías 41,20 € al mes" in mensaje and "pago mensual con el PVPC" in mensaje
    assert "consultar_especialistas" in {t.name for t in sintesis.inicios[0]["tools"]}


def test_a_cross_domain_question_runs_its_specialists_in_parallel() -> None:
    barrera = threading.Barrier(2, timeout=5)
    hipoteca, luz = _hipoteca(espera=barrera), _luz(espera=barrera)
    intencion = Grabador(
        [],
        [
            _plan(
                ("hipoteca", "¿Cuánto ahorro amortizando 10.000 €?", []),
                ("luz", "¿Cuánto pagaría con el PVPC?", []),
            )
        ],
    )
    sintesis = Grabador(
        [
            lambda: text_turn(
                f"Hipoteca: amortizando ahorras 1.200,00 € al año en intereses [{_fid(hipoteca)}]. "
                f"Luz: con el PVPC pagarías 41,20 € al mes [{_fid(luz)}]. No los sumo: son cuentas distintas."
            )
        ]
    )
    verificador = Grabador([], [verdict("respuesta", ("ahorro", ["F1"], "respaldada"), ("pvpc", ["F2"], "respaldada"))])
    recibidos, on_event = eventos()
    resultado = cerebro(
        sintesis=sintesis,
        intencion=intencion,
        verificador=verificador,
        especialistas={"hipoteca": hipoteca, "luz": luz},
    ).run(
        "¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?",
        on_event=on_event,
        modulos=modulos(HIPOTECA, LUZ),
    )
    # Both waited at the same barrier: they ran at the same time, each in its own worker thread.
    assert hipoteca.hilos[0] != luz.hilos[0]
    assert intencion.llamadas == 1 and sintesis.llamadas == 1
    informe = resultado.cerebro
    assert informe is not None and informe.ruta == "sintesis" and informe.intencion.origen == "modelo"
    assert [i.especialista for i in informe.informes] == ["hipoteca", "luz"]
    assert informe.puerta is not None and informe.puerta.ok
    assert {a.especialista for a in informe.puerta.atribuciones} == {"hipoteca", "luz"}
    assert resultado.mode is AgentMode.VERIFIED
    # Events: the plan, the specialists, the synthesis, its draft, the figures gate, the verifier.
    tipos = [e["type"] for e in recibidos]
    assert tipos[0] == EVENTO_PLAN
    Intencion.model_validate(recibidos[0]["intencion"])
    sintesis_en = tipos.index(EVENTO_SINTESIS)
    assert all(i < sintesis_en for i, t in enumerate(tipos) if t == EVENTO_ESPECIALISTA)
    assert tipos.index("text_delta") < tipos.index(EVENTO_PUERTA) < tipos.index("verifying")
    assert recibidos[sintesis_en]["especialistas"] == ["hipoteca", "luz"]


def test_the_tax_specialist_waits_for_the_mortgage_and_reads_its_report() -> None:
    hipoteca = EspecialistaFalso(
        "hipoteca",
        "Hipoteca",
        calculo="Este año has pagado 9.000,00 € de hipoteca (capital 6.000,00 €, intereses 3.000,00 €).",
        cifras=[("pagado de hipoteca en el año", "9000.00", "EUR")],
    )
    fiscal = Grabador(
        [text_turn("Con lo pagado este año, 9.000,00 € [F1], la deducción por vivienda depende de la compra [F1].")],
        [verdict("respuesta", ("pagado", ["F1"], "respaldada"))],
    )
    intencion = Grabador(
        [],
        [
            _plan(
                ("hipoteca", "¿Cuánto he pagado de hipoteca este año?", []),
                ("fiscal", "¿Cuánto me deduzco por vivienda habitual con lo pagado?", ["e1"]),
            )
        ],
    )
    sintesis = Grabador(
        [text_turn("Según Hipoteca, este año pagaste 9.000,00 € [F1]; el asesor fiscal explica la deducción.")]
    )
    verificador = Grabador([], [verdict("respuesta", ("pagado", ["F1"], "respaldada"))])
    resultado = cerebro(
        fiscal=fiscal,
        intencion=intencion,
        sintesis=sintesis,
        verificador=verificador,
        especialistas={"hipoteca": hipoteca},
    ).run("¿Me puedo desgravar la hipoteca?", case=caso(), modulos=modulos(HIPOTECA))
    mensaje = fiscal.inicios[0]["user_message"]
    # The encargo and the earlier report reach the advisor as DATA; its query stays the user's own words.
    assert '<informe_previo especialista="hipoteca"' in mensaje and "9.000,00 €" in mensaje
    assert "<encargo_del_asistente>" in mensaje and mensaje.endswith("¿Me puedo desgravar la hipoteca?")
    assert "pedir_dato" in {t.name for t in fiscal.inicios[0]["tools"]}
    informe = resultado.cerebro
    assert informe is not None and [i.especialista for i in informe.informes] == ["hipoteca", "fiscal"]
    assert informe.informes[1].modo == "verified" and resultado.mode is AgentMode.VERIFIED


def test_replanning_consults_another_specialist_during_the_synthesis() -> None:
    luz, hipoteca = _luz(), _hipoteca()
    sintesis = Grabador(
        [
            tool_turn(
                (
                    "consultar_especialistas",
                    {"encargos": [{"especialista": "hipoteca", "pregunta": "¿Cuánto ahorro amortizando?"}]},
                )
            ),
            lambda: text_turn(
                f"Luz: 41,20 € al mes [{_fid(luz)}]. Hipoteca: ahorras 1.200,00 € al año [{_fid(hipoteca)}]."
            ),
        ]
    )
    verificador = Grabador([], [verdict("respuesta", ("luz", ["F1"], "respaldada"), ("hip", ["F2"], "respaldada"))])
    recibidos, on_event = eventos()
    resultado = cerebro(
        sintesis=sintesis, verificador=verificador, especialistas={"luz": luz, "hipoteca": hipoteca}
    ).run("¿Qué tarifa de luz me conviene?", on_event=on_event, modulos=modulos(LUZ, HIPOTECA))
    assert hipoteca.contextos[0].encargo.id == "e2" and hipoteca.contextos[0].encargo.pregunta.startswith("¿Cuánto")
    informe = resultado.cerebro
    assert informe is not None and [i.encargo_id for i in informe.informes] == ["e1", "e2"]
    assert informe.puerta is not None and informe.puerta.ok and resultado.mode is AgentMode.VERIFIED
    assert ("consultar_especialistas", "cerebro") in [(t.tool, t.agent) for t in resultado.tool_trace]
    assert any(e["type"] == EVENTO_PLAN and e.get("replan") for e in recibidos)
    # The new report reached the model as the tool's result.
    assert "Amortizando 10.000 €" in sintesis.tool_results[0].content


def test_replanning_is_bounded() -> None:
    luz = _luz()
    llamada = ("consultar_especialistas", {"encargos": [{"especialista": "luz", "pregunta": "otra vez"}]})
    sintesis = Grabador([tool_turn(llamada), tool_turn(llamada), lambda: text_turn(f"41,20 € al mes [{_fid(luz)}].")])
    verificador = Grabador([], [verdict("respuesta", ("luz", ["F1"], "respaldada"))])
    cerebro(sintesis=sintesis, verificador=verificador, especialistas={"luz": luz}, max_replanes=1).run(
        "¿Qué tarifa de luz me conviene?", modulos=modulos(LUZ)
    )
    assert not sintesis.tool_results[0].is_error and sintesis.tool_results[1].is_error
    assert "Ya no puedes consultar" in sintesis.tool_results[1].content


# --------------------------------------------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------------------------------------------


def _cruzada(sintesis: Grabador, **ajustes: Any) -> tuple[Any, EspecialistaFalso, EspecialistaFalso]:
    hipoteca, luz = _hipoteca(), _luz()
    intencion = Grabador([], [_plan(("hipoteca", "Ahorro amortizando", []), ("luz", "Pago con el PVPC", []))])
    brain = cerebro(
        sintesis=sintesis,
        intencion=intencion,
        verificador=Grabador([], [verdict("respuesta", ("x", ["F1"], "respaldada"))] * 3),
        especialistas={"hipoteca": hipoteca, "luz": luz},
        max_paralelo=1,  # one after another: hipoteca registers F1, luz F2
        **ajustes,
    )
    return brain, hipoteca, luz


def test_a_sum_no_tool_made_is_sent_back_and_fixed() -> None:
    sintesis = Grabador(
        [
            text_turn("Entre las dos cosas ahorras 1.241,20 € [F1]."),
            text_turn("Hipoteca: ahorras 1.200,00 € al año [F1]. Luz: el PVPC te costaría 41,20 € al mes [F2]."),
        ]
    )
    brain, _h, _l = _cruzada(sintesis)
    recibidos, on_event = eventos()
    resultado = brain.run(
        "¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?",
        on_event=on_event,
        modulos=modulos(HIPOTECA, LUZ),
    )
    assert resultado.revisions == 1 and "1.241,20" not in resultado.answer
    revision = sintesis.user_messages[0]
    assert "1.241,20" in revision and "No se la he mostrado" in revision
    assert [e["violaciones"] for e in recibidos if e["type"] == EVENTO_PUERTA] == [1, 0]
    informe = resultado.cerebro
    assert informe is not None and informe.revisiones_sintesis == 1 and informe.puerta is not None and informe.puerta.ok


def test_a_persistent_violation_ends_in_an_honest_partial_answer() -> None:
    invento = "Ahorras 999,99 € con todo [F1]. Hipoteca: 1.200,00 € al año [F1]."
    sintesis = Grabador([text_turn(invento), text_turn(invento)])
    brain, _h, _l = _cruzada(sintesis, max_revisiones=1)
    resultado = brain.run(
        "¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?", modulos=modulos(HIPOTECA, LUZ)
    )
    assert "999,99" not in resultado.answer and MARCA_RETIRADA in resultado.answer
    assert "1.200,00 € al año" in resultado.answer  # what was right stays
    assert resultado.mode is AgentMode.PARTIALLY_VERIFIED and resultado.revisions == 1
    assert any(w.startswith("He retirado de la respuesta una cifra") and "999,99" in w for w in resultado.warnings)
    assert resultado.cerebro is not None and not resultado.cerebro.puerta.ok  # type: ignore[union-attr]


def test_the_final_mode_is_never_better_than_the_worst_specialist() -> None:
    def informe(estado: str = "ok", modo: str | None = None) -> InformeEspecialista:
        return InformeEspecialista(encargo_id="e1", especialista="fiscal", nombre="Impuestos", estado=estado, modo=modo)  # type: ignore[arg-type]

    assert tope_de_informes([informe(modo="verified")]) is None
    assert tope_de_informes([informe(modo="partially_verified")]) is AgentMode.PARTIALLY_VERIFIED
    assert tope_de_informes([informe(modo="unverified"), informe(modo="verified")]) is AgentMode.UNVERIFIED
    assert tope_de_informes([informe(modo="abstained")]) is AgentMode.PARTIALLY_VERIFIED
    assert tope_de_informes([informe(estado="error")]) is AgentMode.PARTIALLY_VERIFIED
    assert tope_de_informes([informe()]) is None  # a module's report: its figures go through the gate


def test_a_specialist_that_fails_caps_the_mode_and_is_said() -> None:
    luz = _luz()
    hipoteca = _hipoteca(error=RuntimeError("roto"))
    intencion = Grabador([], [_plan(("hipoteca", "Ahorro", []), ("luz", "PVPC", []))])
    sintesis = Grabador([lambda: text_turn(f"Luz: 41,20 € al mes [{_fid(luz)}]. De la hipoteca no tengo datos.")])
    verificador = Grabador([], [verdict("respuesta", ("luz", ["F1"], "respaldada"))])
    resultado = cerebro(
        sintesis=sintesis,
        intencion=intencion,
        verificador=verificador,
        especialistas={"hipoteca": hipoteca, "luz": luz},
    ).run("¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?", modulos=modulos(HIPOTECA, LUZ))
    assert resultado.mode is AgentMode.PARTIALLY_VERIFIED
    assert any(w.startswith("Hipoteca:") and "esa parte falta" in w for w in resultado.warnings)


# --------------------------------------------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------------------------------------------


def test_a_spent_budget_at_the_synthesis_shows_the_reports_as_they_are() -> None:
    class SinPresupuesto(Grabador):
        def start(self, **kwargs: Any):  # type: ignore[override]
            raise PresupuestoAgotadoError("Límite por pregunta.", limite="llamadas_por_pregunta")

    brain, _h, _l = _cruzada(SinPresupuesto())
    resultado = brain.run(
        "¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?", modulos=modulos(HIPOTECA, LUZ)
    )
    assert "**Hipoteca**" in resultado.answer and "1.200,00 € al año" in resultado.answer
    assert "**Luz**" in resultado.answer and resultado.mode is AgentMode.PARTIALLY_VERIFIED
    assert any("límite de uso" in w for w in resultado.warnings)


def test_a_budget_spent_before_any_report_is_the_budget_error() -> None:
    agotado = PresupuestoAgotadoError("Has alcanzado tu límite.", limite="diario_por_usuario")
    hipoteca, luz = _hipoteca(error=agotado), _luz(error=agotado)
    intencion = Grabador([], [_plan(("hipoteca", "x", []), ("luz", "y", []))])
    brain = cerebro(intencion=intencion, especialistas={"hipoteca": hipoteca, "luz": luz}, max_paralelo=1)
    with pytest.raises(PresupuestoAgotadoError):
        brain.run("¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?", modulos=modulos(HIPOTECA, LUZ))


def test_a_budget_spent_at_the_understanding_is_the_budget_error() -> None:
    intencion = Grabador([], [PresupuestoAgotadoError("Sin presupuesto.", limite="llamadas_por_pregunta")])
    with pytest.raises(PresupuestoAgotadoError):
        cerebro(intencion=intencion, especialistas={"hipoteca": _hipoteca(), "luz": _luz()}).run(
            "¿Me compensa más amortizar la hipoteca o cambiar la tarifa de luz?", modulos=modulos(HIPOTECA, LUZ)
        )


# --------------------------------------------------------------------------------------------------------------
# Legacy mode and wiring
# --------------------------------------------------------------------------------------------------------------


def test_build_agent_returns_the_brain_or_the_legacy_advisor(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.nucleo.cerebro import construir

    monkeypatch.setattr(agent_mod, "construir_asesor_fiscal", lambda: "asesor")
    monkeypatch.setattr(construir, "construir_cerebro", lambda: "cerebro")
    monkeypatch.setattr(settings, "CEREBRO_MODO", "fiscal", raising=False)
    assert agent_mod.build_agent() == "asesor" and agent_mod.modo_cerebro() == "fiscal"
    monkeypatch.setattr(settings, "CEREBRO_MODO", "hogar", raising=False)
    assert agent_mod.build_agent() == "cerebro"
    monkeypatch.setattr(settings, "CEREBRO_MODO", "cualquier cosa", raising=False)
    assert agent_mod.modo_cerebro() == "hogar"


def test_the_brain_is_built_around_the_legacy_advisor(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.nucleo.cerebro import construir
    from asistente.nucleo.cerebro.contrato import MemoriaNula

    asesor = TeamAgent(provider=Grabador(), searcher=FakeSearcher(corpus()))
    monkeypatch.setattr(agent_mod, "construir_asesor_fiscal", lambda: asesor)
    monkeypatch.setattr(construir, "SERVICIO_MEMORIA", "asistente.no_existe:servicio_memoria")
    brain = construir.construir_cerebro()
    assert isinstance(brain, Cerebro) and brain.asesor is asesor and isinstance(brain.memoria, MemoriaNula)
    # cli/eval_agent.py reaches into the advisor's parts.
    assert brain._provider is asesor._provider and brain._searcher is asesor._searcher


def test_the_advisors_hooks_default_to_the_legacy_behaviour() -> None:
    asesor = TaxAgent(provider=Grabador(), searcher=FakeSearcher(corpus()))
    assert asesor.herramientas_del_hogar is True and asesor._aportes_del_turno() == ("", [])
    assert len(asesor._nuevo_ledger()) == 0


def test_team_internals_go_to_the_advisor() -> None:
    fiscal = Grabador([text_turn("Hola.")], [verdict("respuesta")])
    brain = cerebro(fiscal=fiscal)
    from asistente.nucleo.agent.evidence import EvidenceLedger

    resultado = brain.run("x", ledger=EvidenceLedger())
    assert resultado.cerebro is None and fiscal.llamadas == 2


def test_without_the_collaboration_runtime_the_plan_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cerebro_mod, "de_la_colaboracion", lambda nombre: None)
    luz = _luz()
    sintesis = Grabador([lambda: text_turn(f"Con el PVPC pagarías 41,20 € al mes [{_fid(luz)}].")])
    verificador = Grabador([], [verdict("respuesta", ("pvpc", ["F1"], "respaldada"))])
    resultado = cerebro(sintesis=sintesis, verificador=verificador, especialistas={"luz": luz}).run(
        "¿Qué tarifa de luz me conviene?", modulos=modulos(LUZ)
    )
    # The gate could not run: it never fails open; capped at partially verified and said.
    assert resultado.mode is AgentMode.PARTIALLY_VERIFIED and resultado.cerebro is not None
    assert resultado.cerebro.puerta is None
    assert cerebro_mod.AVISO_PUERTA_CAIDA in resultado.warnings


def test_a_final_draft_cut_by_max_tokens_is_never_fully_verified() -> None:
    luz = _luz()
    sintesis = Grabador(
        [
            lambda: ModelTurn(
                text=f"Con el PVPC pagarías 41,20 € al mes [{_fid(luz)}].",
                tool_calls=[],
                stop_reason="max_tokens",
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        ]
    )
    verificador = Grabador([], [verdict("respuesta", ("pvpc", ["F1"], "respaldada"))])
    resultado = cerebro(sintesis=sintesis, verificador=verificador, especialistas={"luz": luz}).run(
        "¿Qué tarifa de luz me conviene?", modulos=modulos(LUZ)
    )
    assert resultado.mode is AgentMode.PARTIALLY_VERIFIED
    assert cerebro_mod.TRUNCATED_WARNING in resultado.warnings


def test_a_figure_the_user_gave_in_an_earlier_conversation_is_theirs() -> None:
    memoria = MemoriaFalsa(["Tengo 20.000 € ahorrados para la entrada"])
    sintesis = Grabador([text_turn("Me dijiste que tienes 20.000 € ahorrados para la entrada (el 01/10/2026).")])
    resultado = cerebro(sintesis=sintesis, memoria=memoria).run(
        "¿Qué te dije de mis ahorros?", case=caso(), modulos=modulos(HIPOTECA)
    )
    informe = resultado.cerebro
    assert informe is not None and informe.puerta is not None and informe.puerta.ok and resultado.revisions == 0
    assert [(a.literal, a.del_usuario) for a in informe.puerta.atribuciones] == [("20.000 €", True)]


def test_the_memory_is_read_for_the_signed_in_account_even_without_a_case() -> None:
    memoria = MemoriaFalsa()
    cerebro(sintesis=Grabador([text_turn("¡Hola!")]), memoria=memoria).run(
        "Hola", modulos=modulos(), conversacion_id="conv-12345678", user_id="cuenta-1"
    )
    assert memoria.consultas[0]["user_id"] == "cuenta-1" and memoria.consultas[0]["case_id"] is None

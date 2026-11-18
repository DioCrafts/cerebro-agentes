"""The dispatcher: parallel DAG, order, timeouts, budget, dedupe, isolation, context, figure ids, events."""

from __future__ import annotations

import threading
import time

import pytest

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.agent.team import run_subagent
from asistente.nucleo.cerebro.colaboracion import Despachador, ondas
from asistente.nucleo.cerebro.colaboracion.aislamiento import CanceladoError
from asistente.nucleo.cerebro.contrato import EVENTO_ESPECIALISTA, ContextoEncargo, Encargo, InformeEspecialista
from asistente.nucleo.evaluacion.variantes import Variante, usar_variante, variante_en_uso
from asistente.plataforma.ops import consumo
from asistente.plataforma.ops.consumo import PresupuestoAgotadoError
from .fakes import EspecialistaFalso, Eventos, ProveedorGuion, cifra, texto, turno

ESPERA = 2.0  # seconds a barrier/event waits before the test fails (never reached when the code is right)


def encargo(eid: str, especialista: str, pregunta: str = "¿Cuánto?", depende_de: tuple[str, ...] = ()) -> Encargo:
    return Encargo(id=eid, especialista=especialista, pregunta=pregunta, depende_de=list(depende_de))


def presupuesto_agotado() -> PresupuestoAgotadoError:
    return PresupuestoAgotadoError("Sin presupuesto.", limite="llamadas_por_pregunta")


# --------------------------------------------------------------------------------------------------------------
# Parallelism and order
# --------------------------------------------------------------------------------------------------------------


def test_specialists_run_at_the_same_time() -> None:
    # Each one waits at a barrier for the other two: only possible if the three run concurrently.
    barrera = threading.Barrier(3, timeout=ESPERA)

    def esperar(ctx: ContextoEncargo) -> str:
        barrera.wait()
        return f"ok {ctx.encargo.id}"

    especialistas = {k: EspecialistaFalso(k, accion=esperar) for k in ("luz", "hipoteca", "fiscal")}
    inicio = time.monotonic()
    informes = Despachador(especialistas, max_paralelo=3).ejecutar(
        [encargo("e1", "luz"), encargo("e2", "hipoteca"), encargo("e3", "fiscal")], turno()
    )
    assert [i.estado for i in informes] == ["ok", "ok", "ok"]
    assert time.monotonic() - inicio < ESPERA


def test_parallelism_is_bounded_by_max_paralelo() -> None:
    activos = 0
    maximo = 0
    lock = threading.Lock()
    pareja = threading.Barrier(2, timeout=ESPERA)  # two at a time must meet; never three

    def trabajar(ctx: ContextoEncargo) -> str:
        nonlocal activos, maximo
        with lock:
            activos += 1
            maximo = max(maximo, activos)
        pareja.wait()
        with lock:
            activos -= 1
        return "ok"

    especialistas = {k: EspecialistaFalso(k, accion=trabajar) for k in ("aa", "bb", "cc", "dd")}
    plan = [encargo(f"e{i}", k) for i, k in enumerate(especialistas, start=1)]
    informes = Despachador(especialistas, max_paralelo=2).ejecutar(plan, turno())
    assert [i.estado for i in informes] == ["ok"] * 4
    assert maximo == 2


def test_reports_come_back_in_plan_order_whatever_finishes_first() -> None:
    segundo_terminado = threading.Event()

    def lento(ctx: ContextoEncargo) -> str:
        assert segundo_terminado.wait(ESPERA)
        return "lento"

    def rapido(ctx: ContextoEncargo) -> str:
        return "rápido"

    eventos = Eventos(lambda tipo, d: d.get("estado") == "done" and d["encargo_id"] == "e2" and segundo_terminado.set())
    especialistas = {"luz": EspecialistaFalso("luz", accion=lento), "agua": EspecialistaFalso("agua", accion=rapido)}
    informes = Despachador(especialistas, max_paralelo=2, emit=eventos).ejecutar(
        [encargo("e1", "luz"), encargo("e2", "agua")], turno()
    )
    assert [(i.encargo_id, i.texto) for i in informes] == [("e1", "lento"), ("e2", "rápido")]
    hechos = [d["encargo_id"] for d in eventos.de(EVENTO_ESPECIALISTA) if d["estado"] == "done"]
    assert hechos == ["e2", "e1"]  # completion order differs from the returned order


def test_one_at_a_time_is_the_topological_plan_order() -> None:
    orden: list[str] = []
    especialistas = {
        k: EspecialistaFalso(k, accion=lambda ctx: orden.append(ctx.encargo.id) or "ok") for k in ("aa", "bb", "cc")
    }
    plan = [encargo("e1", "aa", depende_de=("e3",)), encargo("e2", "bb"), encargo("e3", "cc")]
    Despachador(especialistas, max_paralelo=1).ejecutar(plan, turno())
    assert orden == ["e2", "e3", "e1"]


# --------------------------------------------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------------------------------------------


def test_a_dependent_encargo_starts_after_its_dependency_and_gets_its_report() -> None:
    hipoteca = EspecialistaFalso("hipoteca", accion=lambda ctx: "Pagas 812,34 € al mes.")
    fiscal = EspecialistaFalso("fiscal", accion=lambda ctx: "Deducción: 0 €.")
    eventos = Eventos()
    informes = Despachador({"hipoteca": hipoteca, "fiscal": fiscal}, max_paralelo=4, emit=eventos).ejecutar(
        [encargo("e1", "fiscal", depende_de=("e2",)), encargo("e2", "hipoteca")], turno()
    )
    assert [i.estado for i in informes] == ["ok", "ok"]
    (contexto,) = fiscal.contextos
    assert [(p.encargo_id, p.texto) for p in contexto.previos] == [("e2", "Pagas 812,34 € al mes.")]
    secuencia = [(d["encargo_id"], d["estado"]) for d in eventos.de(EVENTO_ESPECIALISTA)]
    assert secuencia.index(("e2", "done")) < secuencia.index(("e1", "start"))


def test_a_failed_dependency_still_reaches_its_dependent_with_its_status() -> None:
    def roto(ctx: ContextoEncargo) -> str:
        raise RuntimeError("bug")

    fiscal = EspecialistaFalso("fiscal")
    informes = Despachador({"hipoteca": EspecialistaFalso("hipoteca", accion=roto), "fiscal": fiscal}).ejecutar(
        [encargo("e1", "hipoteca"), encargo("e2", "fiscal", depende_de=("e1",))], turno()
    )
    assert [i.estado for i in informes] == ["error", "ok"]
    assert fiscal.contextos[0].previos[0].estado == "error"


@pytest.mark.parametrize(
    "plan",
    [
        [encargo("e1", "luz"), encargo("e1", "agua")],  # repeated ids
        [encargo("e1", "luz", depende_de=("e9",))],  # unknown dependency
        [encargo("e1", "luz", depende_de=("e2",)), encargo("e2", "agua", depende_de=("e1",))],  # cycle
    ],
)
def test_an_invalid_plan_is_a_programming_error(plan: list[Encargo]) -> None:
    with pytest.raises(ValueError):
        Despachador({"luz": EspecialistaFalso("luz"), "agua": EspecialistaFalso("agua")}).ejecutar(plan, turno())


def test_waves_of_a_plan() -> None:
    plan = [
        encargo("e1", "aa"),
        encargo("e2", "bb", depende_de=("e1",)),
        encargo("e3", "cc"),
        encargo("e4", "dd", depende_de=("e2", "e3")),
    ]
    assert ondas(plan) == [["e1", "e3"], ["e2"], ["e4"]]


# --------------------------------------------------------------------------------------------------------------
# Timeouts, cancellation, budget, isolation
# --------------------------------------------------------------------------------------------------------------


def test_a_slow_specialist_times_out_and_the_others_go_on() -> None:
    soltar = threading.Event()
    terminado = threading.Event()
    intento: list[BaseException | str] = []

    def colgado(ctx: ContextoEncargo) -> str:
        soltar.wait(ESPERA)
        # After the dispatcher gave up: the watched provider refuses any new model call.
        try:
            ctx.turno.proveedor_para("especialista:luz").start(system="s", tools=[], history=[], user_message="u")
            intento.append("llamó")
        except CanceladoError as exc:
            intento.append(exc)
        ctx.turno.emit("tarde", algo=1)
        terminado.set()
        return "demasiado tarde"

    eventos = Eventos()
    luz = EspecialistaFalso("luz", accion=colgado)
    especialistas = {"luz": luz, "agua": EspecialistaFalso("agua")}
    inicio = time.monotonic()
    informes = Despachador(especialistas, timeout_s=0.1, emit=eventos).ejecutar(
        [encargo("e1", "luz"), encargo("e2", "agua")], turno()
    )
    assert time.monotonic() - inicio < 1.0
    lento, otro = informes
    assert lento.estado == "error" and "Tiempo agotado" in lento.avisos[0] and otro.estado == "ok"
    assert luz.contextos[0].pizarra.cancelada  # type: ignore[attr-defined]
    soltar.set()
    assert terminado.wait(ESPERA)  # the abandoned thread finishes on its own
    assert isinstance(intento[0], CanceladoError)
    assert not eventos.de("tarde")  # a cancelled encargo's events are dropped


def test_budget_exhaustion_stops_scheduling() -> None:
    error = presupuesto_agotado()

    def agotar(ctx: ContextoEncargo) -> str:
        raise error

    otros = [EspecialistaFalso("bb"), EspecialistaFalso("cc")]
    especialistas = {"aa": EspecialistaFalso("aa", accion=agotar), "bb": otros[0], "cc": otros[1]}
    despachador = Despachador(especialistas, max_paralelo=1)
    informes = despachador.ejecutar([encargo("e1", "aa"), encargo("e2", "bb"), encargo("e3", "cc")], turno())
    assert [i.estado for i in informes] == ["presupuesto"] * 3
    assert all(o.llamadas == 0 for o in otros)
    assert despachador.presupuesto_agotado is error


def test_budget_exhaustion_swallowed_by_the_loop_is_still_seen() -> None:
    """``run_subagent`` swallows ProviderError; the watched provider still tells the dispatcher."""

    class SinPresupuesto(ProveedorGuion):
        def start(self, **kwargs):  # type: ignore[no-untyped-def]
            conversacion = super().start(**kwargs)

            def fallar(_c):  # type: ignore[no-untyped-def]
                raise presupuesto_agotado()

            conversacion.turnos = [fallar]
            return conversacion

    def con_bucle(ctx: ContextoEncargo) -> str:
        proveedor = ctx.turno.proveedor_para("especialista:aa")
        run = run_subagent(
            proveedor,
            agent="x",
            system="s",
            toolbox=_SinHerramientas(),
            user_message="u",
            max_steps=2,
            emit=ctx.turno.emit,
        )
        return run.memo

    proveedor = SinPresupuesto()
    especialistas = {"aa": EspecialistaFalso("aa", accion=con_bucle), "bb": EspecialistaFalso("bb")}
    despachador = Despachador(especialistas, max_paralelo=1)
    informes = despachador.ejecutar([encargo("e1", "aa"), encargo("e2", "bb")], turno(proveedor))
    assert [i.estado for i in informes] == ["presupuesto", "presupuesto"]
    assert isinstance(despachador.presupuesto_agotado, PresupuestoAgotadoError)


class _SinHerramientas:
    def specs(self):  # type: ignore[no-untyped-def]
        return []

    def execute(self, call):  # type: ignore[no-untyped-def]
        raise AssertionError


def test_a_failing_specialist_is_isolated() -> None:
    def roto(ctx: ContextoEncargo) -> str:
        raise RuntimeError("fallo interno")

    def raro(ctx: ContextoEncargo):  # type: ignore[no-untyped-def]
        return InformeEspecialista(encargo_id="otro", especialista="zz", nombre="Z", estado="ok", texto="suplantado")

    especialistas = {
        "aa": EspecialistaFalso("aa", accion=roto),
        "bb": EspecialistaFalso("bb"),
        "cc": EspecialistaFalso("cc", accion=raro),
    }
    informes = Despachador(especialistas).ejecutar(
        [encargo("e1", "aa"), encargo("e2", "bb"), encargo("e3", "cc")], turno()
    )
    assert [i.estado for i in informes] == ["error", "ok", "ok"]
    assert informes[0].avisos == ["El especialista falló (RuntimeError)."]
    # A specialist cannot answer for another encargo or another specialist.
    assert (informes[2].encargo_id, informes[2].especialista) == ("e3", "cc")


def test_a_specialist_returning_garbage_is_an_error() -> None:
    especialistas = {"aa": EspecialistaFalso("aa", accion=lambda ctx: None)}
    especialistas["aa"].atender = lambda ctx: "no es un informe"  # type: ignore[method-assign,assignment,return-value]
    (informe,) = Despachador(especialistas).ejecutar([encargo("e1", "aa")], turno())
    assert informe.estado == "error" and "TypeError" in informe.avisos[0]


def test_an_inactive_specialist_is_denied_without_running() -> None:
    informes = Despachador({"luz": EspecialistaFalso("luz")}).ejecutar(
        [encargo("e1", "luz"), encargo("e2", "seguros")], turno()
    )
    assert [(i.estado, i.especialista) for i in informes] == [("ok", "luz"), ("denegado", "seguros")]


# --------------------------------------------------------------------------------------------------------------
# Dedupe and replanning
# --------------------------------------------------------------------------------------------------------------


def test_identical_encargos_run_once() -> None:
    luz = EspecialistaFalso("luz", cifras=[cifra("C1", "luz", "coste", "31.20")])
    informes = Despachador({"luz": luz}).ejecutar(
        [encargo("e1", "luz", "¿Cuánto pago de luz?"), encargo("e2", "luz", "  ¿cuánto  PAGO de luz ")], turno()
    )
    assert luz.llamadas == 1
    assert [i.encargo_id for i in informes] == ["e1", "e2"]
    assert informes[1].texto == informes[0].texto and "Mismo encargo que e1" in informes[1].avisos[-1]
    assert [c.id for c in informes[0].cifras] == [c.id for c in informes[1].cifras] == ["C1"]


def test_same_question_with_other_dependencies_is_not_a_duplicate() -> None:
    luz = EspecialistaFalso("luz")
    Despachador({"luz": luz, "agua": EspecialistaFalso("agua")}).ejecutar(
        [encargo("e1", "agua"), encargo("e2", "luz", "x"), encargo("e3", "luz", "x", depende_de=("e1",))], turno()
    )
    assert luz.llamadas == 2


def test_replanning_reuses_answered_encargos_and_keeps_figure_ids() -> None:
    luz = EspecialistaFalso("luz", cifras=[cifra("C7", "luz", "coste", "31.20")])
    agua = EspecialistaFalso("agua", cifras=[cifra("C1", "agua", "consumo", "9.5", None)])
    despachador = Despachador({"luz": luz, "agua": agua})
    contexto = turno()
    (primero,) = despachador.ejecutar([encargo("e1", "luz", "coste")], contexto)
    segundo, tercero = despachador.ejecutar(
        [encargo("e2", "luz", "coste"), encargo("e3", "agua", "consumo", depende_de=("e1",))], contexto
    )
    assert luz.llamadas == 1 and agua.llamadas == 1
    assert segundo.texto == primero.texto and segundo.cifras[0].id == primero.cifras[0].id == "C1"
    assert tercero.cifras[0].id == "C2"
    assert agua.contextos[0].previos[0].encargo_id == "e1"
    assert [i.encargo_id for i in despachador.informes] == ["e1", "e2", "e3"]


# --------------------------------------------------------------------------------------------------------------
# Figures, context variables, events
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("primero_en_terminar", ["e1", "e2"])
def test_figure_ids_are_unique_and_follow_plan_order(primero_en_terminar: str) -> None:
    turno_de = {"e1": threading.Event(), "e2": threading.Event()}
    otro = {"e1": "e2", "e2": "e1"}

    def accion(ctx: ContextoEncargo) -> str:
        if ctx.encargo.id != primero_en_terminar:
            assert turno_de[otro[ctx.encargo.id]].wait(ESPERA)
        return "ok"

    eventos = Eventos(lambda t, d: d.get("estado") == "done" and turno_de[d["encargo_id"]].set())
    especialistas = {
        "luz": EspecialistaFalso(
            "luz", accion=accion, cifras=[cifra("C1", "luz", "a", "1.5"), cifra("C2", "luz", "b", "2")]
        ),
        "agua": EspecialistaFalso(
            "agua", accion=accion, cifras=[cifra("C1", "agua", "c", "3"), cifra("C1", "agua", "c", "3")]
        ),
    }
    informes = Despachador(especialistas, max_paralelo=2, emit=eventos).ejecutar(
        [encargo("e1", "luz"), encargo("e2", "agua")], turno()
    )
    assert [[c.id for c in i.cifras] for i in informes] == [["C1", "C2"], ["C3"]]


def test_context_variables_reach_the_worker_threads() -> None:
    vistos: list[tuple[object, str, int]] = []

    def mirar(ctx: ContextoEncargo) -> str:
        vistos.append((consumo._CONTEXTO.get(), variante_en_uso().id, threading.get_ident()))
        return "ok"

    especialistas = {k: EspecialistaFalso(k, accion=mirar) for k in ("aa", "bb")}
    with (
        consumo.contexto_consumo(user_id="u1", case_id="c1") as contexto_uso,
        usar_variante(Variante(id="prueba-hilos")),
    ):
        Despachador(especialistas, max_paralelo=2).ejecutar([encargo("e1", "aa"), encargo("e2", "bb")], turno())
    assert len(vistos) == 2
    assert all(uso is contexto_uso and variante == "prueba-hilos" for uso, variante, _h in vistos)
    assert all(hilo != threading.get_ident() for _u, _v, hilo in vistos)


def test_events_mark_start_and_done_of_every_specialist() -> None:
    eventos = Eventos()

    def emitir_y_fallar(_t, _d):  # a callback that raises never breaks the turn
        raise RuntimeError

    Despachador({"luz": EspecialistaFalso("luz")}, emit=eventos).ejecutar([encargo("e1", "luz")], turno())
    assert [(d["encargo_id"], d["estado"]) for d in eventos.de(EVENTO_ESPECIALISTA)] == [
        ("e1", "start"),
        ("e1", "done"),
    ]
    fin = eventos.de(EVENTO_ESPECIALISTA)[-1]
    assert fin["resultado"] == "ok" and fin["clave"] == "luz" and fin["nombre"] == "Luz" and fin["duracion_ms"] >= 0
    informes = Despachador({"luz": EspecialistaFalso("luz")}, emit=Eventos(emitir_y_fallar)).ejecutar(
        [encargo("e1", "luz")], turno()
    )
    assert informes[0].estado == "ok"


def test_the_turn_emitter_is_used_when_none_is_given() -> None:
    eventos = Eventos()
    Despachador({"luz": EspecialistaFalso("luz")}).ejecutar([encargo("e1", "luz")], turno(emit=eventos))
    assert len(eventos.de(EVENTO_ESPECIALISTA)) == 2


def test_settings_and_contract_bound_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "CEREBRO_MAX_PARALELO", 3)
    monkeypatch.setattr(settings, "CEREBRO_TIMEOUT_ENCARGO_S", 0.0)
    despachador = Despachador({"luz": EspecialistaFalso("luz")})
    assert despachador.max_paralelo == 3 and despachador.timeout_s is None
    assert Despachador({}, max_paralelo=0).max_paralelo == 1
    despachador.ejecutar([encargo("e1", "luz")], turno())
    assert despachador.pizarra is not None and despachador.pizarra.max_profundidad == 2
    alto = Despachador({"luz": EspecialistaFalso("luz")}, max_profundidad=9, max_peticiones=99)
    alto.ejecutar([encargo("e1", "luz")], turno())
    assert alto.pizarra is not None and (alto.pizarra.max_profundidad, alto.pizarra.max_peticiones) == (2, 8)


def test_bad_specialist_keys_are_refused() -> None:
    with pytest.raises(ValueError):
        Despachador({"Luz!": EspecialistaFalso("luz")})
    with pytest.raises(TypeError):
        Despachador({"luz": object()})  # type: ignore[dict-item]


def test_an_empty_plan_returns_nothing() -> None:
    assert Despachador({"luz": EspecialistaFalso("luz")}).ejecutar([], turno()) == []


def test_a_provider_error_inside_a_specialist_is_its_own_business() -> None:
    def fallar(ctx: ContextoEncargo) -> str:
        raise ProviderError("caído")

    (informe,) = Despachador({"luz": EspecialistaFalso("luz", accion=fallar)}).ejecutar([encargo("e1", "luz")], turno())
    assert informe.estado == "error"


def test_the_scripted_provider_is_shared_safely_by_purpose() -> None:
    proveedor = ProveedorGuion({"especialista:aa": [[texto("A")]], "especialista:bb": [[texto("B")]]})

    def con_modelo(ctx: ContextoEncargo) -> str:
        clave = ctx.encargo.especialista
        conversacion = ctx.turno.proveedor_para(f"especialista:{clave}").start(
            system="s", tools=[], history=[], user_message="u"
        )
        return conversacion.step().text

    especialistas = {k: EspecialistaFalso(k, accion=con_modelo) for k in ("aa", "bb")}
    informes = Despachador(especialistas, max_paralelo=2).ejecutar(
        [encargo("e1", "aa"), encargo("e2", "bb")], turno(proveedor)
    )
    assert [i.texto for i in informes] == ["A", "B"]
    assert sorted(a["proposito"] for a in proveedor.arranques) == ["especialista:aa", "especialista:bb"]

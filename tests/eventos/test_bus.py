"""The bus: routing to subscribed modules only, idempotency, retries, dead letters, leases, two workers."""

from __future__ import annotations

from datetime import timedelta
import sqlite3
import threading

from asistente.nucleo.decision.schemas import CaseStatus
from asistente.nucleo.eventos import productores
from asistente.nucleo.eventos.bus import HECHO, MUERTO, OMITIDO, PENDIENTE, Bus
from asistente.nucleo.eventos.salud import estado, problemas
from asistente.nucleo.eventos.tipos import (
    FECHA_UMBRAL,
    INDICE_PUBLICADO,
    MODULO_ACTIVADO,
    MODULO_DESACTIVADO,
    PERFIL_CAMBIADO,
)
from tests.proactive import make_case
from .ayudas import FISCAL, HOY, Llamadas, Reloj, agua, aviso, contador, crear_bus, instalar, seguros, stores


def _entregas(bus: Bus) -> list[tuple[str, str, str, str]]:
    return [
        (f["tipo"], f["modulo"], f["case_id"], f["estado"])
        for f in bus._leer("SELECT * FROM eventos_entregas ORDER BY id")
    ]


def test_a_fact_change_wakes_only_the_module_that_reads_it(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    llamadas = Llamadas()
    m_agua = agua(llamadas, avisos_fijos=[aviso("factura")])
    m_seguros = seguros(llamadas, avisos_fijos=[aviso("poliza")])
    instalar(m_agua, m_seguros)
    productores.instalar()
    bus = crear_bus(db, modulos=[FISCAL, m_agua, m_seguros])

    case = cases.save_case(make_case("c1", agua_personas=3))
    bus.drenar()
    assert len(m_agua.contextos) == 1 and m_seguros.contextos == []
    assert [n.key for n in notas.list("c1")] == ["modulo:agua:factura"]  # the tax module was not woken

    cases.save_case(make_case("c1", agua_personas=3, seguros_poliza="P-1").model_copy(update={"version": case.version}))
    bus.drenar()
    assert len(m_agua.contextos) == 1 and len(m_seguros.contextos) == 1
    assert _entregas(bus) == [
        (PERFIL_CAMBIADO, "agua", "c1", HECHO),
        (PERFIL_CAMBIADO, "seguros", "c1", HECHO),
    ]


def test_a_shared_fact_wakes_the_tax_module_and_the_modules_that_declare_it(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    llamadas = Llamadas()
    m_agua, m_seguros = agua(llamadas), seguros(llamadas)
    instalar(m_agua, m_seguros)
    productores.instalar()
    bus = crear_bus(db, modulos=[FISCAL, m_agua, m_seguros])
    cases.save_case(make_case("c1", comunidad_autonoma="Madrid", rendimiento_trabajo_bruto=30000))
    bus.drenar()
    assert sorted(m for _t, m, _c, _e in _entregas(bus)) == ["agua", "fiscal"]
    assert m_seguros.contextos == []
    assert all(e == HECHO for _t, _m, _c, e in _entregas(bus))
    # The tax jobs ran, as the daily run would: the year-end plan (from 1 October).
    assert any(n.kind == "fin_de_ejercicio" for n in notas.list("c1"))


def test_a_disabled_module_is_never_woken(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    llamadas = Llamadas()
    m_agua, m_seguros = agua(llamadas, avisos_fijos=[aviso("a")]), seguros(llamadas, avisos_fijos=[aviso("s")])
    instalar(m_agua, m_seguros)
    productores.instalar()
    activos = {"c1": [FISCAL, m_agua]}  # «seguros» is off for this owner
    bus = crear_bus(db, modulos=lambda case_id: activos[case_id])
    cases.save_case(make_case("c1", agua_personas=1, seguros_poliza="P"))
    bus.avanzar_consumidores()
    assert [m for _t, m, _c, _e in _entregas(bus)] == ["agua"]

    # Switched off between the fan-out and the run: skipped, not run.
    activos["c1"] = [FISCAL]
    bus.drenar()
    assert m_agua.contextos == [] and m_seguros.contextos == [] and notas.list("c1") == []
    assert _entregas(bus)[0][3] == OMITIDO


def test_switching_a_module_on_wakes_it_and_off_cancels_its_timer(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    m = contador("agua", proxima=HOY + timedelta(days=20))
    otro = contador("seguros")
    instalar(m, otro)
    cases.save_case(make_case("c1"))
    bus = crear_bus(db, modulos=[FISCAL, m, otro])
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 1} and otro.llamadas_por_caso == {}
    assert bus.temporizadores() == [("c1", "agua", HOY + timedelta(days=20))]

    bus.emitir(MODULO_DESACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.drenar()
    assert bus.temporizadores() == [] and m.llamadas_por_caso == {"c1": 1}


def test_a_module_can_declare_its_own_subscriptions(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    indices = contador("indices", eventos=(INDICE_PUBLICADO,))
    m_agua = contador("agua")
    instalar(indices, m_agua)
    cases.save_case(make_case("c1"))
    cases.save_case(make_case("c2"))
    cerrado = make_case("c3").model_copy(update={"status": CaseStatus.CLOSED})
    cases.save_case(cerrado)
    bus = crear_bus(db, modulos=[FISCAL, indices, m_agua])
    bus.emitir(INDICE_PUBLICADO, payload={"boe_ids": ["BOE-A-2026-1"]})
    bus.drenar()
    assert indices.llamadas_por_caso == {"c1": 1, "c2": 1}  # every open case, never the closed one
    assert m_agua.llamadas_por_caso == {}
    # A profile change does not wake it: it declared only the index event.
    bus.emitir(PERFIL_CAMBIADO, case_id="c1", payload={"hechos": ["indices_dato"], "borrados": []})
    bus.drenar()
    assert indices.llamadas_por_caso == {"c1": 1, "c2": 1}


def test_idempotent_emission_fan_out_and_handling(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    m = contador("agua")
    instalar(m)
    cases.save_case(make_case("c1"))
    bus = crear_bus(db, modulos=[FISCAL, m])
    payload = {"modulo": "agua"}
    assert bus.emitir(MODULO_ACTIVADO, case_id="c1", payload=payload, clave="k") is not None
    assert bus.emitir(MODULO_ACTIVADO, case_id="c1", payload=payload, clave="k") is None
    bus.drenar()
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 1}

    # Replaying the log from the start (a lost cursor) creates no second delivery.
    with bus._tx() as conn:
        conn.execute("UPDATE eventos_cursores SET ultimo_id = 0")
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 1} and len(_entregas(bus)) == 1

    # At-least-once: a delivery run again (a worker died before marking it) stores no second notice.
    with bus._tx() as conn:
        conn.execute("UPDATE eventos_entregas SET estado = ?", (PENDIENTE,))
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 2}
    assert [n.key for n in notas.list("c1")] == ["modulo:agua:aviso:c1"]


def test_a_failure_is_retried_with_backoff(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    m = contador("agua", fallos=2)
    instalar(m)
    cases.save_case(make_case("c1"))
    reloj = Reloj()
    bus = crear_bus(db, modulos=[FISCAL, m], reloj=reloj, max_intentos=5, backoff_base_s=30.0)
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})

    bus.drenar()
    fila = bus._leer("SELECT * FROM eventos_entregas")[0]
    assert (fila["estado"], fila["intentos"]) == (PENDIENTE, 1) and "fallo pasajero" in fila["error"]
    reloj.avanza(seconds=29)
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 1}  # still waiting: 30 s after the first failure
    reloj.avanza(seconds=1)
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 2}  # second failure: now 60 s
    reloj.avanza(seconds=59)
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 2}
    reloj.avanza(seconds=1)
    bus.drenar()
    fila = bus._leer("SELECT * FROM eventos_entregas")[0]
    assert (fila["estado"], fila["intentos"], m.llamadas_por_caso) == (HECHO, 3, {"c1": 3})
    assert len(notas.list("c1")) == 1
    [metrica] = bus.metricas()
    assert (metrica["tipo"], metrica["modulo"], metrica["total"], metrica["reintentos"], metrica["fallos"]) == (
        MODULO_ACTIVADO,
        "agua",
        1,
        2,
        2,
    )
    assert metrica["latencia_ms_p50"] == 90_000.0


def test_exhausted_retries_go_to_the_dead_letters_and_can_be_requeued(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    m = contador("agua", fallos=99)
    instalar(m)
    cases.save_case(make_case("c1"))
    reloj = Reloj()
    bus = crear_bus(db, modulos=[FISCAL, m], reloj=reloj, max_intentos=3, backoff_base_s=0.0)
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.drenar()
    assert m.llamadas_por_caso == {"c1": 3}
    assert _entregas(bus)[0][3] == MUERTO
    salud = bus.salud()
    assert salud["muertas"] == 1 and salud["pendientes"] == 0
    assert any("agotaron sus reintentos" in p for p in problemas(db, ahora=reloj()))
    assert "c1" not in str(estado(db, ahora=reloj()))  # health is aggregates only

    m.fallos = 0
    assert bus.reintentar_muertas() == 1
    bus.drenar()
    assert _entregas(bus)[0][3] == HECHO and problemas(db, ahora=reloj()) == []


class _Fragil:
    nombre = "fragil"

    def __init__(self) -> None:
        self.vistos: list[int] = []

    def procesar(self, bus, evento, conn) -> None:
        if evento.payload.get("modulo") == "veneno":
            conn.execute("INSERT INTO eventos_temporizadores VALUES ('x', 'y', '2030-01-01', 'p')")  # rolled back
            raise ValueError("no sé procesar esto")
        self.vistos.append(evento.id)


def test_a_consumer_retries_then_dead_letters_the_event_and_moves_on(tmp_path) -> None:
    db = str(tmp_path / "estado.sqlite3")
    reloj = Reloj()
    bus = crear_bus(db, modulos=[], reloj=reloj, max_intentos=2, backoff_base_s=10.0)
    fragil = _Fragil()
    bus.consumidores = [fragil]
    a = bus.emitir(FECHA_UMBRAL, case_id="c1", payload={"modulo": "veneno"})
    b = bus.emitir(FECHA_UMBRAL, case_id="c1", payload={"modulo": "agua"})
    assert bus.avanzar_consumidores() == 0
    assert bus.salud()["consumidores_con_error"] == ["fragil"]
    assert bus.avanzar_consumidores() == 0  # backing off
    reloj.avanza(seconds=10)
    assert bus.avanzar_consumidores() == 2  # second failure: dead letter, then the next event
    assert fragil.vistos == [b]
    assert bus._leer("SELECT evento_id, modulo, estado FROM eventos_entregas")[0][:] == (a, "consumidor:fragil", MUERTO)
    assert bus.temporizadores() == []  # the failed event's writes were rolled back
    assert bus.reintentar_muertas() == 0  # a consumer's dead letter is a record, not a delivery


def test_an_expired_lease_is_taken_over_and_the_old_worker_cannot_finish_it(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    m = contador("agua")
    instalar(m)
    cases.save_case(make_case("c1"))
    reloj = Reloj()
    bus = crear_bus(db, modulos=[FISCAL, m], reloj=reloj, arriendo_s=60.0)
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.avanzar_consumidores()
    [entrega] = bus.reclamar("muerto-de-repente")
    assert bus.reclamar("otro") == []  # leased
    reloj.avanza(seconds=61)
    [retomada] = bus.reclamar("otro")
    assert retomada["intentos"] == 2
    assert bus.procesar_entrega(retomada, "otro") == HECHO
    assert not bus._terminar(entrega, "muerto-de-repente", estado=HECHO)  # it lost the lease
    assert m.llamadas_por_caso == {"c1": 1} and len(notas.list("c1")) == 1


def test_two_workers_never_run_a_delivery_twice(tmp_path) -> None:
    db, cases, notas = stores(tmp_path)
    m = contador("agua", pausa=0.002)
    instalar(m)
    for n in range(40):
        cases.save_case(make_case(f"c{n:02d}"))
    productor = crear_bus(db, modulos=[FISCAL, m])
    for n in range(40):
        productor.emitir(MODULO_ACTIVADO, case_id=f"c{n:02d}", payload={"modulo": "agua"})

    errores: list[BaseException] = []

    def trabajar(nombre: str) -> None:
        bus = crear_bus(db, modulos=[FISCAL, m], lote=3)
        try:
            bus.drenar(nombre, max_ciclos=50)
        except BaseException as exc:  # pragma: no cover - reported below
            errores.append(exc)
        finally:
            bus.close()

    hilos = [threading.Thread(target=trabajar, args=(f"w{i}",)) for i in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)
    assert errores == []
    assert m.llamadas_por_caso == {f"c{n:02d}": 1 for n in range(40)}
    filas = productor._leer("SELECT estado, COUNT(*) FROM eventos_entregas GROUP BY estado")
    assert [tuple(f) for f in filas] == [(HECHO, 40)]
    trabajadores = {t["nombre"] for t in productor.salud()["trabajadores"]}
    assert trabajadores == {"w0", "w1"}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 40


def test_timers_wake_the_module_on_the_date_it_asked_for(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    m = contador("agua", proxima=HOY + timedelta(days=10))
    otro = contador("seguros")
    instalar(m, otro)
    cases.save_case(make_case("c1"))
    dia = {"hoy": HOY}
    bus = Bus(db, modulos_activos=lambda _c: [FISCAL, m, otro], hoy=lambda: dia["hoy"])
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.drenar()
    assert bus.temporizadores() == [("c1", "agua", HOY + timedelta(days=10))]

    dia["hoy"] = HOY + timedelta(days=9)
    assert bus.drenar()["temporizadores"] == 0
    dia["hoy"] = HOY + timedelta(days=10)
    m.proxima = None  # nothing more to wait for
    resumen = bus.drenar()
    assert resumen["temporizadores"] == 1
    assert m.llamadas_por_caso == {"c1": 2} and otro.llamadas_por_caso == {}
    assert [e.tipo for e in bus.eventos()][-1] == FECHA_UMBRAL
    assert bus.temporizadores() == []
    assert bus.disparar_temporizadores() == 0


def test_purge_forgets_old_finished_work_only(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    m = contador("agua")
    instalar(m)
    cases.save_case(make_case("c1"))
    reloj = Reloj()
    bus = crear_bus(db, modulos=[FISCAL, m], reloj=reloj)
    bus.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    bus.drenar()
    assert bus.purgar(dias=30) == {"entregas": 0, "eventos": 0}
    reloj.avanza(days=31)
    assert bus.purgar(dias=30) == {"entregas": 1, "eventos": 1}
    assert bus.salud()["eventos"] == 0


def test_a_tasks_cursor_left_behind_no_longer_blocks_the_purge(tmp_path) -> None:
    """The tasks worker ran once (its ``tareas`` cursor exists) and then never again: the event worker runs the
    tasks' consumer too, so the cursor keeps up and the purge still forgets old events."""
    from asistente.nucleo.eventos.bus import consumidores_del_trabajador
    from asistente.nucleo.tareas.motor import ConsumidorTareas

    db = str(tmp_path / "bus.sqlite3")
    reloj = Reloj()
    Bus(db, reloj=reloj, hoy=lambda: HOY, consumidores=[ConsumidorTareas()]).avanzar_consumidores()

    solo_modulos = crear_bus(db, modulos=[FISCAL], reloj=reloj)
    solo_modulos.emitir(MODULO_ACTIVADO, case_id="c1", payload={"modulo": "agua"})
    solo_modulos.drenar()
    reloj.avanza(days=31)
    # Before: only the modules' consumer ran, the tasks' cursor stayed at 0 and nothing could be forgotten.
    assert solo_modulos.purgar(dias=30)["eventos"] == 0

    trabajador = crear_bus(db, modulos=[FISCAL], reloj=reloj)
    trabajador.consumidores = consumidores_del_trabajador()
    assert [c.nombre for c in trabajador.consumidores] == ["modulos", "tareas"]
    trabajador.drenar()
    assert trabajador.purgar(dias=30)["eventos"] == 1
    assert trabajador.salud()["eventos"] == 0


def test_every_event_worker_runs_the_tasks_consumer(tmp_path, monkeypatch) -> None:
    from asistente.plataforma import trabajos
    from asistente.plataforma.cli import eventos as cli_eventos

    db = str(tmp_path / "bus.sqlite3")
    assert [c.nombre for c in cli_eventos._bus(db).consumidores] == ["modulos", "tareas"]
    trabajos.pasada_de_eventos(db, "w")
    with sqlite3.connect(db) as conn:
        cursores = {fila[0] for fila in conn.execute("SELECT consumidor FROM eventos_cursores")}
    assert cursores == {"modulos", "tareas"}

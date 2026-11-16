"""The engine: pending actions wait for the owner's button, pause/resume, restart recovery, retries, modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
import time
from typing import Any

import pytest

from asistente.nucleo.eventos.bus import Bus
from asistente.nucleo.eventos.tipos import PERFIL_CAMBIADO
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.tareas import servicio
from asistente.nucleo.tareas.almacen import TareaStore
from asistente.nucleo.tareas.capacidades import ResultadoCapacidad, capacidades_por_defecto
from asistente.nucleo.tareas.motor import Configuracion, id_accion
from .conftest import Entorno


def _plan(entorno: Entorno, pasos: list[dict[str, Any]], *, titulo: str = "Cambiar la tarifa del agua") -> Any:
    case_id = entorno.caso()
    motor = entorno.motor()
    tarea, _ = servicio.proponer_plan(
        motor.store, case_id=case_id, user_id=None, titulo=titulo, objetivo="quiero pagar menos de agua", pasos=pasos
    )
    return motor, tarea


ACCION = [
    {"id": "leer", "titulo": "Lee el contador", "tipo": "calculo", "herramienta": "agua_lectura"},
    {
        "id": "cambiar",
        "titulo": "Cambia la tarifa",
        "tipo": "accion",
        "herramienta": "agua_cambiar_tarifa",
        "argumentos": {"importe": 10},
        "depende_de": ["leer"],
    },
    {"id": "fin", "titulo": "Comprueba la factura", "tipo": "confirmacion_usuario", "depende_de": ["cambiar"]},
]


def test_an_action_step_only_queues_a_pending_action_and_waits_for_the_button(entorno: Entorno) -> None:
    motor, tarea = _plan(entorno, ACCION)
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    paso = tarea.paso("cambiar")
    assert paso.estado == "esperando" and paso.espera_actual.motivo == "accion"
    assert "agua_cambiar_tarifa" not in entorno.llamadas.nombres()  # never executed by the engine
    acciones = AccionStore(entorno.db)
    pendientes = acciones.list(tarea.case_id, estado="pendiente")
    assert [a.id for a in pendientes] == [paso.espera_actual.accion_id] == tarea.vinculos.acciones
    assert tarea.estado == "esperando_usuario"

    # Running again (another worker, a restart) creates nothing new.
    for _ in range(3):
        motor.avanzar(tarea.id)
        entorno.motor().ciclo()
    assert len(acciones.list(tarea.case_id)) == 1

    # The chat or the step button cannot confirm an action: only its own button runs it.
    with pytest.raises(servicio.OperacionNoPermitidaError, match="botón"):
        servicio.confirmar_paso(motor, tarea.id, "cambiar", fuente="boton")

    # The owner confirms it (what POST /acciones/{id}/confirmar does): then the task goes on.
    accion = pendientes[0]
    acciones.transicion(accion.id, desde="pendiente", hacia="ejecutando")
    acciones.transicion(accion.id, desde="ejecutando", hacia="ejecutada", resultado="Tarifa cambiada.")
    tarea = motor.avanzar(tarea.id)
    assert tarea.paso("cambiar").estado == "hecho" and tarea.paso("cambiar").resultado.resumen == "Tarifa cambiada."
    assert tarea.paso("fin").estado == "esperando"
    acciones.close()


def test_a_rejected_action_fails_the_step_and_cancelling_rejects_pending_ones(entorno: Entorno) -> None:
    motor, tarea = _plan(entorno, ACCION)
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    acciones = AccionStore(entorno.db)
    accion_id = tarea.paso("cambiar").espera_actual.accion_id
    acciones.transicion(accion_id, desde="pendiente", hacia="rechazada")
    tarea = motor.avanzar(tarea.id)
    assert tarea.paso("cambiar").estado == "fallido" and tarea.estado == "fallida"
    assert tarea.paso("cambiar").error == "La rechazaste."

    # Retrying creates a NEW action (a new attempt); cancelling the task rejects it: it can never run.
    tarea = servicio.reintentar(motor, tarea.id, fuente="boton")
    nueva = tarea.paso("cambiar").espera_actual.accion_id
    assert nueva != accion_id and acciones.get(nueva).estado == "pendiente"
    servicio.cancelar(motor, tarea.id, fuente="boton")
    assert acciones.get(nueva).estado == "rechazada"
    acciones.close()


def test_pause_keeps_an_event_that_arrives_and_resume_handles_it(entorno: Entorno) -> None:
    motor, tarea = _plan(
        entorno,
        [
            {
                "id": "espera",
                "titulo": "Espera tu lectura",
                "tipo": "espera",
                "espera": {"evento": PERFIL_CAMBIADO, "hecho": "agua_lectura"},
            },
            {
                "id": "nota",
                "titulo": "Listo",
                "tipo": "nota",
                "descripcion": "Ya tengo tu lectura.",
                "depende_de": ["espera"],
            },
        ],
    )
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    assert tarea.estado == "esperando_evento"
    tarea = servicio.pausar(motor, tarea.id, fuente="boton")
    assert tarea.estado == "pausada"

    bus = Bus(entorno.db)
    bus.emitir(PERFIL_CAMBIADO, case_id=tarea.case_id, payload={"hechos": ["otra_cosa"]})
    bus.emitir(PERFIL_CAMBIADO, case_id=tarea.case_id, payload={"hechos": ["agua_lectura"]})
    bus.close()
    motor.ciclo()
    pausada = motor.store.get(tarea.id)
    assert pausada.estado == "pausada" and pausada.paso("espera").estado == "esperando"
    assert len(motor.store.senales(tarea.id)) == 1  # only the matching event, kept for later

    tarea = servicio.reanudar(motor, tarea.id, fuente="boton")
    assert tarea.estado == "completada"
    assert motor.store.senales(tarea.id) == []


def test_restart_recovery_repeats_nothing(entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    motor, tarea = _plan(entorno, ACCION)
    servicio.aceptar(motor, tarea.id, fuente="boton")  # leaves the action pending
    acciones = AccionStore(entorno.db)
    # A worker that crashes between creating the action and saving the task: the save fails once.
    motor2, tarea2 = _plan(entorno, ACCION, titulo="Otra tarifa del agua")
    original = TareaStore.guardar
    estado = {"fallar": True}

    def guardar_que_falla(self: TareaStore, t: Any, **kw: Any) -> Any:
        if estado["fallar"] and t.id == tarea2.id and t.paso("cambiar").estado == "esperando":
            estado["fallar"] = False
            raise RuntimeError("se cae el proceso")
        return original(self, t, **kw)

    monkeypatch.setattr(TareaStore, "guardar", guardar_que_falla)
    servicio_aceptado = None
    try:
        servicio_aceptado = servicio.aceptar(motor2, tarea2.id, fuente="boton")
    except RuntimeError:
        pass
    assert servicio_aceptado is None
    guardada = TareaStore(entorno.db).get(tarea2.id)
    assert guardada.estado == "activa" and guardada.paso("cambiar").estado == "pendiente"
    assert len(acciones.list(tarea2.case_id)) == 2  # the first task's and the crashed one's

    # A dead worker's lease expires; a fresh engine (after a restart) resumes and reuses the same action.
    TareaStore(entorno.db).reclamar(tarea2.id, "muerto", segundos=0.01)
    time.sleep(0.02)
    nuevo = entorno.motor(trabajador="nuevo")
    recuperada = nuevo.avanzar(tarea2.id)
    assert recuperada.paso("cambiar").estado == "esperando"
    assert recuperada.paso("cambiar").espera_actual.accion_id == id_accion(recuperada, recuperada.paso("cambiar"))
    assert len(acciones.list(tarea2.case_id)) == 2  # no duplicate
    acciones.close()


def test_a_live_lease_keeps_a_second_worker_out(entorno: Entorno) -> None:
    motor, tarea = _plan(entorno, ACCION)
    servicio.aceptar(motor, tarea.id, fuente="boton")
    assert TareaStore(entorno.db).reclamar(tarea.id, "otro", segundos=60)
    antes = motor.store.get(tarea.id)
    assert motor.avanzar(tarea.id) == antes  # untouched while the other worker holds it


class _Rota:
    clave = "documentos"
    nombre = "Documentos rotos"

    def __init__(self) -> None:
        self.operaciones = capacidades_por_defecto()["documentos"].operaciones
        self.llamadas = 0

    def ejecutar(self, operacion: str, parametros: Any, ctx: Any) -> ResultadoCapacidad:
        self.llamadas += 1
        if self.llamadas == 1:
            return ResultadoCapacidad(resumen="Servicio no disponible.", es_error=True)
        raise RuntimeError("fallo interno")


def test_failures_retry_with_backoff_then_surface_to_the_user(entorno: Entorno) -> None:
    rota = _Rota()
    capacidades = {**entorno.capacidades(), "documentos": rota}
    reloj = {"ahora": datetime.now(UTC)}
    case_id = entorno.caso()
    motor = entorno.motor(
        capacidades=capacidades,
        config=Configuracion(max_intentos=3, backoff_base_s=60.0),
        reloj=lambda: reloj["ahora"],
    )
    tarea, _ = servicio.proponer_plan(
        motor.store,
        case_id=case_id,
        user_id=None,
        titulo="Papeles",
        objetivo="quiero tener los papeles",
        pasos=[
            {
                "id": "lista",
                "titulo": "Lista de papeles",
                "tipo": "capacidad",
                "capacidad": "documentos",
                "operacion": "lista_de_comprobacion",
                "parametros": {"origen": "renta"},
            }
        ],
    )
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    paso = tarea.paso("lista")
    assert paso.estado == "pendiente" and paso.intentos == 1 and paso.error == "Servicio no disponible."
    assert paso.siguiente_intento == reloj["ahora"] + timedelta(seconds=60)
    assert tarea.estado == "esperando_evento"
    assert motor.avanzar(tarea.id).paso("lista").intentos == 1  # not before its time

    reloj["ahora"] += timedelta(seconds=61)
    paso = motor.avanzar(tarea.id).paso("lista")
    assert paso.intentos == 2 and paso.siguiente_intento == reloj["ahora"] + timedelta(seconds=120)
    reloj["ahora"] += timedelta(seconds=121)
    tarea = motor.avanzar(tarea.id)
    assert tarea.paso("lista").estado == "fallido" and tarea.estado == "fallida"
    avisos = [n for n in motor.avisos().list(case_id) if "No he podido completar" in n.title]
    assert len(avisos) == 1 and avisos[0].severity == "aviso"
    assert "fallo interno" not in tarea.paso("lista").error  # internals stay in the log

    rota.llamadas = 99  # it works now
    rota.ejecutar = lambda *a, **k: ResultadoCapacidad(resumen="Lista hecha.")  # type: ignore[method-assign]
    assert servicio.reintentar(motor, tarea.id, fuente="boton").estado == "completada"


def test_a_tool_that_needs_data_asks_the_user_and_reruns_on_confirm(entorno: Entorno) -> None:
    entorno.llamadas.errores["agua_lectura"] = "Falta el número de contador."
    motor, tarea = _plan(entorno, ACCION[:1])
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    espera = tarea.paso("leer").espera_actual
    assert espera.motivo == "usuario" and espera.reintentar and "Falta el número" in espera.detalle
    del entorno.llamadas.errores["agua_lectura"]
    tarea = servicio.confirmar_paso(motor, tarea.id, "leer", fuente="boton")
    assert tarea.estado == "completada"
    assert entorno.llamadas.nombres().count("agua_lectura") == 2


def test_a_module_that_is_off_blocks_until_switched_on_or_is_skipped_if_optional(entorno: Entorno) -> None:
    entorno.activos.discard("agua")
    motor, tarea = _plan(
        entorno,
        [
            {
                "id": "opc",
                "titulo": "Lectura opcional",
                "tipo": "calculo",
                "herramienta": "agua_lectura",
                "opcional": True,
            },
            {"id": "obl", "titulo": "Lectura obligatoria", "tipo": "calculo", "herramienta": "agua_lectura"},
        ],
    )
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    assert tarea.paso("opc").estado == "omitido"
    assert tarea.paso("obl").espera_actual.motivo == "modulo" and tarea.estado == "esperando_usuario"
    entorno.activos.add("agua")
    assert motor.avanzar(tarea.id).estado == "completada"


def test_consultations_wait_for_a_worker_with_a_model(entorno: Entorno) -> None:
    case_id = entorno.caso()
    api = entorno.motor(ejecutar_consultas=False)
    tarea, _ = servicio.proponer_plan(
        api.store,
        case_id=case_id,
        user_id=None,
        titulo="Pregunta al especialista",
        objetivo="quiero saber si me compensa",
        pasos=[
            {
                "id": "q",
                "titulo": "Pregunta",
                "tipo": "consulta",
                "modulo": "luz",
                "pregunta": "¿Me compensa la tarifa?",
            }
        ],
    )
    assert servicio.aceptar(api, tarea.id, fuente="boton").estado == "activa"
    assert entorno.consultas == []
    worker = entorno.motor()
    worker.ciclo()
    tarea = worker.store.get(tarea.id)
    assert tarea.estado == "completada" and entorno.consultas == [("luz", "¿Me compensa la tarifa?")]


def test_a_deleted_case_takes_its_tasks_with_it(entorno: Entorno) -> None:
    motor, tarea = _plan(entorno, ACCION)
    servicio.aceptar(motor, tarea.id, fuente="boton")
    assert entorno.casos.delete_case(tarea.case_id)
    with sqlite3.connect(entorno.db) as conn:
        for tabla in ("tareas", "tareas_historial", "tareas_senales"):
            assert conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE case_id = ?", (tarea.case_id,)).fetchone()[0] == 0


def test_the_platform_worker_runs_the_engine_as_a_service(entorno: Entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from asistente.nucleo.tareas import registrar_en_plataforma
    from asistente.plataforma import procesos, settings

    registro = procesos.Registro()
    registrar_en_plataforma(registro)
    servicio = registro.servicios["tareas"]
    assert procesos.registro().servicios["tareas"].arrancar is servicio.arrancar  # found through EXTENSIONES

    motor, tarea = _plan(entorno, ACCION)
    with sqlite3.connect(entorno.db) as conn:  # accepted by hand: the service must advance it on its own
        datos = conn.execute("SELECT payload_json FROM tareas WHERE id = ?", (tarea.id,)).fetchone()[0]
        conn.execute(
            "UPDATE tareas SET estado = 'activa', payload_json = ? WHERE id = ?",
            (datos.replace('"estado":"propuesta"', '"estado":"activa"'), tarea.id),
        )
    monkeypatch.setattr(settings, "TAREAS_INTERVALO_SEGUNDOS", 1, raising=False)
    parar = threading.Event()
    contexto = procesos.ContextoServicio(db_path=entorno.db, nombre_proceso="prueba", parar=parar)
    hilo = threading.Thread(target=servicio.arrancar, args=(contexto,))
    hilo.start()
    for _ in range(50):
        if motor.store.get(tarea.id).paso("leer").estado != "pendiente":
            break
        time.sleep(0.05)
    parar.set()
    hilo.join(5)
    assert not hilo.is_alive()
    # Advanced by the service alone (with its default module switches the water module may be off: then it waits).
    assert motor.store.get(tarea.id).paso("leer").estado in ("hecho", "esperando")

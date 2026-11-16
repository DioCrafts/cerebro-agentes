"""Each template end to end with fake modules, the real bus and time travel."""

from __future__ import annotations

from datetime import date, timedelta
import json
import sqlite3

from hacienda_gpt.eventos import productores
from hacienda_gpt.memoria.servicio import decisiones_del_caso
from hacienda_gpt.modulos import registro as registro_mod
from hacienda_gpt.proactive.store import NotificationStore
from hacienda_gpt.tareas import servicio
from hacienda_gpt.tareas.motor import MODULO_TEMPORIZADOR
from hacienda_gpt.tareas.plantillas import instanciar, plantillas
from .conftest import HOY, Entorno


def _avisos(env: Entorno, case_id: str = "c1") -> list:
    store = NotificationStore(env.db)
    try:
        return [n for n in store.list(case_id) if n.kind == "modulo:tareas"]
    finally:
        store.close()


def _estados(tarea) -> dict[str, str]:
    return {p.id: p.estado for p in tarea.pasos}


def test_every_template_passes_the_gate_with_the_real_modules() -> None:
    registro_mod.reiniciar()
    try:
        assert {p.id for p in plantillas()} == {"renovar_pasaporte", "preparar_renta", "cambiar_tarifa_luz"}
        for plantilla in plantillas():
            tarea, _avisos_gate = instanciar(
                plantilla.id, case_id="c1", user_id=None, objetivo="quiero hacerlo cuanto antes", hoy=HOY
            )
            assert tarea.estado == "propuesta" and tarea.plantilla_version == plantilla.version
            assert all(p.estado == "pendiente" for p in tarea.pasos)
    finally:
        registro_mod.reiniciar()


def test_renovar_pasaporte(entorno: Entorno) -> None:
    case_id = entorno.caso()
    motor = entorno.motor()
    tarea, _ = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="renovar_pasaporte",
        objetivo="Tengo que renovar el pasaporte antes de mi viaje",
        hoy=entorno.dia(),
    )
    # A proposal never runs.
    assert motor.avanzar(tarea.id).estado == "propuesta"
    assert entorno.llamadas.registro == []

    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    assert _estados(tarea) == {
        "caducidad": "hecho",
        "requisitos": "hecho",
        "informacion": "hecho",
        "cita": "esperando",
        "recordatorio": "pendiente",
        "renovado": "pendiente",
    }
    assert tarea.estado == "esperando_usuario"
    requisitos = tarea.paso("requisitos").resultado
    assert "BOE-A-2003-13978 (art. 4.1 RD 896/2003)" in requisitos.evidencias
    informacion = tarea.paso("informacion").resultado
    # Research stays on the official roots: the non-official result is dropped.
    assert "pasaportes-rapidos" not in informacion.resumen and "descartado 1" in informacion.resumen
    assert entorno.busquedas[0][1] == ["https://www.citapreviadnie.es/", "https://www.policia.es/"]
    assert any(n.key.endswith(":cita") for n in _avisos(entorno))

    cita = HOY + timedelta(days=20)
    tarea = servicio.confirmar_paso(motor, tarea.id, "cita", fuente="boton", fecha=cita)
    assert tarea.paso("recordatorio").estado == "esperando"
    assert tarea.paso("recordatorio").espera_actual.hasta == cita - timedelta(days=1)
    assert tarea.estado == "esperando_evento" and tarea.proxima_revision == cita - timedelta(days=1)
    # The bus keeps the case's timer for the tasks.
    assert (case_id, MODULO_TEMPORIZADOR, cita - timedelta(days=1)) in motor.bus().temporizadores()

    entorno.dia.avanzar(18)
    motor.ciclo()
    assert motor.store.get(tarea.id).paso("recordatorio").estado == "esperando"
    entorno.dia.avanzar(1)  # the eve of the appointment: the timer fires, the reminder is written
    resumen = motor.ciclo()
    assert resumen["temporizadores"] >= 1
    tarea = motor.store.get(tarea.id)
    assert tarea.paso("recordatorio").estado == "hecho" and tarea.paso("renovado").estado == "esperando"
    assert any(n.key.endswith(":recordatorio") for n in _avisos(entorno))

    tarea = servicio.confirmar_paso(motor, tarea.id, "renovado", fuente="boton")
    assert tarea.estado == "completada" and tarea.terminada is not None
    # The audit trail has no values: neither the goal nor the date of the appointment.
    with sqlite3.connect(entorno.db) as conn:
        historial = " ".join(r[0] for r in conn.execute("SELECT detalle_json FROM tareas_historial"))
    assert "viaje" not in historial and cita.isoformat() not in historial
    eventos = [e.evento for e in motor.store.historial(tarea.id)]
    assert eventos[0] == "propuesta" and "aceptada" in eventos and eventos[-1] == "estado"


def test_renovar_pasaporte_without_a_date_has_no_reminder(entorno: Entorno) -> None:
    case_id = entorno.caso()
    motor = entorno.motor()
    tarea, _ = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="renovar_pasaporte",
        objetivo="renovar el pasaporte",
        hoy=HOY,
    )
    servicio.aceptar(motor, tarea.id, fuente="boton")
    tarea = servicio.confirmar_paso(motor, tarea.id, "cita", fuente="boton")
    assert tarea.paso("recordatorio").estado == "hecho"
    assert "No me diste fecha" in tarea.paso("recordatorio").resultado.resumen
    assert tarea.paso("renovado").estado == "esperando"


def test_preparar_renta(entorno: Entorno) -> None:
    case_id = entorno.caso(rendimiento_trabajo_bruto=30000, retenciones=4000, donativos=150)
    motor = entorno.motor()
    tarea, avisos = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="preparar_renta",
        objetivo="quiero preparar la renta este año",
        hoy=HOY,
        modulos_activos=sorted(entorno.activos),
    )
    assert tarea.parametros == {"ejercicio": 2025}
    assert any("Correo" in a for a in avisos)  # the e-mail module is off: the optional step will be skipped
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    assert tarea.paso("obligacion").estado == "hecho" and "SÍ" in tarea.paso("obligacion").resultado.resumen
    assert ("obligacion_declarar", {"ejercicio": 2025}) in entorno.llamadas.registro
    assert tarea.paso("correo").estado == "omitido"
    assert tarea.paso("plazos").estado == "hecho"
    assert tarea.paso("datos").estado == "esperando" and tarea.estado == "esperando_usuario"

    tarea = servicio.confirmar_paso(motor, tarea.id, "datos", fuente="boton")
    assert [tarea.paso(p).estado for p in ("irpf", "optimizar", "papeles")] == ["hecho"] * 3
    assert "1.234,56" in tarea.paso("irpf").resultado.resumen
    papeles = tarea.paso("papeles").resultado.resumen
    assert "orientativa" in papeles and "retenciones" in papeles and "donativos" in papeles
    recordatorio = tarea.paso("recordatorio")
    assert recordatorio.estado == "esperando" and recordatorio.espera_actual.motivo == "fecha"
    # The calendar's deadline minus 30 days; the calendar says when its date is not verified.
    fecha = recordatorio.espera_actual.hasta
    assert fecha is not None and fecha > HOY
    assert tarea.estado == "esperando_evento"

    entorno.dia.valor = fecha
    motor.ciclo()
    tarea = motor.store.get(tarea.id)
    assert tarea.paso("recordatorio").estado == "hecho"
    assert tarea.paso("presentada").estado == "esperando"
    tarea = servicio.confirmar_paso(motor, tarea.id, "presentada", fuente="boton")
    assert tarea.estado == "completada"


def test_cambiar_tarifa_luz_waits_for_the_consumption_event(entorno: Entorno) -> None:
    productores.instalar()  # the real producer: saving the fact writes perfil.cambiado in the same transaction
    case_id = entorno.caso(luz_potencia_p1_kw=4.6)
    motor = entorno.motor()
    tarea, _ = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="cambiar_tarifa_luz",
        objetivo="quiero pagar menos de luz",
        hoy=HOY,
    )
    tarea = servicio.aceptar(motor, tarea.id, fuente="boton")
    assert tarea.paso("consumo").estado == "hecho"
    assert "Descarga de Datadis" in tarea.paso("consumo").resultado.resumen  # no browser agent here
    assert tarea.paso("llega_consumo").estado == "esperando" and tarea.estado == "esperando_evento"
    assert "luz_comparar_tarifas" not in entorno.llamadas.nombres()

    motor.ciclo()  # an unrelated cycle changes nothing
    assert motor.store.get(tarea.id).paso("llega_consumo").estado == "esperando"
    entorno.caso(luz_potencia_p1_kw=4.6, luz_consumo="[]")  # the CSV arrived (any path that saves the fact)
    motor.ciclo()
    tarea = motor.store.get(tarea.id)
    assert tarea.paso("llega_consumo").estado == "hecho"
    assert tarea.paso("llega_consumo").resultado.evidencias[0].startswith("evento:")
    assert tarea.paso("comparar").estado == "hecho" and tarea.paso("decidir").estado == "esperando"

    tarea = servicio.confirmar_paso(motor, tarea.id, "decidir", fuente="boton")
    borrador = tarea.paso("borrador").resultado.borrador
    assert borrador is not None and borrador.startswith("BORRADOR") and "no lo envía" in borrador
    assert tarea.paso("enviada").estado == "esperando"
    assert servicio.confirmar_paso(motor, tarea.id, "enviada", fuente="boton").estado == "completada"


def test_cambiar_tarifa_luz_cancelled_with_a_reason_is_remembered(entorno: Entorno) -> None:
    case_id = entorno.caso()
    motor = entorno.motor()
    tarea, _ = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="cambiar_tarifa_luz",
        objetivo="quiero pagar menos de luz",
        hoy=HOY,
    )
    servicio.aceptar(motor, tarea.id, fuente="boton")
    servicio.omitir_paso(motor, tarea.id, "llega_consumo", fuente="boton")
    assert motor.store.get(tarea.id).paso("decidir").estado == "esperando"
    assert _avisos(entorno)

    tarea = servicio.cancelar(motor, tarea.id, fuente="boton", motivo="Tengo permanencia hasta marzo")
    assert tarea.estado == "cancelada" and tarea.paso("decidir").estado == "cancelado"
    decisiones = decisiones_del_caso(entorno.db, case_id)
    assert [(d.ambito, d.tipo, d.razon) for d in decisiones] == [
        ("luz:cambio_tarifa", "rechazo", "Tengo permanencia hasta marzo")
    ]
    assert tarea.vinculos.decisiones == [decisiones[0].id]
    assert _avisos(entorno) == []  # its notices left the inbox
    # Nothing of it runs again.
    entorno.dia.avanzar(30)
    motor.ciclo()
    assert motor.store.get(tarea.id).estado == "cancelada"


def test_an_event_wait_times_out_and_asks_the_user(entorno: Entorno) -> None:
    case_id = entorno.caso()
    motor = entorno.motor()
    tarea, _ = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="cambiar_tarifa_luz",
        objetivo="quiero pagar menos de luz",
        hoy=HOY,
    )
    servicio.aceptar(motor, tarea.id, fuente="boton")
    entorno.dia.avanzar(21)
    motor.ciclo()
    tarea = motor.store.get(tarea.id)
    espera = tarea.paso("llega_consumo").espera_actual
    assert espera.motivo == "usuario" and "No ha llegado" in espera.detalle
    assert tarea.estado == "esperando_usuario"
    tarea = servicio.confirmar_paso(motor, tarea.id, "llega_consumo", fuente="boton")
    assert tarea.paso("comparar").estado == "hecho"


def test_export_holds_the_task_and_its_history(entorno: Entorno) -> None:
    from hacienda_gpt.tareas.almacen import exportar_tareas

    case_id = entorno.caso()
    motor = entorno.motor()
    tarea, _ = servicio.proponer_desde_plantilla(
        motor.store,
        case_id=case_id,
        user_id=None,
        plantilla_id="renovar_pasaporte",
        objetivo="renovar el pasaporte",
        hoy=HOY,
    )
    exportado = exportar_tareas(entorno.db, case_id)
    assert [t["id"] for t in exportado] == [tarea.id]
    assert exportado[0]["objetivo"] == "renovar el pasaporte" and exportado[0]["historial"][0]["evento"] == "propuesta"
    json.dumps(exportado)  # plain JSON
    assert exportar_tareas(entorno.db, "otro") == []
    assert date.fromisoformat(exportado[0]["vence"]) == HOY + timedelta(days=180)

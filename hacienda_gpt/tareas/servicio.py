"""What the user (through the API or the chat) can do with a task. Every change is checked here.

* propose from a template or from a plan (the gate, :mod:`~hacienda_gpt.tareas.validacion`): the task is stored
  as ``propuesta`` and NOTHING runs until the user accepts it (:func:`aceptar`);
* confirm a step that waits for the user (with an optional date: the day of the appointment), skip one, retry a
  failed one; a pending ACTION is never confirmed here: only its own button runs it
  (``POST /acciones/{id}/confirmar``);
* pause, resume, cancel. Cancelling rejects the task's pending actions (they can never run), removes its
  notices, and, when the user gives a reason, records it in the decision memory (``hacienda_gpt.memoria``) on
  the template's ámbito, so the assistant does not propose it again while that decision is in force.

Each change is saved over the version it read (a concurrent engine pass makes it retry) and leaves a line in the
task's history; the engine then advances the task at once.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
import logging
from typing import Any, Literal

from hacienda_gpt.tareas.almacen import ConflictoDeVersionError, TareaStore
from hacienda_gpt.tareas.modelo import EN_MARCHA, PASO_FINAL, TERMINALES, Paso, Resultado, Tarea, ahora
from hacienda_gpt.tareas.motor import PREFIJO_AVISO, Motor
from hacienda_gpt.tareas.plantillas import instanciar
from hacienda_gpt.tareas.validacion import validar_plan

logger = logging.getLogger(__name__)

Fuente = Literal["chat", "boton"]


class TareaNoEncontradaError(LookupError):
    pass


class OperacionNoPermitidaError(ValueError):
    """The change makes no sense in the task's current state (Spanish message, for the user)."""


def _mutar(
    store: TareaStore,
    tarea_id: str,
    cambio: Callable[[Tarea], tuple[str, str | None, dict[str, Any]]],
) -> Tarea:
    for _ in range(5):
        tarea = store.get(tarea_id)
        if tarea is None:
            raise TareaNoEncontradaError(tarea_id)
        copia = tarea.model_copy(deep=True)
        evento, paso_id, detalle = cambio(copia)
        try:
            return store.guardar(copia, evento=evento, paso_id=paso_id, **detalle)
        except ConflictoDeVersionError:
            continue
    raise OperacionNoPermitidaError("La tarea está cambiando ahora mismo; inténtalo de nuevo.")


# --------------------------------------------------------------------------- #
# Proposing
# --------------------------------------------------------------------------- #


def proponer_desde_plantilla(
    store: TareaStore,
    *,
    case_id: str,
    user_id: str | None,
    plantilla_id: str,
    objetivo: str,
    hoy: date,
    parametros: Mapping[str, Any] | None = None,
    modulos_activos: Sequence[str] | None = None,
    reg: Any = None,
    capacidades: Any = None,
) -> tuple[Tarea, list[str]]:
    tarea, avisos = instanciar(
        plantilla_id,
        case_id=case_id,
        user_id=user_id,
        objetivo=objetivo,
        hoy=hoy,
        parametros=parametros,
        reg=reg,
        capacidades=capacidades,
        modulos_activos=modulos_activos,
    )
    store.add(tarea, evento="propuesta", origen="plantilla", plantilla=tarea.plantilla, version=tarea.plantilla_version)
    return tarea, avisos


def proponer_plan(
    store: TareaStore,
    *,
    case_id: str,
    user_id: str | None,
    titulo: str,
    objetivo: str,
    pasos: Sequence[Any],
    modulos_activos: Sequence[str] | None = None,
    reg: Any = None,
    capacidades: Any = None,
) -> tuple[Tarea, list[str]]:
    """A custom plan (a model wrote it): through the gate, stored as a proposal the user must accept."""
    from hacienda_gpt.tareas.validacion import PlanInvalidoError, _problemas_de_texto

    problemas = _problemas_de_texto("titulo", titulo)
    if problemas:
        raise PlanInvalidoError(problemas)
    plan = validar_plan(pasos, reg=reg, capacidades=capacidades, modulos_activos=modulos_activos, origen="plan")
    tarea = Tarea(
        case_id=case_id,
        user_id=user_id,
        titulo=titulo,
        objetivo=objetivo,
        origen="plan",
        pasos=plan.pasos,
        ambito_decision="tareas:personalizada",
    )
    store.add(tarea, evento="propuesta", origen="plan", pasos=len(plan.pasos))
    return tarea, plan.avisos


# --------------------------------------------------------------------------- #
# The user's decisions
# --------------------------------------------------------------------------- #


def aceptar(motor: Motor, tarea_id: str, *, fuente: Fuente) -> Tarea:
    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        if tarea.estado != "propuesta":
            raise OperacionNoPermitidaError("Esta tarea ya no es una propuesta.")
        tarea.estado = "activa"
        tarea.aceptada = ahora()
        return "aceptada", None, {"fuente": fuente}

    _mutar(motor.store, tarea_id, cambio)
    return motor.avanzar(tarea_id) or _get(motor.store, tarea_id)


def _paso(tarea: Tarea, paso_id: str) -> Paso:
    paso = tarea.paso(paso_id)
    if paso is None:
        raise TareaNoEncontradaError(f"{tarea.id}/{paso_id}")
    return paso


def _en_marcha(tarea: Tarea) -> None:
    if tarea.estado not in EN_MARCHA and tarea.estado != "fallida":
        raise OperacionNoPermitidaError(
            "La tarea está "
            + {"propuesta": "propuesta: acéptala antes", "pausada": "en pausa: reanúdala antes"}.get(
                tarea.estado, tarea.estado
            )
            + "."
        )


def confirmar_paso(
    motor: Motor, tarea_id: str, paso_id: str, *, fuente: Fuente, fecha: date | None = None, nota: str = ""
) -> Tarea:
    """The user says a waiting step is done (or, when a tool asked for data, that it can run again)."""

    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        _en_marcha(tarea)
        paso = _paso(tarea, paso_id)
        espera = paso.espera_actual
        if paso.estado != "esperando" or espera is None:
            raise OperacionNoPermitidaError("Ese paso no está esperando tu confirmación.")
        if espera.motivo == "accion":
            raise OperacionNoPermitidaError(
                "Ese paso es una acción: se confirma con su botón «Confirmar» de acciones pendientes, que la ejecuta "
                "una sola vez."
            )
        if espera.reintentar or espera.motivo == "modulo":
            paso.estado, paso.espera_actual, paso.error = "pendiente", None, None
            return "paso_reintento_pedido", paso.id, {"fuente": fuente}
        anterior = paso.resultado
        resumen = "Confirmado por ti." + (f" {nota.strip()[:300]}" if nota.strip() else "")
        if anterior is not None and anterior.resumen:
            resumen = f"{anterior.resumen}\n{resumen}"
        paso.resultado = Resultado(
            resumen=resumen[:4000],
            evidencias=(anterior.evidencias if anterior else []) + [f"confirmado_por_usuario:{fuente}"],
            fecha=fecha,
            borrador=anterior.borrador if anterior else None,
        )
        paso.estado, paso.espera_actual, paso.terminado = "hecho", None, ahora()
        return "paso_confirmado", paso.id, {"fuente": fuente, "con_fecha": fecha is not None}

    _mutar(motor.store, tarea_id, cambio)
    return motor.avanzar(tarea_id) or _get(motor.store, tarea_id)


def omitir_paso(motor: Motor, tarea_id: str, paso_id: str, *, fuente: Fuente) -> Tarea:
    """Skip an optional step or one that waits; a waiting action is rejected first (it can never run)."""
    accion_a_rechazar: list[str] = []

    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        _en_marcha(tarea)
        paso = _paso(tarea, paso_id)
        if paso.estado in PASO_FINAL and paso.estado != "fallido":
            raise OperacionNoPermitidaError("Ese paso ya está terminado.")
        if not (paso.opcional or paso.estado in ("esperando", "fallido")):
            raise OperacionNoPermitidaError("Ese paso no se puede saltar todavía.")
        accion_a_rechazar.clear()
        if paso.espera_actual is not None and paso.espera_actual.accion_id:
            accion_a_rechazar.append(paso.espera_actual.accion_id)
        paso.estado, paso.espera_actual, paso.error, paso.terminado = "omitido", None, None, ahora()
        paso.resultado = Resultado(resumen="Lo has saltado.", evidencias=[f"omitido_por_usuario:{fuente}"])
        return "paso_omitido", paso.id, {"fuente": fuente}

    _mutar(motor.store, tarea_id, cambio)
    for accion_id in accion_a_rechazar:
        motor.acciones().transicion(accion_id, desde="pendiente", hacia="rechazada")
    return motor.avanzar(tarea_id) or _get(motor.store, tarea_id)


def reintentar(motor: Motor, tarea_id: str, *, fuente: Fuente) -> Tarea:
    """Failed steps (and steps that asked for data) run again."""

    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        _en_marcha(tarea)
        n = 0
        for paso in tarea.pasos:
            if paso.estado == "fallido" or (
                paso.estado == "esperando" and paso.espera_actual is not None and paso.espera_actual.reintentar
            ):
                paso.estado, paso.espera_actual, paso.error = "pendiente", None, None
                paso.siguiente_intento, paso.terminado = None, None
                n += 1
        if not n:
            raise OperacionNoPermitidaError("No hay ningún paso que reintentar.")
        if tarea.estado == "fallida":
            tarea.estado = "activa"
        return "reintento_pedido", None, {"fuente": fuente, "pasos": n}

    _mutar(motor.store, tarea_id, cambio)
    return motor.avanzar(tarea_id) or _get(motor.store, tarea_id)


def pausar(motor: Motor, tarea_id: str, *, fuente: Fuente) -> Tarea:
    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        if tarea.estado not in EN_MARCHA and tarea.estado != "fallida":
            raise OperacionNoPermitidaError("Solo se puede pausar una tarea en marcha.")
        anterior = tarea.estado
        tarea.estado = "pausada"
        tarea.proxima_revision = None
        return "pausada", None, {"fuente": fuente, "antes": anterior}

    tarea = _mutar(motor.store, tarea_id, cambio)
    motor._programar(tarea.case_id)
    return tarea


def reanudar(motor: Motor, tarea_id: str, *, fuente: Fuente) -> Tarea:
    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        if tarea.estado != "pausada":
            raise OperacionNoPermitidaError("La tarea no está en pausa.")
        tarea.estado = "activa"  # the engine recomputes the real state at once
        return "reanudada", None, {"fuente": fuente}

    _mutar(motor.store, tarea_id, cambio)
    return motor.avanzar(tarea_id) or _get(motor.store, tarea_id)


def cancelar(
    motor: Motor, tarea_id: str, *, fuente: Fuente, motivo: str | None = None, user_id: str | None = None
) -> Tarea:
    """Cancel: nothing of it runs again. Its pending actions are rejected; with a reason, a decision is kept."""
    motivo = " ".join((motivo or "").split())[:500] or None

    def cambio(tarea: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        if tarea.estado in TERMINALES:
            raise OperacionNoPermitidaError("La tarea ya está terminada.")
        tarea.estado = "cancelada"
        tarea.terminada = ahora()
        tarea.proxima_revision = None
        tarea.motivo_cancelacion = motivo
        for paso in tarea.pasos:
            if paso.estado not in PASO_FINAL:
                paso.estado, paso.espera_actual, paso.siguiente_intento = "cancelado", None, None
        return "cancelada", None, {"fuente": fuente, "con_motivo": motivo is not None}

    tarea = _mutar(motor.store, tarea_id, cambio)
    rechazadas = 0
    for accion_id in tarea.vinculos.acciones:
        if motor.acciones().transicion(accion_id, desde="pendiente", hacia="rechazada") is not None:
            rechazadas += 1
    try:
        motor.avisos().remove_superseded(tarea.case_id, key_prefix=f"{PREFIJO_AVISO}{tarea.id}", keep_key="")
    except Exception:
        logger.exception("Notices of a cancelled task could not be removed")
    if motivo is not None:
        tarea = _decision(motor, tarea, motivo=motivo, fuente=fuente, user_id=user_id)
    if rechazadas:
        motor.store.anotar(tarea, "acciones_rechazadas", None, n=rechazadas)
    motor._programar(tarea.case_id)
    return tarea


def _decision(motor: Motor, tarea: Tarea, *, motivo: str, fuente: Fuente, user_id: str | None) -> Tarea:
    from hacienda_gpt.memoria.servicio import DecisionInvalidaError, registrar

    ambito = tarea.ambito_decision or f"tareas:{tarea.plantilla or 'personalizada'}"
    try:
        resultado = registrar(
            motor.db_path,
            case_id=tarea.case_id,
            user_id=user_id or tarea.user_id,
            ambito=ambito,
            tipo="rechazo",
            razon=motivo,
            fuente=fuente,
            propuesta={"titulo": tarea.titulo[:300]},
        )
    except DecisionInvalidaError:
        logger.exception("The cancellation reason of a task could not be recorded as a decision")
        return tarea

    def cambio(t: Tarea) -> tuple[str, str | None, dict[str, Any]]:
        t.vinculos.decisiones.append(resultado.decision.id)
        return "decision_registrada", None, {"ambito": ambito}

    return _mutar(motor.store, tarea.id, cambio)


def _get(store: TareaStore, tarea_id: str) -> Tarea:
    tarea = store.get(tarea_id)
    if tarea is None:
        raise TareaNoEncontradaError(tarea_id)
    return tarea


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

ETIQUETAS_ESTADO = {
    "propuesta": "propuesta (pendiente de que la aceptes)",
    "activa": "en marcha",
    "pausada": "en pausa",
    "esperando_usuario": "esperando algo de ti",
    "esperando_evento": "esperando una fecha o un aviso",
    "completada": "completada",
    "cancelada": "cancelada",
    "fallida": "con un paso que ha fallado",
}


def lo_que_espera(tarea: Tarea) -> list[dict[str, Any]]:
    """The steps waiting now, with why, for the user."""
    return [
        {
            "paso": p.id,
            "titulo": p.titulo,
            "motivo": p.espera_actual.motivo,
            "detalle": p.espera_actual.detalle,
            "hasta": p.espera_actual.hasta.isoformat() if p.espera_actual.hasta else None,
            "accion_id": p.espera_actual.accion_id,
            "reintentar": p.espera_actual.reintentar,
        }
        for p in tarea.pasos
        if p.estado == "esperando" and p.espera_actual is not None
    ]


def resumen(tarea: Tarea) -> dict[str, Any]:
    hechos = sum(1 for p in tarea.pasos if p.estado in ("hecho", "omitido"))
    return {
        "id": tarea.id,
        "ref": tarea.ref,
        "titulo": tarea.titulo,
        "objetivo": tarea.objetivo,
        "plantilla": tarea.plantilla,
        "estado": tarea.estado,
        "estado_texto": ETIQUETAS_ESTADO[tarea.estado],
        "pasos_hechos": hechos,
        "pasos_total": len(tarea.pasos),
        "esperando": lo_que_espera(tarea),
        "proxima_revision": tarea.proxima_revision.isoformat() if tarea.proxima_revision else None,
        "vence": tarea.vence.isoformat() if tarea.vence else None,
        "creada": tarea.creada.isoformat(),
    }


__all__ = [
    "ETIQUETAS_ESTADO",
    "Fuente",
    "OperacionNoPermitidaError",
    "TareaNoEncontradaError",
    "aceptar",
    "cancelar",
    "confirmar_paso",
    "lo_que_espera",
    "omitir_paso",
    "pausar",
    "proponer_desde_plantilla",
    "proponer_plan",
    "reanudar",
    "reintentar",
    "resumen",
]

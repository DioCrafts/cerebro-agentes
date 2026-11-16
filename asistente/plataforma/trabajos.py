"""The worker's built-in services and scheduled jobs: the cron lines of docs/operacion.md, as data.

Each job runs the same command as its documented cron line (``ops run <nombre> -- python -m …``), at the same
time (Spanish time), so the watchdog (``ops doctor``) and the healthcheck pings see no difference. Jobs that
write into the repository's data files (the parameter autopilot) or need the corpus and its models (the
search index) are off by default: turn them on with ``PLATAFORMA_TRABAJOS``.
"""

from __future__ import annotations

from datetime import date, time
import logging
from typing import Any

from asistente.plataforma import settings
from asistente.plataforma.procesos import ContextoServicio, Programacion, Registro, Servicio, Trabajo

logger = logging.getLogger(__name__)

LUNES = 0


def _diario(hora: int, minuto: int) -> Programacion:
    return Programacion(hora=time(hora, minuto))


TRABAJOS: tuple[Trabajo, ...] = (
    Trabajo(
        "hipoteca-indices",
        "Índices de referencia hipotecarios publicados en el BOE (módulo hipoteca).",
        Programacion(hora=time(6, 20), dias_mes=frozenset({4, 19})),
        orden=("asistente.ambitos.hipoteca.indices", "actualizar"),
    ),
    Trabajo(
        "boe-watch",
        "Normas del catálogo que han cambiado en el BOE.",
        _diario(6, 30),
        orden=("asistente.plataforma.cli.boe_watch",),
    ),
    Trabajo(
        "boe-impact",
        "Aviso a los usuarios afectados por los cambios del BOE.",
        _diario(6, 45),
        orden=(
            "asistente.plataforma.cli.proactive",
            "boe-impact",
            "--changes-log",
            "data/html/_boe_changes.jsonl",
            "--snapshot-dir",
            "data/html",
            "--yes",
        ),
    ),
    Trabajo(
        "boe-novedades",
        "Normas nuevas con contenido tributario en el BOE del día.",
        _diario(6, 50),
        orden=("asistente.plataforma.cli.proactive", "boe-novedades"),
    ),
    Trabajo(
        "params-autopilot",
        "Piloto de parámetros (escribe en asistente/ambitos/fiscal/data; desactivado por defecto).",
        _diario(6, 55),
        orden=("asistente.plataforma.cli.proactive", "params-autopilot", "--no-detect"),
        por_defecto=False,
    ),
    Trabajo(
        "run-daily",
        "Recordatorios de plazos, plan de fin de ejercicio y avisos de los módulos activados.",
        _diario(7, 0),
        orden=("asistente.plataforma.cli.proactive", "run-daily"),
    ),
    Trabajo(
        "deliver",
        "Entrega de los avisos fuera de la aplicación (webhook, correo, push).",
        _diario(7, 15),
        orden=("asistente.plataforma.cli.ops", "deliver"),
        # ``ops deliver`` records itself under this name.
        vigilado=False,
    ),
    Trabajo(
        "run-weekly",
        "Resumen semanal de cada caso (los lunes).",
        Programacion(hora=time(7, 30), dias_semana=frozenset({LUNES})),
        orden=("asistente.plataforma.cli.proactive", "run-weekly"),
    ),
    Trabajo(
        "doctor",
        "Revisión: ¿ha corrido todo? Avisa al operador si algo falta o falló.",
        _diario(9, 0),
        orden=("asistente.plataforma.cli.ops", "doctor"),
    ),
    Trabajo(
        "correo-sincronizar",
        "Sincroniza Gmail de los casos conectados con el módulo de correo activado.",
        Programacion(cada_minutos=30),
        orden=("asistente.capacidades.correo", "sincronizar"),
    ),
    Trabajo(
        "indice-corpus",
        "Actualización incremental del índice de búsqueda del corpus (necesita el corpus y los modelos).",
        _diario(3, 40),
        orden=("asistente.plataforma.cli.processor", "--incremental"),
        por_defecto=False,
    ),
)

# Finished deliveries and consumed events are kept this long (the same as ``cli.eventos``).
DIAS_RETENCION_EVENTOS = 30


def pasada_de_eventos(db_path: str, nombre: str) -> dict[str, Any]:
    """One full pass of the event bus (what ``cli.eventos worker --una-vez`` does)."""
    from asistente.nucleo.eventos.bus import Bus, consumidores_del_trabajador

    bus = Bus(db_path, consumidores=consumidores_del_trabajador())
    try:
        resumen: dict[str, Any] = {"reconciliados": bus.reconciliar(), **bus.drenar(nombre)}
        resumen["purgado"] = bus.purgar(dias=DIAS_RETENCION_EVENTOS)
        return resumen
    finally:
        bus.close()


def servicio_eventos(contexto: ContextoServicio) -> None:
    """The event bus worker loop (the same as ``python -m asistente.plataforma.cli.eventos worker``)."""
    from asistente.nucleo.eventos.bus import Bus, consumidores_del_trabajador

    # The tasks' consumer runs here too, so its cursor never holds the purge back (``consumidores_del_trabajador``).
    bus = Bus(contexto.db_path, consumidores=consumidores_del_trabajador())
    nombre = f"{contexto.nombre_proceso}-eventos"
    pausa = max(0.5, float(getattr(settings, "EVENTOS_INTERVALO_SEGUNDOS", 5)))
    ultimo_dia: date | None = None
    try:
        while not contexto.parar.is_set():
            try:
                if bus.dia() != ultimo_dia:
                    ultimo_dia = bus.dia()
                    logger.info("Daily reconciliation: %d timers set", bus.reconciliar(ultimo_dia))
                    logger.info("Daily purge: %s", bus.purgar(dias=DIAS_RETENCION_EVENTOS))
                resumen = bus.ciclo(nombre)
                if resumen["entregas"] or resumen["eventos"]:
                    logger.info("Event cycle: %s", resumen)
                    continue
            except Exception:
                logger.exception("Event worker cycle failed; retrying after the pause")
            contexto.parar.wait(pausa)
    finally:
        bus.close()


def registrar_incorporados(registro: Registro) -> None:
    registro.registrar_servicio(
        Servicio(
            "eventos",
            "Bus de eventos: temporizadores, reparto a los módulos y entregas con reintentos.",
            servicio_eventos,
        )
    )
    for trabajo in TRABAJOS:
        registro.registrar_trabajo(trabajo)


__all__ = ["DIAS_RETENCION_EVENTOS", "TRABAJOS", "pasada_de_eventos", "registrar_incorporados", "servicio_eventos"]

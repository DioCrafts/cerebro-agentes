"""The event bus worker and its maintenance commands.

Run one worker (or several: deliveries are leased, so they never run twice) next to the API::

    uv run python -m hacienda_gpt.cli.eventos worker

The API already dispatches in-process after each change; the worker also picks up what other processes
emit (``boe-novedades``, the mortgage index update) and fires the scheduled wake-ups. Without any worker
the daily ``proactive run-daily`` still produces every notice, once a day (cron fallback)::

    */5 * * * *  cd /srv/hacienda-asesor && uv run python -m hacienda_gpt.cli.eventos worker --una-vez \
                    >> logs/eventos.log 2>&1

See docs/operacion.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
import logging
import os
import signal
import socket
import threading
from typing import Any

import click

from hacienda_gpt import settings
from hacienda_gpt.eventos.bus import Bus

logger = logging.getLogger(__name__)

# Finished deliveries and consumed events are kept this long (metrics window), then erased.
DIAS_RETENCION = 30

_DB_OPTION = click.option(
    "--db-path",
    default=None,
    help="Base de datos SQLite de casos y notificaciones (por defecto, DECISION_STATE_DB_PATH).",
)


def _bus(db_path: str | None) -> Bus:
    return Bus(db_path or settings.DECISION_STATE_DB_PATH)


def _echo(datos: Any) -> None:
    click.echo(json.dumps(datos, ensure_ascii=False, indent=2, default=str))


@click.group()
def cli() -> None:
    """Bus de eventos: el trabajador que despierta a los módulos cuando algo cambia."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@cli.command("worker")
@_DB_OPTION
@click.option("--nombre", default=None, help="Nombre del trabajador (por defecto, equipo y proceso).")
@click.option(
    "--intervalo",
    type=float,
    default=None,
    help="Segundos entre pasadas cuando no hay trabajo (por defecto, EVENTOS_INTERVALO_SEGUNDOS).",
)
@click.option("--una-vez", is_flag=True, help="Una pasada completa y salir (para cron).")
def worker(db_path: str | None, nombre: str | None, intervalo: float | None, una_vez: bool) -> None:
    """Procesa los eventos: temporizadores vencidos, reparto a los módulos y entregas con reintentos.

    Una vez al día recalcula los temporizadores de todos los expedientes (conciliación) y borra lo
    terminado hace más de 30 días.
    """
    bus = _bus(db_path)
    nombre = nombre or f"{socket.gethostname()}-{os.getpid()}"
    if una_vez:
        resumen: dict[str, Any] = {"reconciliados": bus.reconciliar(), **bus.drenar(nombre)}
        resumen["purgado"] = bus.purgar(dias=DIAS_RETENCION)
        _echo(resumen)
        bus.close()
        return
    pausa = intervalo if intervalo is not None else float(getattr(settings, "EVENTOS_INTERVALO_SEGUNDOS", 5))
    parar = threading.Event()

    def _salir(_signum: int, _frame: Any) -> None:
        parar.set()

    signal.signal(signal.SIGTERM, _salir)
    signal.signal(signal.SIGINT, _salir)
    ultimo_dia: date | None = None
    logger.info("Event worker %s started on %s", nombre, bus.db_path)
    while not parar.is_set():
        try:
            if bus.dia() != ultimo_dia:
                ultimo_dia = bus.dia()
                logger.info("Daily reconciliation: %d timers set", bus.reconciliar(ultimo_dia))
                logger.info("Daily purge: %s", bus.purgar(dias=DIAS_RETENCION))
            resumen = bus.ciclo(nombre)
            if resumen["entregas"] or resumen["eventos"]:
                logger.info("Cycle: %s", resumen)
                continue
        except Exception:
            logger.exception("Event worker cycle failed; retrying after the pause")
        parar.wait(pausa)
    bus.close()
    logger.info("Event worker %s stopped", nombre)


@cli.command("estado")
@_DB_OPTION
def estado(db_path: str | None) -> None:
    """Salud del bus (atraso, cola de errores, temporizadores, trabajadores) y métricas por tipo y módulo."""
    bus = _bus(db_path)
    try:
        muertas = [
            {k: fila[k] for k in ("id", "evento_id", "tipo", "modulo", "intentos", "error", "terminado")}
            for fila in bus._leer("SELECT * FROM eventos_entregas WHERE estado = 'muerto' ORDER BY id LIMIT 50")
        ]
        _echo({"salud": bus.salud(), "metricas": bus.metricas(), "cola_de_errores": muertas})
    finally:
        bus.close()


@cli.command("reintentar")
@_DB_OPTION
@click.argument("ids", nargs=-1, type=int)
def reintentar(db_path: str | None, ids: tuple[int, ...]) -> None:
    """Vuelve a poner en cola las entregas de la cola de errores (todas, o las IDS indicadas)."""
    bus = _bus(db_path)
    try:
        _echo({"reencoladas": bus.reintentar_muertas(list(ids) if ids else None)})
    finally:
        bus.close()


@cli.command("reconciliar")
@_DB_OPTION
def reconciliar(db_path: str | None) -> None:
    """Recalcula la próxima revisión de cada módulo y expediente (lo que el trabajador hace a diario)."""
    bus = _bus(db_path)
    try:
        _echo({"temporizadores": bus.reconciliar(), "hoy": bus.dia().isoformat()})
    finally:
        bus.close()


@cli.command("purgar")
@_DB_OPTION
@click.option("--dias", type=int, default=30, show_default=True, help="Antigüedad mínima de lo que se borra.")
@click.option("--muertas", is_flag=True, help="Borrar también la cola de errores.")
def purgar(db_path: str | None, dias: int, muertas: bool) -> None:
    """Borra las entregas terminadas y los eventos ya consumidos más antiguos que --dias."""
    bus = _bus(db_path)
    try:
        _echo({"borrado": bus.purgar(dias=dias, muertas=muertas), "a": datetime.now(UTC).isoformat()})
    finally:
        bus.close()


if __name__ == "__main__":
    cli()

"""``python -m asistente.nucleo.tareas`` — the task worker (cron or a long-running process) and a status view.

    uv run python -m asistente.nucleo.tareas ciclo            # one pass: timers, events, every task in progress
    uv run python -m asistente.nucleo.tareas worker           # the same, every --intervalo seconds
    uv run python -m asistente.nucleo.tareas estado           # counts by state (no personal data)

The worker is the only place where ``consulta`` steps run (they need a language model); the API and the chat
advance everything else at once.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time

import click

from asistente.nucleo.tareas.almacen import tabla_existe
from asistente.nucleo.tareas.motor import Motor
from asistente.plataforma import settings

_DB = click.option("--db-path", default=None, help="Base de datos de casos (por defecto, DECISION_STATE_DB_PATH).")


@click.group()
def cli() -> None:
    """Tareas de larga duración."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@cli.command("ciclo")
@_DB
@click.option("--sin-consultas", is_flag=True, help="No ejecutar los pasos que consultan a un especialista.")
def ciclo_cmd(db_path: str | None, sin_consultas: bool) -> None:
    """Una pasada: temporizadores, eventos y todas las tareas en marcha."""
    motor = Motor(db_path or settings.DECISION_STATE_DB_PATH, ejecutar_consultas=not sin_consultas)
    try:
        click.echo(json.dumps(motor.ciclo(), ensure_ascii=False))
    finally:
        motor.close()


@cli.command("worker")
@_DB
@click.option("--intervalo", type=float, default=60.0, help="Segundos entre pasadas.")
def worker_cmd(db_path: str | None, intervalo: float) -> None:
    """Pasadas sin fin (systemd, supervisor…)."""
    motor = Motor(db_path or settings.DECISION_STATE_DB_PATH)
    try:
        while True:
            logging.info("tareas: %s", motor.ciclo())
            time.sleep(max(1.0, intervalo))
    finally:
        motor.close()


@cli.command("estado")
@_DB
def estado_cmd(db_path: str | None) -> None:
    """Tareas por estado y señales sin procesar (solo recuentos)."""
    ruta = db_path or settings.DECISION_STATE_DB_PATH
    if not tabla_existe(ruta):
        click.echo(json.dumps({"instalado": False}))
        return
    with sqlite3.connect(ruta) as conn:
        por_estado = dict(conn.execute("SELECT estado, COUNT(*) FROM tareas GROUP BY estado").fetchall())
        senales = conn.execute("SELECT COUNT(*) FROM tareas_senales WHERE procesada = 0").fetchone()[0]
    click.echo(json.dumps({"instalado": True, "tareas": por_estado, "senales_pendientes": senales}))


if __name__ == "__main__":  # pragma: no cover
    cli()

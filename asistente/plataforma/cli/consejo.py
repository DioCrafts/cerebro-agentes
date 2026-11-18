"""The household council from the command line (``asistente/nucleo/consejo``, docs/consejo/arquitectura.md).

    # What the worker runs every 5 minutes (job ``consejo``): expire unconfirmed convocations, take over sessions of
    # a crashed run, run the confirmed ones (one per case at a time) and apply the retention.
    python -m asistente.plataforma.cli.consejo ejecutar [--limite 5]

    # Convene it for a case (prints the estimate; nothing runs until it is confirmed):
    python -m asistente.plataforma.cli.consejo convocar --case <id> --pregunta "¿Amortizo o invierto?"
    # … and confirm it at once (the operator, on the user's behalf), then run it now:
    python -m asistente.plataforma.cli.consejo convocar --case <id> --pregunta "…" --confirmar
    python -m asistente.plataforma.cli.consejo ejecutar

    # The sessions of a case, or one session (state, phase, estimate, cost, recommendation):
    python -m asistente.plataforma.cli.consejo estado --case <id> [--sesion cons_…]

Output is JSON (ids, states, figures in USD). ``ejecutar`` never fails because a session did: a failed session is an
outcome the user is told about (notification), not an operator error.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import click

from asistente.plataforma import settings

_DB_OPTION = click.option(
    "--db-path",
    default=None,
    help="Base de datos SQLite de casos (por defecto, DECISION_STATE_DB_PATH).",
)


def _imprimir(datos: Any) -> None:
    click.echo(json.dumps(datos, ensure_ascii=False, default=str))


def _servicio(db_path: str | None) -> Any:
    from asistente.nucleo.consejo.servicio import ServicioConsejo

    # The CLI runs sessions in its own process, never in a background thread it would not wait for.
    return ServicioConsejo(db_path or str(settings.DECISION_STATE_DB_PATH), en_segundo_plano=False)


@click.group()
def cli() -> None:
    """Consejo del hogar: decisiones difíciles estudiadas por varias IA, de forma independiente y anónima."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@cli.command("ejecutar")
@_DB_OPTION
@click.option("--limite", type=int, default=5, show_default=True, help="Sesiones como mucho en esta pasada.")
def ejecutar(db_path: str | None, limite: int) -> None:
    """Lo que corre el worker: caduca, recupera y ejecuta las sesiones confirmadas."""
    from asistente.nucleo.consejo.servicio import activo

    if not activo():
        _imprimir({"activo": False})
        return
    _imprimir(_servicio(db_path).ejecutar_pendientes(limite=max(1, limite)))


@cli.command("convocar")
@_DB_OPTION
@click.option("--case", "case_id", required=True, help="Caso.")
@click.option("--pregunta", required=True, help="La decisión, con las palabras del usuario.")
@click.option("--preferencia", default=None, help="La inclinación del usuario, si la ha dicho (se sella).")
@click.option("--confirmar", is_flag=True, help="Confirmarla ya (en nombre del usuario).")
def convocar(db_path: str | None, case_id: str, pregunta: str, preferencia: str | None, confirmar: bool) -> None:
    """Convoca el consejo para una decisión (con su coste estimado)."""
    from asistente.nucleo.consejo.servicio import ConsejoError
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso

    servicio = _servicio(db_path)
    case = SQLiteCaseStateStore(servicio.db_path).get_case(case_id)
    if case is None:
        raise click.ClickException(f"No existe el caso {case_id}.")
    try:
        sesion = servicio.convocar(
            case=case,
            pregunta=pregunta,
            origen="cli",
            preferencia=preferencia,
            modulos=modulos_activos_para_caso(case_id),
        )
        if confirmar:
            sesion = servicio.confirmar(sesion.id, case_id=case_id).sesion
    except ConsejoError as exc:
        raise click.ClickException(exc.mensaje) from exc
    registro = servicio.obtener(sesion.id, case_id=case_id)
    _imprimir(
        {
            "id": sesion.id,
            "estado": sesion.estado,
            "estimacion_usd": str(sesion.estimacion_usd),
            "tope_usd": str(sesion.tope_usd),
            "minutos": registro.extra.minutos_estimados,
            "miembros": registro.extra.miembros,
        }
    )


@cli.command("estado")
@_DB_OPTION
@click.option("--case", "case_id", required=True, help="Caso.")
@click.option("--sesion", "sesion_id", default=None, help="Una sesión (por defecto, todas las del caso).")
def estado(db_path: str | None, case_id: str, sesion_id: str | None) -> None:
    """Las sesiones de un caso: estado, fase, estimación, coste y recomendación."""
    from asistente.nucleo.consejo.servicio import ConsejoError
    from asistente.plataforma.api.routers.consejo import resumen

    servicio = _servicio(db_path)
    if sesion_id is not None:
        try:
            registros = [servicio.obtener(sesion_id, case_id=case_id)]
        except ConsejoError as exc:
            raise click.ClickException(exc.mensaje) from exc
    else:
        registros = servicio.listar(case_id)
    _imprimir([resumen(r).model_dump(mode="json") for r in registros])


if __name__ == "__main__":  # pragma: no cover
    cli()

"""The brain's proactive pass from the command line (``asistente/nucleo/repaso``, docs/cerebro/repaso.md).

    # What the worker runs every 30 minutes (job ``repaso``): collect the batches in flight and, when the period's
    # pass is due (REPASO_DIA / REPASO_HORA), start it for every open case. Does nothing without REPASO_ACTIVO.
    python -m asistente.plataforma.cli.repaso trabajo

    # Run it now for one case, or for all, printing what it found and what it would write (nothing is written):
    python -m asistente.plataforma.cli.repaso ahora --case <id> --simular
    python -m asistente.plataforma.cli.repaso ahora --todos --simular --con-modelo

    # Write it now (synchronous; ``--lotes`` sends one batch and leaves it to ``trabajo`` to collect):
    python -m asistente.plataforma.cli.repaso ahora --case <id>

    # The passes of a case (period, state, origin of the text, batch):
    python -m asistente.plataforma.cli.repaso estado --case <id>

A dry run never calls the model unless ``--con-modelo`` (it costs money); a real run uses it when one is
configured. Output is JSON (ids, counts, states) except the dry run's rendering, which prints the digest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

import click

from asistente.plataforma import settings

_DB_OPTION = click.option(
    "--db-path",
    default=None,
    help="Base de datos SQLite de casos y avisos (por defecto, DECISION_STATE_DB_PATH).",
)


def _dia(_ctx: click.Context, _param: click.Parameter, valor: str | None) -> date | None:
    if valor is None:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise click.BadParameter("usa el formato AAAA-MM-DD") from exc


def _momento(_ctx: click.Context, _param: click.Parameter, valor: str | None) -> datetime | None:
    if valor is None:
        return None
    try:
        momento = datetime.fromisoformat(valor)
    except ValueError as exc:
        raise click.BadParameter("usa el formato AAAA-MM-DDTHH:MM (hora peninsular si no lleva zona)") from exc
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=ZoneInfo("Europe/Madrid"))
    return momento


def _imprimir(datos: Any) -> None:
    click.echo(json.dumps(datos, ensure_ascii=False, default=str))


@click.group()
def cli() -> None:
    """Repaso proactivo del asistente: el resumen priorizado que el usuario recibe sin preguntar."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@cli.command("trabajo")
@_DB_OPTION
@click.option("--ahora", "ahora_", callback=_momento, default=None, help="Momento de referencia (pruebas).")
@click.option("--forzar", is_flag=True, help="Hacer la pasada aunque todavía no toque (día y hora).")
def trabajo(db_path: str | None, ahora_: datetime | None, forzar: bool) -> None:
    """Lo que corre el worker: recoge los lotes en curso y, si toca, hace la pasada de cada caso abierto."""
    from asistente.nucleo.repaso import servicio

    if not servicio.activo():
        _imprimir({"activo": False})
        return
    resumen = servicio.repasar_todos(
        db_path or str(settings.DECISION_STATE_DB_PATH), ahora=ahora_ or datetime.now(UTC), forzar=forzar
    )
    _imprimir(resumen)
    if resumen.get("errores"):
        raise SystemExit(1)


def _texto_simulado(resultado: Any) -> str:
    lineas = [f"== {resultado.case_id} · {resultado.periodo} · {resultado.estado}"]
    for punto in resultado.puntos:
        lineas.append(
            f"  {punto.rango}. [{punto.puntuacion}] {punto.titulo}"
            + (" (urgente)" if punto.urgente else "")
            + f" — {'; '.join(punto.por_que[:3])}"
        )
        for propuesta in punto.propuestas:
            lineas.append(f"       propuesta {propuesta.id}: {propuesta.etiqueta}")
    if resultado.restantes:
        lineas.append(f"  (+{resultado.restantes} más)")
    for omitido in resultado.omitidos:
        lineas.append(f"  omitido {omitido.senal_id} ({omitido.alcance}) por la decisión {omitido.decision_id}")
    if resultado.titulo:
        lineas += ["", f"Título: {resultado.titulo}", f"Texto ({resultado.origen}):", resultado.cuerpo or ""]
    for violacion in resultado.violaciones:
        lineas.append(f"  puerta: {violacion.tipo} — {violacion.detalle}")
    return "\n".join(lineas)


@cli.command("ahora")
@_DB_OPTION
@click.option("--case", "case_ids", multiple=True, help="Caso (repetible).")
@click.option("--todos", is_flag=True, help="Todos los casos abiertos.")
@click.option("--hoy", callback=_dia, default=None, help="Fecha de referencia AAAA-MM-DD (por defecto, hoy).")
@click.option("--simular", is_flag=True, help="No escribe nada: imprime los puntos y el resumen que se escribiría.")
@click.option("--con-modelo", is_flag=True, help="En una simulación, redactar también con el modelo (cuesta dinero).")
@click.option("--lotes", is_flag=True, help="Enviar un lote del proveedor (lo recoge «trabajo» más tarde).")
@click.option(
    "--frecuencia", type=click.Choice(["semanal", "diaria"]), default=None, help="Por defecto, REPASO_FRECUENCIA."
)
def ahora(
    db_path: str | None,
    case_ids: tuple[str, ...],
    todos: bool,
    hoy: date | None,
    simular: bool,
    con_modelo: bool,
    lotes: bool,
    frecuencia: str | None,
) -> None:
    """Hace la pasada ya (sin esperar a REPASO_DIA / REPASO_HORA), para uno o varios casos o para todos."""
    from asistente.nucleo.decision.schemas import CaseStatus
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.repaso import servicio

    if not case_ids and not todos:
        raise click.UsageError("Indica --case <id> (repetible) o --todos.")
    ruta = db_path or str(settings.DECISION_STATE_DB_PATH)
    dia = hoy or datetime.now(UTC).astimezone(ZoneInfo("Europe/Madrid")).date()
    casos = SQLiteCaseStateStore(ruta)
    if lotes and not simular:
        resumen = servicio.repasar_todos(
            ruta,
            hoy=dia,
            forzar=True,
            por_lotes=True,
            casos_ids=None if todos else list(case_ids),
            frecuencia_=frecuencia,
        )
        _imprimir(resumen)
        return
    elegidos = (
        [c for c in casos.list_all_cases() if c.status != CaseStatus.CLOSED]
        if todos
        else [c for c in (casos.get_case(i) for i in case_ids) if c is not None]
    )
    faltan = [] if todos else [i for i in case_ids if casos.get_case(i) is None]
    proveedor: Any = None
    if not simular or con_modelo:
        proveedor = servicio.proveedor_configurado()
    salida: list[dict[str, Any]] = []
    errores = 0
    for case in elegidos:
        try:
            resultado = servicio.repasar(
                case, hoy=dia, db_path=ruta, proveedor=proveedor, simular=simular, frecuencia_=frecuencia
            )
        except Exception as exc:
            logging.getLogger(__name__).exception("The pass failed for case %s", case.case_id)
            salida.append({"case_id": case.case_id, "estado": "error", "error": type(exc).__name__})
            errores += 1
            continue
        if simular:
            click.echo(_texto_simulado(resultado))
        salida.append(resultado.resumen())
    _imprimir({"hoy": dia.isoformat(), "modelo": proveedor is not None, "casos": salida, "no_encontrados": faltan})
    if errores or faltan:
        raise SystemExit(1)


@cli.command("estado")
@_DB_OPTION
@click.option("--case", "case_id", required=True, help="Caso.")
def estado(db_path: str | None, case_id: str) -> None:
    """Las pasadas de un caso: periodo, estado, origen del texto y lote (sin el texto)."""
    from asistente.nucleo.repaso.almacen import RepasoStore

    pasadas = RepasoStore(db_path or str(settings.DECISION_STATE_DB_PATH)).de_caso(case_id)
    _imprimir(
        [
            {
                "periodo": p.periodo,
                "estado": p.estado,
                "origen": p.origen,
                "puntos": len(p.prioridad.puntos),
                "notification_id": p.notification_id,
                "lote_id": p.lote_id,
                "intentos": p.intentos,
                "violaciones": len(p.violaciones),
                "actualizada": p.actualizada.isoformat(),
            }
            for p in pasadas
        ]
    )


if __name__ == "__main__":
    cli()

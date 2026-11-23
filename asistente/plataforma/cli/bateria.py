"""Regression battery of the researcher and the brain (docs/cerebro/bateria.md).

    python -m asistente.plataforma.cli.bateria run [--sistema investigador|cerebro] [--caso ID]...
        [--presupuesto-total 8] [--baseline eval_data/bateria/linea_base.json] [--fijar-linea-base] [--yes]
    python -m asistente.plataforma.cli.bateria comparar ACTUAL.json [--baseline BASE.json]
    python -m asistente.plataforma.cli.bateria validar

Exit code 1 when a critical invariant fails or regresses against the baseline (also on a quality regression with
``--estricto``).
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path

import click

from asistente.nucleo.evaluacion import bateria as b


@click.group()
def cli() -> None:
    """Batería fija de evaluación del investigador y el cerebro."""


@cli.command()
def validar() -> None:
    """Valida los casos (esquema, patrones, ids)."""
    bat = b.cargar_bateria()
    click.echo(f"Batería v{bat.version}: {len(bat.casos)} casos, huella {bat.huella()}.")


def _informar_comparacion(actual: b.InformeBateria, baseline: Path | None, estricto: bool) -> int:
    codigo = 0 if actual.resumen.get("criticos_ok") == actual.resumen.get("ejecutados") else 1
    if baseline is not None and baseline.exists():
        comparacion = b.comparar(actual, b.cargar_informe(baseline))
        click.echo("")
        click.echo(b.texto_comparacion(comparacion))
        if comparacion.bloquea or (estricto and comparacion.regresion_calidad):
            codigo = 1
    return codigo


@cli.command()
@click.option("--sistema", "sistemas", multiple=True, type=click.Choice(["investigador", "cerebro"]))
@click.option("--caso", "ids", multiple=True)
@click.option(
    "--presupuesto-total",
    type=float,
    default=10.0,
    show_default=True,
    help="Corta (omite casos) al superar este gasto acumulado.",
)
@click.option("--sin-juez", is_flag=True, help="No evalúa las rúbricas (cuentan como no cumplidas).")
@click.option(
    "--baseline", type=click.Path(dir_okay=False, path_type=Path), default=b.LINEA_BASE_PATH, show_default=True
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--fijar-linea-base", is_flag=True, help="Guarda también el informe como línea base versionada.")
@click.option("--estricto", is_flag=True, help="Una regresión de calidad también termina con error.")
@click.option(
    "--parcial",
    type=click.Path(dir_okay=False, path_type=Path),
    default=b.INFORMES_DIR / "en_curso.jsonl",
    show_default=True,
    help="Resultados por caso a medida que terminan; una ejecución interrumpida se reanuda desde aquí.",
)
@click.option("--yes", is_flag=True, help="No pide confirmación antes de gastar.")
def run(
    sistemas: tuple[str, ...],
    ids: tuple[str, ...],
    presupuesto_total: float,
    sin_juez: bool,
    baseline: Path,
    out_path: Path | None,
    fijar_linea_base: bool,
    estricto: bool,
    parcial: Path,
    yes: bool,
) -> None:
    """Ejecuta la batería con modelo real y la compara con la línea base."""
    from asistente.nucleo.evaluacion.bateria_ejecutores import ejecutor_cerebro, ejecutor_investigador, juez_por_defecto

    bat = b.cargar_bateria()
    seleccion = [c for c in bat.casos if (not ids or c.id in ids) and (not sistemas or c.sistema in sistemas)]
    if not yes:
        click.confirm(
            f"{len(seleccion)} casos con modelo real (hasta {bat.presupuesto_por_caso_eur} € por caso del investigador, "
            f"{presupuesto_total} € en total). ¿Seguir?",
            abort=True,
        )
    usados = {c.sistema for c in seleccion}
    ejecutores: dict[str, b.Ejecutor] = {}
    if "investigador" in usados:
        ejecutores["investigador"] = ejecutor_investigador()
    if "cerebro" in usados:
        ejecutores["cerebro"] = ejecutor_cerebro()
    modelos = {
        "investigador": os.environ.get("INVESTIGADOR_MODELO", "anthropic/claude-sonnet-5-5"),
    }
    if "cerebro" in usados:
        from asistente.nucleo.agent.providers import create_provider

        proveedor = create_provider()
        modelos["cerebro"] = f"{proveedor.name}/{proveedor.model}"

    previos = b.cargar_parcial(parcial)
    if previos:
        click.echo(f"Reanudando: {len(previos)} casos ya hechos en {parcial}.", err=True)
    parcial.parent.mkdir(parents=True, exist_ok=True)

    def progreso(r: b.Resultado) -> None:
        with parcial.open("a", encoding="utf-8") as f:
            f.write(r.model_dump_json() + "\n")
        marca = "ok " if r.critico_ok else "CRÍTICO"
        click.echo(
            f"[{marca}] {r.id}: calidad {r.calidad:.2f}, {r.coste:.4f} {r.moneda}, {r.latencia_s:.0f} s", err=True
        )

    informe = b.ejecutar(
        bat,
        ejecutores,
        ids=ids,
        sistemas=sistemas,
        juez=None if sin_juez else juez_por_defecto(),
        presupuesto_total_eur=presupuesto_total,
        modelos=modelos,
        al_terminar_caso=progreso,
        previos=previos,
    )
    destino = out_path or b.INFORMES_DIR / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{informe.commit}.json"
    b.guardar(informe, destino)
    if not ids and not sistemas:  # a partial selection keeps accumulating for the full run
        parcial.unlink(missing_ok=True)
    click.echo(b.tabla(informe))
    click.echo(f"\nInforme: {destino}")
    codigo = _informar_comparacion(informe, None if fijar_linea_base else baseline, estricto)
    if fijar_linea_base and (ids or sistemas):
        click.echo("La línea base solo se fija con la batería completa.", err=True)
    elif fijar_linea_base:
        b.guardar(informe, b.LINEA_BASE_PATH)
        click.echo(f"Línea base fijada: {b.LINEA_BASE_PATH}")
    raise SystemExit(codigo)


@cli.command()
@click.argument("actual", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--baseline", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=b.LINEA_BASE_PATH)
@click.option("--estricto", is_flag=True)
@click.option("--json", "como_json", is_flag=True)
def comparar(actual: Path, baseline: Path, estricto: bool, como_json: bool) -> None:
    """Compara un informe guardado con la línea base."""
    informe = b.cargar_informe(actual)
    if como_json:
        click.echo(json.dumps(b.comparar(informe, b.cargar_informe(baseline)).a_dict(), ensure_ascii=False, indent=2))
        return
    click.echo(b.tabla(informe))
    raise SystemExit(_informar_comparacion(informe, baseline, estricto))


if __name__ == "__main__":
    cli()

"""Platform commands: schema migrations, the worker process, health, backups and key rotation.

    uv run python -m asistente.plataforma.cli.plataforma migrar [--estado] [--dry-run]
    uv run python -m asistente.plataforma.cli.plataforma worker [--una-vez]
    uv run python -m asistente.plataforma.cli.plataforma procesos
    uv run python -m asistente.plataforma.cli.plataforma salud [--exigir-worker]
    uv run python -m asistente.plataforma.cli.plataforma copia --destino /var/backups/hacienda

See docs/despliegue.md.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from typing import Any

import click

from asistente.plataforma import settings
from asistente.plataforma.migraciones import BASE_CASOS, BASE_DOCTRINA, estado, migrar

logger = logging.getLogger(__name__)

VARIABLE_CLAVE_COPIAS = "PLATAFORMA_CLAVE_COPIAS"


def _ruta(base: str, db_path: str | None) -> str:
    if db_path:
        return db_path
    return str(settings.DOCTRINE_GRAPH_PATH if base == BASE_DOCTRINA else settings.DECISION_STATE_DB_PATH)


def _echo(datos: Any) -> None:
    click.echo(json.dumps(datos, ensure_ascii=False, indent=2, default=str))


_DB = click.option(
    "--db-path",
    default=None,
    help="Fichero SQLite (por defecto, DECISION_STATE_DB_PATH; con --base doctrina, el grafo).",
)
_BASE = click.option(
    "--base",
    type=click.Choice([BASE_CASOS, BASE_DOCTRINA]),
    default=BASE_CASOS,
    show_default=True,
    help="Qué base de datos: la de casos o el grafo de doctrina.",
)


@click.group()
def cli() -> None:
    """Plataforma: migraciones del esquema, el proceso worker, salud y copias de seguridad."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@cli.command("migrar")
@_DB
@_BASE
@click.option("--estado", "ver_estado", is_flag=True, help="Solo mostrar la versión de cada propietario.")
@click.option("--dry-run", is_flag=True, help="Mostrar lo que se aplicaría (con el SQL) sin escribir nada.")
@click.option("--propietario", "propietarios", multiple=True, help="Solo estos propietarios (y sus dependencias).")
@click.option("--json", "como_json", is_flag=True, help="Salida en JSON.")
def migrar_cmd(
    db_path: str | None, base: str, ver_estado: bool, dry_run: bool, propietarios: tuple[str, ...], como_json: bool
) -> None:
    """Aplica las migraciones pendientes (en una transacción; idempotente)."""
    ruta = _ruta(base, db_path)
    if ver_estado:
        filas = estado(ruta, base=base)
        if como_json:
            _echo([f.model_dump() for f in filas])
            return
        click.echo(f"Base «{base}»: {ruta}")
        for f in filas:
            marca = "al día" if not f.pendientes else f"pendientes: {', '.join(map(str, f.pendientes))}"
            aviso = f"  ¡migraciones cambiadas tras aplicarse: {f.sumas_distintas}!" if f.sumas_distintas else ""
            click.echo(f"  {f.propietario:<18} v{f.version_actual}/{f.version_ultima}  {marca}{aviso}")
        return
    informe = migrar(ruta, base=base, nombres=list(propietarios) or None, dry_run=dry_run)
    if como_json:
        _echo(informe.model_dump())
        return
    verbo = "Se aplicarían" if dry_run else "Aplicadas"
    click.echo(f"{verbo} {len(informe.aplicadas)} migración(es) en {ruta}.")
    for paso in informe.aplicadas:
        click.echo(f"  {paso.propietario} v{paso.version} {paso.nombre}")
        if dry_run:
            for sentencia in paso.sentencias:
                click.echo("      " + sentencia.replace("\n", "\n      ") + ";")
            if paso.funcion:
                click.echo(f"      -- y la función {paso.funcion}")
    for paso in informe.aplazadas:
        click.echo(f"  (aplazada) {paso.propietario} v{paso.version} {paso.nombre}: espera {paso.espera_tablas}")


@cli.command("worker")
@_DB
@click.option("--nombre", default=None, help="Nombre del worker (por defecto, worker-<equipo>-<pid>).")
@click.option("--una-vez", is_flag=True, help="Una pasada (bus de eventos y trabajos debidos) y salir.")
@click.option("--sin-servicios", is_flag=True, help="Sin servicios continuos (ni el bus de eventos).")
@click.option("--sin-trabajos", is_flag=True, help="Sin tareas programadas.")
def worker_cmd(db_path: str | None, nombre: str | None, una_vez: bool, sin_servicios: bool, sin_trabajos: bool) -> None:
    """El proceso worker: bus de eventos, tareas programadas y los servicios que registren otros paquetes."""
    from asistente.plataforma.procesos import Worker

    worker = Worker(_ruta(BASE_CASOS, db_path), nombre=nombre, servicios=not sin_servicios, trabajos=not sin_trabajos)
    informe = worker.preparar()
    if informe["aplicadas"]:
        logger.info("Migrations applied at start-up: %d", len(informe["aplicadas"]))
    if una_vez:
        _echo(worker.una_vez())
        return

    def _salir(_signum: int, _frame: Any) -> None:
        worker.parar.set()

    signal.signal(signal.SIGTERM, _salir)
    signal.signal(signal.SIGINT, _salir)
    worker.ejecutar()


@cli.command("procesos")
def procesos_cmd() -> None:
    """El modelo de procesos: qué hace la API, qué hace el worker, y cuándo corre cada tarea."""
    from asistente.plataforma.procesos import describir

    _echo(describir())


@cli.command("salud")
@_DB
@click.option("--exigir-worker", is_flag=True, help="Fallar (código 1) si ningún worker ha latido hace poco.")
def salud_cmd(db_path: str | None, exigir_worker: bool) -> None:
    """Migraciones pendientes, workers vivos y última ejecución de cada tarea. Código 1 si algo falla."""
    from asistente.plataforma.procesos import salud

    datos = salud(_ruta(BASE_CASOS, db_path))
    _echo(datos)
    if datos["migraciones_pendientes"] or (exigir_worker and not datos["worker_vivo"]):
        sys.exit(1)


def _clave(obligatoria: bool) -> str | None:
    clave = os.environ.get(VARIABLE_CLAVE_COPIAS, "").strip()
    if obligatoria and not clave:
        raise click.ClickException(
            f"Falta {VARIABLE_CLAVE_COPIAS} (genera una con «plataforma nueva-clave»), o usa --sin-cifrar "
            "si el destino ya es un disco cifrado."
        )
    return clave or None


@cli.command("copia")
@_DB
@_BASE
@click.option("--destino", required=True, type=click.Path(file_okay=False), help="Directorio de las copias.")
@click.option("--sin-cifrar", is_flag=True, help="Copia sin cifrar (solo hacia un disco ya cifrado).")
def copia_cmd(db_path: str | None, base: str, destino: str, sin_cifrar: bool) -> None:
    """Copia en caliente y consistente (API de copia de SQLite), comprobada y cifrada con PLATAFORMA_CLAVE_COPIAS."""
    from asistente.plataforma.operaciones import CopiaError, copiar

    try:
        ruta = copiar(_ruta(base, db_path), destino, clave=None if sin_cifrar else _clave(True))
    except CopiaError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(ruta))


@cli.command("descifrar-copia")
@click.argument("fichero", type=click.Path(exists=True, dir_okay=False))
@click.argument("destino", type=click.Path(dir_okay=False))
def descifrar_cmd(fichero: str, destino: str) -> None:
    """Descifra una copia (con cualquiera de las claves de PLATAFORMA_CLAVE_COPIAS) en DESTINO."""
    from asistente.plataforma.operaciones import CopiaError, descifrar

    clave = _clave(True)
    assert clave is not None
    try:
        click.echo(str(descifrar(fichero, destino, clave=clave)))
    except CopiaError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("recifrar-copias")
@click.argument("ficheros", nargs=-1, type=click.Path(exists=True, dir_okay=False))
def recifrar_cmd(ficheros: tuple[str, ...]) -> None:
    """Rotación: vuelve a cifrar las copias con la PRIMERA clave de PLATAFORMA_CLAVE_COPIAS."""
    from asistente.plataforma.operaciones import CopiaError, recifrar

    clave = _clave(True)
    assert clave is not None
    for fichero in ficheros:
        try:
            recifrar(fichero, clave=clave)
        except CopiaError as exc:
            raise click.ClickException(f"{fichero}: {exc}") from exc
    click.echo(f"{len(ficheros)} copia(s) recifrada(s) con la primera clave.")


@cli.command("nueva-clave")
def nueva_clave_cmd() -> None:
    """Imprime una clave Fernet nueva (para PLATAFORMA_CLAVE_COPIAS o CORREO_CLAVE_CIFRADO)."""
    from asistente.plataforma.operaciones import nueva_clave

    click.echo(nueva_clave())


@cli.command("revocar-sesiones")
@_DB
@click.option("--yes", "confirmado", is_flag=True, help="No preguntar.")
def revocar_cmd(db_path: str | None, confirmado: bool) -> None:
    """Cierra todas las sesiones abiertas: todos los usuarios tendrán que volver a entrar."""
    from asistente.plataforma.operaciones import revocar_sesiones

    if not confirmado:
        click.confirm("¿Cerrar TODAS las sesiones abiertas?", abort=True)
    click.echo(f"{revocar_sesiones(_ruta(BASE_CASOS, db_path))} sesión(es) revocada(s).")


if __name__ == "__main__":
    cli()

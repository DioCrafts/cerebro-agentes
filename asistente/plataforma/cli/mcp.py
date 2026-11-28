"""MCP from the command line: approve the tools of an external server, and serve the app's own tools.

    python -m asistente.plataforma.cli.mcp listar                  # declared servers and what is approved
    python -m asistente.plataforma.cli.mcp aprobar notas           # connect, show every tool, approve them
    python -m asistente.plataforma.cli.mcp aprobar notas --solo buscar --solo leer_nota --si
    python -m asistente.plataforma.cli.mcp retirar notas           # withdraw the approvals of a server
    python -m asistente.plataforma.cli.mcp servir --caso CASO      # this app as an MCP server, over stdio

``aprobar`` is the moment a person reads what a third party's server says about its tools: the descriptions are
printed as the model would see them (cleaned) and each tool's effect as the declaration sets it (``lectura`` only
when listed there; anything else waits for the user's confirmation). The API must be restarted afterwards.

``servir`` writes the protocol to stdout, so every log line goes to stderr. It serves one case; whoever can run it
already has the database. See docs/capacidades/conectores.md.
"""

from __future__ import annotations

import logging
import sys

import click

from asistente.capacidades.conectores import aprobaciones, conexiones
from asistente.capacidades.conectores.config import SecretoAusenteError, ServidorMCP, leer
from asistente.capacidades.conectores.definicion import HerramientaRemota, clave_de, limpiar_descripcion
from asistente.plataforma import settings


def _declaracion() -> dict[str, ServidorMCP]:
    lectura = leer()
    if lectura.error is not None:
        raise click.ClickException(lectura.error)
    if not lectura.configuracion.servidores:
        raise click.ClickException("No hay servidores declarados: define MCP_SERVIDORES con la ruta del fichero JSON.")
    return dict(lectura.configuracion.servidores)


def _servidor(clave: str) -> ServidorMCP:
    servidores = _declaracion()
    if clave not in servidores:
        raise click.ClickException(f"«{clave}» no está declarado. Declarados: {', '.join(sorted(servidores))}.")
    return servidores[clave]


def _efecto(servidor: ServidorMCP, nombre: str) -> str:
    return "lectura" if nombre in servidor.lectura else "actúa (pide confirmación)"


@click.group()
def main() -> None:
    """Conectores MCP y servidor MCP del asistente."""


@main.command()
def listar() -> None:
    """Servidores declarados y herramientas aprobadas de cada uno (no se conecta a ninguno)."""
    servidores = _declaracion()
    aprobadas = aprobaciones.leer()
    for clave, servidor in sorted(servidores.items()):
        estado = "" if servidor.activo else " [desactivado]"
        destino = servidor.url if servidor.transporte == "http" else servidor.comando
        click.echo(f"{clave}{estado} — {servidor.nombre} ({servidor.transporte}: {destino})")
        herramientas = aprobadas.de(clave)
        if not herramientas:
            click.echo("  sin herramientas aprobadas: ejecuta «aprobar " + clave + "»")
            continue
        usadas: set[str] = set()
        for nombre in sorted(herramientas):
            operacion = clave_de(nombre, usadas)
            usadas.add(operacion)
            if not servidor.ofrece(nombre):
                click.echo(f"  - {nombre}: aprobada, pero la declaración no la ofrece")
                continue
            click.echo(f"  - {nombre} → conectores_{clave}_{operacion} [{_efecto(servidor, nombre)}]")


@main.command()
@click.argument("clave")
@click.option("--solo", multiple=True, help="Aprobar solo estas herramientas (por su nombre en el servidor).")
@click.option("--si", is_flag=True, help="No preguntar antes de escribir las aprobaciones.")
def aprobar(clave: str, solo: tuple[str, ...], si: bool) -> None:
    """Conecta con el servidor CLAVE, enseña sus herramientas y aprueba sus definiciones."""
    servidor = _servidor(clave)
    gestor = conexiones.GestorConexiones()
    try:
        vivas: list[HerramientaRemota] = gestor.listar(clave, servidor)
    except SecretoAusenteError as exc:
        raise click.ClickException(str(exc)) from exc
    except conexiones.ConexionError as exc:
        raise click.ClickException(f"No se ha podido conectar con «{clave}»: {exc}") from exc
    finally:
        gestor.cerrar()
    por_nombre = {h.nombre: h for h in vivas}
    desconocidas = sorted(set(solo) - set(por_nombre))
    if desconocidas:
        raise click.ClickException(f"El servidor no ofrece: {', '.join(desconocidas)}.")
    elegidas = [h for h in vivas if (not solo or h.nombre in solo) and servidor.ofrece(h.nombre)]
    if not elegidas:
        raise click.ClickException("No hay ninguna herramienta que aprobar con esa selección.")
    anteriores = aprobaciones.leer().de(clave)
    click.echo(f"{servidor.nombre}: {len(elegidas)} herramienta(s). Lee cada descripción: la escribe un tercero.\n")
    for herramienta in elegidas:
        anterior = anteriores.get(herramienta.nombre)
        if anterior is None:
            marca = "nueva"
        elif anterior.huella == herramienta.huella:
            marca = "sin cambios"
        else:
            marca = "HA CAMBIADO desde la última aprobación"
        click.echo(f"- {herramienta.nombre} [{_efecto(servidor, herramienta.nombre)}] ({marca})")
        click.echo(f"    {limpiar_descripcion(herramienta.descripcion) or '(sin descripción)'}")
        campos = sorted((herramienta.entrada.get("properties") or {}).keys())
        click.echo(f"    datos que recibe: {', '.join(campos) or 'ninguno'}")
    no_declaradas = sorted(set(servidor.lectura) - set(por_nombre))
    if no_declaradas:
        click.echo(f"\nAviso: «lectura» nombra herramientas que el servidor no ofrece: {', '.join(no_declaradas)}.")
    if not si and not click.confirm("\n¿Aprobar estas definiciones?", default=False):
        raise click.Abort()
    aprobaciones.aprobar(clave, elegidas)
    click.echo(f"Aprobadas en {aprobaciones.ruta()}. Reinicia la API para que las ofrezca.")


@main.command()
@click.argument("clave")
def retirar(clave: str) -> None:
    """Retira todas las aprobaciones del servidor CLAVE."""
    if aprobaciones.retirar(clave):
        click.echo(f"Aprobaciones de «{clave}» retiradas. Reinicia la API.")
    else:
        click.echo(f"«{clave}» no tenía aprobaciones.")


@main.command()
@click.option("--caso", required=True, help="Expediente que se sirve.")
@click.option("--db", default=None, help="Base de datos de los expedientes (por defecto, DECISION_STATE_DB_PATH).")
def servir(caso: str, db: str | None) -> None:
    """Sirve por stdio las herramientas del asistente para un expediente."""
    import anyio

    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.modulos.acciones import AccionStore
    from asistente.nucleo.proactive.jobs import hoy_en_espana
    from asistente.plataforma.mcp.servidor import ContextoServidor, servir_stdio

    # stdout carries the protocol: nothing else may be written to it.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ruta = db or settings.DECISION_STATE_DB_PATH
    cases = SQLiteCaseStateStore(ruta)
    if cases.get_case(caso) is None:
        raise click.ClickException(f"El expediente «{caso}» no existe en {ruta}.")
    auth = None
    if settings.AUTH_MODE == "required":
        from asistente.plataforma.auth.deps import get_auth_store

        auth = get_auth_store()
    contexto = ContextoServidor(case_id=caso, cases=cases, acciones=AccionStore(ruta), auth=auth, hoy=hoy_en_espana)
    anyio.run(servir_stdio, contexto)


if __name__ == "__main__":
    main()

"""The messaging gateway from the command line (``asistente/plataforma/mensajeria``, docs/mensajeria/arquitectura.md).

    # Is it on, which channels, are they connected (as the worker last published), how many chats are paired:
    python -m asistente.plataforma.cli.mensajeria estado

    # A one-time pairing code for a case (the operator, on its owner's behalf; the app's «Mensajería» does the same):
    python -m asistente.plataforma.cli.mensajeria emparejar-codigo --case <id> [--canal simplex]

Output is JSON. ``estado`` prints aggregates only: no contact ids, no names, no codes. The gateway itself runs in the
worker (service ``mensajeria``, with ``MENSAJERIA_ACTIVA=true``).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
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


def _ruta(db_path: str | None) -> str:
    return db_path or str(settings.DECISION_STATE_DB_PATH)


@click.group()
def cli() -> None:
    """Mensajería con el cerebro del hogar (SimpleX)."""


@cli.command("estado")
@_DB_OPTION
def estado(db_path: str | None) -> None:
    """Si está activa, los canales y su conexión, y cuántos chats hay vinculados (sin datos de nadie)."""
    from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria, tabla_existe
    from asistente.plataforma.mensajeria.servicio import activa, canales_configurados

    ruta = _ruta(db_path)
    salida: dict[str, Any] = {
        "activa": activa(),
        "canales_configurados": canales_configurados(),
        "canales": [],
        "chats_vinculados": {},
        "desconexiones_pendientes": 0,
    }
    if tabla_existe(ruta):
        store = AlmacenMensajeria(ruta)
        try:
            ahora = datetime.now(UTC)
            salida["canales"] = [
                {
                    "canal": e.canal,
                    "conectado": e.conectado,
                    "detalle": e.detalle,
                    "version": e.version,
                    "direccion": e.direccion,
                    "ultimo_error": e.ultimo_error,
                    "publicado_hace_s": round((ahora - momento).total_seconds(), 1),
                }
                for e, momento in store.estados()
            ]
            salida["chats_vinculados"] = dict(Counter(v.canal for v in store.vinculos()))
            salida["desconexiones_pendientes"] = len(store.desconexiones(limite=1000))
        finally:
            store.close()
    _imprimir(salida)


@cli.command("emparejar-codigo")
@_DB_OPTION
@click.option("--case", "case_id", required=True, help="Caso al que quedará vinculado el chat.")
@click.option("--canal", default=None, help="Canal (por defecto, el primero de MENSAJERIA_CANALES).")
def emparejar_codigo(db_path: str | None, case_id: str, canal: str | None) -> None:
    """Un código de emparejamiento de un solo uso para el dueño de un caso."""
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.plataforma.auth.store import AuthStore
    from asistente.plataforma.mensajeria import emparejamiento
    from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria
    from asistente.plataforma.mensajeria.servicio import activa, canales_configurados

    if not activa():
        raise click.ClickException("La mensajería no está activada (MENSAJERIA_ACTIVA=false).")
    nombres = canales_configurados()
    elegido = canal or (nombres[0] if nombres else "")
    if elegido not in nombres:
        raise click.ClickException(f"Canal no disponible: {elegido or '(ninguno)'}. Configurados: {nombres}.")
    ruta = _ruta(db_path)
    casos = SQLiteCaseStateStore(ruta)
    try:
        if casos.get_case(case_id) is None:
            raise click.ClickException(f"No existe el caso {case_id}.")
    finally:
        casos.close()
    propietario: str | None = None
    if settings.AUTH_MODE == "required":
        auth = AuthStore(ruta)
        try:
            propietario = auth.owner_of(case_id)
        finally:
            auth.close()
        if propietario is None:
            raise click.ClickException("Ese caso no tiene dueño: con cuentas, solo su dueño puede emparejar un chat.")
    store = AlmacenMensajeria(ruta)
    try:
        maximo = max(1, int(getattr(settings, "MENSAJERIA_CONTACTOS_POR_USUARIO", 1)))
        if sum(1 for v in store.vinculos_de_usuario(propietario) if v.canal == elegido) >= maximo:
            raise click.ClickException("El dueño ya tiene el máximo de chats vinculados en ese canal.")
        nuevo = emparejamiento.nuevo_codigo(
            store, canal=elegido, user_id=propietario, case_id=case_id, momento=datetime.now(UTC)
        )
        direccion = next((e.direccion for e, _m in store.estados() if e.canal == elegido), None)
    finally:
        store.close()
    _imprimir({"codigo": nuevo.codigo, "caduca": nuevo.caduca.isoformat(), "canal": elegido, "direccion": direccion})


if __name__ == "__main__":  # pragma: no cover
    cli()

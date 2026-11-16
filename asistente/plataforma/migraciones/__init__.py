"""Schema migrations of the SQLite databases (see :mod:`~hacienda_gpt.plataforma.migraciones.motor`).

The stores call :func:`asegurar_esquema` right after opening a connection; the API runs :func:`migrar` at
start-up; operators run ``python -m hacienda_gpt.cli.plataforma migrar [--estado] [--dry-run]``.

Adding a table or a column (any package)::

    from hacienda_gpt.plataforma.migraciones import Migracion, Propietario, registrar

    registrar(Propietario(nombre="tareas", paquete="hacienda_gpt.tareas", tablas=("tareas",), migraciones=(
        Migracion(1, "esquema_inicial", ("CREATE TABLE IF NOT EXISTS tareas (...)",)),
    )))

and call ``asegurar_esquema(conn, "tareas")`` where the store opens its connection. For an owner that is
part of the product, add its module to :mod:`~hacienda_gpt.plataforma.migraciones.catalogo` instead, so the
start-up migration and the CLI know it without importing the package. Never edit an applied migration:
append a new one.
"""

from __future__ import annotations

from hacienda_gpt.plataforma.migraciones.motor import (
    BASE_CASOS,
    BASE_DOCTRINA,
    BASES,
    TABLA_REGISTRO,
    EstadoPropietario,
    Informe,
    Migracion,
    MigracionError,
    Paso,
    Propietario,
    asegurar_esquema,
    automaticas,
    estado,
    migrar,
    olvidar,
    propietario,
    propietarios,
    registrar,
    volcado,
)

__all__ = [
    "BASES",
    "BASE_CASOS",
    "BASE_DOCTRINA",
    "TABLA_REGISTRO",
    "EstadoPropietario",
    "Informe",
    "Migracion",
    "MigracionError",
    "Paso",
    "Propietario",
    "asegurar_esquema",
    "automaticas",
    "estado",
    "migrar",
    "olvidar",
    "propietario",
    "propietarios",
    "registrar",
    "volcado",
]

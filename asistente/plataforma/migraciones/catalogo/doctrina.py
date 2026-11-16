"""``asistente.conocimiento.doctrine`` (``DoctrineGraph``): the doctrine graph, in ITS OWN file (``DOCTRINE_GRAPH_PATH``).

Public data only (rulings, consultas and their relations); rebuilt by ``cli.doctrine_graph build``.
Baseline copied byte for byte from ``doctrine/grafo.py`` (``_SCHEMA``, one statement each).
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import BASE_DOCTRINA, Migracion, Propietario

DDL = (
    """CREATE TABLE IF NOT EXISTS nodos (
    clave TEXT PRIMARY KEY, tipo TEXT NOT NULL, fecha TEXT, titulo TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '', vinculante INTEGER, fija_doctrina INTEGER NOT NULL DEFAULT 0,
    en_corpus INTEGER NOT NULL DEFAULT 0
)""",
    "CREATE TABLE IF NOT EXISTS alias (alias TEXT PRIMARY KEY, clave TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS aristas (
    origen TEXT NOT NULL, destino TEXT NOT NULL, relacion TEXT NOT NULL, confianza REAL NOT NULL,
    evidencia TEXT NOT NULL DEFAULT '', metodo TEXT NOT NULL, PRIMARY KEY (origen, destino)
)""",
    "CREATE INDEX IF NOT EXISTS aristas_destino ON aristas (destino)",
)

PROPIETARIO = Propietario(
    nombre="doctrina",
    paquete="asistente.conocimiento.doctrine",
    base=BASE_DOCTRINA,
    descripcion="Grafo de doctrina: documentos, alias y relaciones (datos públicos).",
    tablas=("nodos", "alias", "aristas"),
    otros_objetos=("aristas_destino",),
    migraciones=(Migracion(1, "esquema_inicial", DDL),),
)

"""``asistente.capacidades.correo``: Gmail connections, OAuth requests in flight, derived items, proposals.

Baseline copied byte for byte from ``modulos/correo/almacen.py`` (``_ESQUEMA``, one statement each).
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

DDL = (
    """CREATE TABLE IF NOT EXISTS correo_conexiones (
    case_id TEXT PRIMARY KEY,
    user_id TEXT,
    cuenta TEXT,
    alcance TEXT NOT NULL,
    token_cifrado TEXT NOT NULL,
    conectada_el TEXT NOT NULL,
    history_id TEXT,
    ultima_sincronizacion TEXT,
    ultimo_error TEXT,
    estado TEXT NOT NULL DEFAULT 'conectada'
)""",
    """CREATE TABLE IF NOT EXISTS correo_oauth_estados (
    estado_hash TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    user_id TEXT,
    verificador_cifrado TEXT NOT NULL,
    creado TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS correo_elementos (
    case_id TEXT NOT NULL,
    id TEXT NOT NULL,
    fecha TEXT NOT NULL,
    categoria TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (case_id, id)
)""",
    "CREATE INDEX IF NOT EXISTS idx_correo_elementos_fecha ON correo_elementos(case_id, fecha)",
    """CREATE TABLE IF NOT EXISTS correo_propuestas (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL,
    elemento_id TEXT NOT NULL,
    estado TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (case_id, elemento_id)
)""",
    """CREATE TABLE IF NOT EXISTS correo_contactos (
    case_id TEXT NOT NULL,
    direccion TEXT NOT NULL,
    PRIMARY KEY (case_id, direccion)
)""",
)

PROPIETARIO = Propietario(
    nombre="correo",
    paquete="asistente.capacidades.correo",
    descripcion="Conexión de Gmail (token cifrado), estados OAuth, elementos derivados, propuestas, contactos.",
    tablas=("correo_conexiones", "correo_oauth_estados", "correo_elementos", "correo_propuestas", "correo_contactos"),
    otros_objetos=("idx_correo_elementos_fecha",),
    migraciones=(Migracion(1, "esquema_inicial", DDL),),
)

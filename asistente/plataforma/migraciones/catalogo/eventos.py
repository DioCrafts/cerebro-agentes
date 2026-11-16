"""``asistente.nucleo.eventos``: the event bus (outbox, cursors, deliveries, timers, worker heartbeats).

Baseline copied byte for byte from ``eventos/esquema.py`` (``DDL``).
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

DDL = (
    """
    CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL UNIQUE,
        tipo TEXT NOT NULL,
        case_id TEXT,
        payload_json TEXT NOT NULL,
        clave TEXT UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eventos_case ON eventos(case_id)",
    """
    CREATE TABLE IF NOT EXISTS eventos_cursores (
        consumidor TEXT PRIMARY KEY,
        ultimo_id INTEGER NOT NULL DEFAULT 0,
        intentos INTEGER NOT NULL DEFAULT 0,
        siguiente_intento TEXT,
        error TEXT,
        actualizado TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eventos_entregas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evento_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        modulo TEXT NOT NULL,
        case_id TEXT NOT NULL,
        estado TEXT NOT NULL,
        intentos INTEGER NOT NULL DEFAULT 0,
        siguiente_intento TEXT NOT NULL,
        bloqueado_por TEXT,
        bloqueado_hasta TEXT,
        error TEXT,
        evento_creado TEXT NOT NULL,
        creado TEXT NOT NULL,
        terminado TEXT,
        duracion_ms REAL,
        latencia_ms REAL,
        avisos INTEGER,
        UNIQUE (evento_id, modulo, case_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entregas_estado ON eventos_entregas(estado, siguiente_intento)",
    "CREATE INDEX IF NOT EXISTS idx_entregas_case ON eventos_entregas(case_id)",
    """
    CREATE TABLE IF NOT EXISTS eventos_temporizadores (
        case_id TEXT NOT NULL,
        modulo TEXT NOT NULL,
        despertar_el TEXT NOT NULL,
        programado TEXT NOT NULL,
        PRIMARY KEY (case_id, modulo)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_temporizadores_fecha ON eventos_temporizadores(despertar_el)",
    """
    CREATE TABLE IF NOT EXISTS eventos_trabajadores (
        nombre TEXT PRIMARY KEY,
        latido TEXT NOT NULL,
        procesadas INTEGER NOT NULL DEFAULT 0
    )
    """,
)

PROPIETARIO = Propietario(
    nombre="eventos",
    paquete="asistente.nucleo.eventos",
    descripcion="Bus de eventos: bandeja de salida, cursores, entregas con reintentos, temporizadores, latidos.",
    tablas=("eventos", "eventos_cursores", "eventos_entregas", "eventos_temporizadores", "eventos_trabajadores"),
    otros_objetos=("idx_eventos_case", "idx_entregas_estado", "idx_entregas_case", "idx_temporizadores_fecha"),
    migraciones=(Migracion(1, "esquema_inicial", DDL),),
)

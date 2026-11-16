"""Tables of the event bus, in the cases' SQLite file (one backup and one erasure cover them).

* ``eventos`` — the outbox and the log: every event, in commit order (SQLite has one writer at a time, so
  ``id`` grows in commit order and a cursor over it never skips a late commit). ``clave`` makes a producer's
  re-run idempotent (same key, no second event).
* ``eventos_cursores`` — per consumer, the last event it has fully handled, and the retry state of the next.
* ``eventos_entregas`` — one row per (event, module, case) to wake: claimed with a lease, retried with
  backoff, ``muerto`` (dead letter) after the last attempt. Its timestamps are the metrics.
* ``eventos_temporizadores`` — per case and module, the next date the module asked to be woken on.
* ``eventos_trabajadores`` — heartbeat of each worker (``ops doctor`` reads it).
"""

from __future__ import annotations

import sqlite3

TABLAS = (
    "eventos",
    "eventos_cursores",
    "eventos_entregas",
    "eventos_temporizadores",
    "eventos_trabajadores",
)

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


def crear_tablas(conn: sqlite3.Connection) -> None:
    """Create the bus tables if missing (idempotent; safe inside an open transaction)."""
    for sentencia in DDL:
        conn.execute(sentencia)


def hay_tablas(conn: sqlite3.Connection) -> bool:
    fila = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'eventos'").fetchone()
    return fila is not None


__all__ = ["DDL", "TABLAS", "crear_tablas", "hay_tablas"]

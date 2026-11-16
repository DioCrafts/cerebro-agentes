"""``asistente.nucleo.decision`` (``SQLiteCaseStateStore``): the cases and their audit trail.

Baseline copied byte for byte from ``decision/state_store_sqlite.py`` — SQLite keeps the text of each
``CREATE`` statement, indentation included, so the text is part of the schema. Version 1 also adds the
``version`` column to a ``case_states`` created before optimistic concurrency existed (the store's old
«ALTER if missing»), so an old database is adopted exactly as the store would have upgraded it.
"""

from __future__ import annotations

import sqlite3

from asistente.plataforma.migraciones.motor import Migracion, Propietario

CASE_STATES = """
                CREATE TABLE IF NOT EXISTS case_states (
                    case_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
AUDIT_EVENTS = """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY (case_id) REFERENCES case_states(case_id) ON DELETE CASCADE
                )
                """


def _version_si_falta(conn: sqlite3.Connection) -> None:
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(case_states)")}
    if "version" not in columnas:
        conn.execute("ALTER TABLE case_states ADD COLUMN version INTEGER NOT NULL DEFAULT 0")


PROPIETARIO = Propietario(
    nombre="decision",
    paquete="asistente.nucleo.decision",
    descripcion="Expedientes (estado del caso en JSON) y su registro de auditoría.",
    tablas=("case_states", "audit_events"),
    otros_objetos=("idx_case_states_user", "idx_case_states_updated", "idx_audit_case_time"),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (
                CASE_STATES,
                AUDIT_EVENTS,
                "CREATE INDEX IF NOT EXISTS idx_case_states_user ON case_states(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_case_states_updated ON case_states(updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_audit_case_time ON audit_events(case_id, event_time)",
            ),
            funcion=_version_si_falta,
        ),
    ),
)

"""``asistente.plataforma.ops.consumo`` (``ConsumoStore``): the log of LLM calls (tokens, estimated cost).

Baseline copied byte for byte from ``ops/consumo.py``, including its «ALTER if missing» of the four columns
added after the first release, in the same order (SQLite appends each to the stored ``CREATE`` text).
"""

from __future__ import annotations

import sqlite3

from asistente.plataforma.migraciones.motor import Migracion, Propietario

LLM_USAGE = """
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    user_id TEXT,
                    case_id TEXT,
                    purpose TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    cache_write_tokens INTEGER,
                    cost_usd REAL
                )
                """
COLUMNAS_NUEVAS: tuple[tuple[str, str], ...] = (
    ("cache_write_1h_tokens", "INTEGER"),
    ("tier", "TEXT"),
    ("escalated", "INTEGER NOT NULL DEFAULT 0"),
    ("batch", "INTEGER NOT NULL DEFAULT 0"),
)


def _columnas_si_faltan(conn: sqlite3.Connection) -> None:
    existentes = {fila[1] for fila in conn.execute("PRAGMA table_info(llm_usage)")}
    for columna, tipo in COLUMNAS_NUEVAS:
        if columna not in existentes:
            conn.execute(f"ALTER TABLE llm_usage ADD COLUMN {columna} {tipo}")


PROPIETARIO = Propietario(
    nombre="consumo",
    paquete="asistente.plataforma.ops",
    descripcion="Registro de llamadas al LLM: tokens y coste estimado (anonimizado al borrar la cuenta).",
    tablas=("llm_usage",),
    otros_objetos=("idx_llm_usage_ts",),
    migraciones=(
        Migracion(
            1,
            "esquema_inicial",
            (LLM_USAGE, "CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts)"),
            funcion=_columnas_si_faltan,
        ),
    ),
)

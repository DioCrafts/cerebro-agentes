"""The user's «pausar la memoria» switch: one row per paused owner, in the cases' SQLite file.

Pausing deletes nothing: while paused, no turn is retained and nothing is recalled; what is in the bank stays
visible and erasable. The rows go with the case and with the account (triggers of the migration owner
``recuerdos_hindsight``).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from threading import RLock

from asistente.plataforma.migraciones.catalogo.recuerdos_hindsight import (
    DISPARADOR_CASO,
    DISPARADOR_USUARIO,
    PAUSA,
    RETIRADA,
)
from asistente.plataforma.migraciones.motor import asegurar_esquema
from asistente.plataforma.sqlite import abrir_sqlite


class AlmacenPausa:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()

    @contextmanager
    def _conectar(self) -> Generator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = abrir_sqlite(self.db_path, timeout=30.0, filas=True)
        try:
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                asegurar_esquema(conn, "recuerdos_hindsight")  # migrations (asistente/plataforma)
                # Same schema when the migrations are off.
                for sentencia in RETIRADA:
                    if not sentencia.startswith("DELETE FROM schema_migrations") or _existe(conn, "schema_migrations"):
                        conn.execute(sentencia)
                conn.execute(PAUSA)
                if _existe(conn, "case_states"):
                    conn.execute(DISPARADOR_CASO)
                if _existe(conn, "users"):
                    conn.execute(DISPARADOR_USUARIO)
            with conn:
                yield conn
        finally:
            conn.close()

    def pausada(self, case_id: str | None, user_id: str | None) -> bool:
        with self._lock, self._conectar() as conn:
            fila = conn.execute(
                "SELECT 1 FROM recuerdos_pausa WHERE case_id = ? AND user_id = ?", (case_id or "", user_id or "")
            ).fetchone()
        return fila is not None

    def fijar(self, case_id: str | None, user_id: str | None, *, pausada: bool) -> None:
        with self._lock, self._conectar() as conn:
            if pausada:
                conn.execute(
                    "INSERT OR IGNORE INTO recuerdos_pausa (case_id, user_id, desde) VALUES (?, ?, ?)",
                    (case_id or "", user_id or "", datetime.now(UTC).isoformat()),
                )
            else:
                conn.execute(
                    "DELETE FROM recuerdos_pausa WHERE case_id = ? AND user_id = ?", (case_id or "", user_id or "")
                )

    def borrar_de_usuario(self, user_id: str) -> None:
        with self._lock, self._conectar() as conn:
            conn.execute("DELETE FROM recuerdos_pausa WHERE user_id = ?", (user_id,))


def _existe(conn: sqlite3.Connection, tabla: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone() is not None
    )


__all__ = ["AlmacenPausa"]

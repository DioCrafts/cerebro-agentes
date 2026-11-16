"""The decisions table, in the same SQLite file as the cases (one backup, one erasure).

Two tables:

* ``decisiones`` — one row per decision (current state, JSON payload).
* ``decisiones_historial`` — what happened to each decision (created, edited, came back, deleted), so the
  user can see it. When the user deletes a decision, its row goes and every history entry of it is
  **redacted**: the user's words and the proposal are replaced by «[borrado]»; only the event, the ámbito
  and the dates remain.

When a case is erased (``SQLiteCaseStateStore.delete_case``, e.g. ``DELETE /me``), a trigger on
``case_states`` erases its decisions and their history in the same transaction: no code path that erases a
case can leave them behind.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from hacienda_gpt.memoria.modelo import Decision, ahora
from hacienda_gpt.plataforma.migraciones import asegurar_esquema

REDACTADO = "[borrado]"
# ``list`` is also a method of the store below, so the builtin needs another name inside the class.
_Filas = list[dict[str, Any]]


def _crear_tablas(conn: sqlite3.Connection) -> None:
    asegurar_esquema(conn, "memoria")  # migrations (hacienda_gpt/plataforma)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisiones (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            case_id TEXT NOT NULL,
            user_id TEXT,
            ambito TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisiones_case ON decisiones(case_id, seq)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisiones_historial (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            momento TEXT NOT NULL,
            evento TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisiones_historial_case ON decisiones_historial(case_id, seq)")


def _crear_disparador(conn: sqlite3.Connection) -> bool:
    """Erase a case's decisions with the case. Only possible once the cases table exists."""
    existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'case_states'").fetchone()
    if existe is None:
        return False
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS decisiones_borrar_con_caso AFTER DELETE ON case_states
        BEGIN
            DELETE FROM decisiones WHERE case_id = OLD.case_id;
            DELETE FROM decisiones_historial WHERE case_id = OLD.case_id;
        END
        """)
    return True


def tabla_existe(db_path: str) -> bool:
    """Whether ``db_path`` exists and has the decisions table (read-only check: never creates the file)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        fila = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'decisiones'").fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


class DecisionStore:
    """SQLite store of the user's decisions. Tables and trigger are created on first use."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._con_disparador = False

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            # Deleted words are overwritten in the file, not just unlinked (see :meth:`delete`).
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                _crear_tablas(conn)
            self._conn = conn
        if not self._con_disparador:
            with self._conn:
                self._con_disparador = _crear_disparador(self._conn)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._con_disparador = False

    # ------------------------------------------------------------------ #

    def _historial(self, conn: sqlite3.Connection, decision: Decision, evento: str, extra: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO decisiones_historial (decision_id, case_id, momento, evento, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                decision.id,
                decision.case_id,
                ahora().isoformat(),
                evento,
                json.dumps(
                    {"ambito": decision.ambito, "tipo": decision.tipo, "razon": decision.razon, **extra},
                    ensure_ascii=False,
                    default=str,
                ),
            ),
        )

    def add(self, decision: Decision) -> Decision:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "INSERT INTO decisiones (id, case_id, user_id, ambito, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (decision.id, decision.case_id, decision.user_id, decision.ambito, decision.model_dump_json()),
                )
                self._historial(conn, decision, "creada", {"fuente": decision.fuente, "boton": decision.boton})
        return decision

    def get(self, case_id: str, decision_id: str) -> Decision | None:
        """A decision of ``case_id`` (None when it does not exist FOR THAT CASE)."""
        with self._lock:
            fila = (
                self._get()
                .execute("SELECT payload_json FROM decisiones WHERE id = ? AND case_id = ?", (decision_id, case_id))
                .fetchone()
            )
        return Decision.model_validate_json(fila["payload_json"]) if fila else None

    def list(self, case_id: str) -> list[Decision]:
        """Decisions of a case, newest first."""
        with self._lock:
            filas = (
                self._get()
                .execute("SELECT payload_json FROM decisiones WHERE case_id = ? ORDER BY seq DESC", (case_id,))
                .fetchall()
            )
        return [Decision.model_validate_json(f["payload_json"]) for f in filas]

    def update(self, decision: Decision, *, evento: str, extra: dict[str, Any] | None = None) -> Decision:
        """Store a new state of an existing decision (same id and case) and log ``evento``."""
        with self._lock:
            conn = self._get()
            with conn:
                cursor = conn.execute(
                    "UPDATE decisiones SET ambito = ?, payload_json = ? WHERE id = ? AND case_id = ?",
                    (decision.ambito, decision.model_dump_json(), decision.id, decision.case_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(decision.id)
                if evento:
                    self._historial(conn, decision, evento, extra or {})
        return decision

    def contar_omision(self, case_id: str, decision_id: str, hoy: date) -> None:
        """A proposal was held back by this decision (no history entry: it would grow every day)."""
        with self._lock:
            conn = self._get()
            with conn:
                fila = conn.execute(
                    "SELECT payload_json FROM decisiones WHERE id = ? AND case_id = ?", (decision_id, case_id)
                ).fetchone()
                if fila is None:
                    return
                decision = Decision.model_validate_json(fila["payload_json"])
                if decision.ultima_omision == hoy:
                    return
                nueva = decision.model_copy(update={"omisiones": decision.omisiones + 1, "ultima_omision": hoy})
                conn.execute(
                    "UPDATE decisiones SET payload_json = ? WHERE id = ? AND case_id = ?",
                    (nueva.model_dump_json(), decision_id, case_id),
                )

    def delete(self, case_id: str, decision_id: str) -> bool:
        """Delete a decision and redact its history (the user's words never outlive the decision)."""
        with self._lock:
            conn = self._get()
            with conn:
                fila = conn.execute(
                    "SELECT payload_json FROM decisiones WHERE id = ? AND case_id = ?", (decision_id, case_id)
                ).fetchone()
                if fila is None:
                    return False
                decision = Decision.model_validate_json(fila["payload_json"])
                self._redactar(conn, case_id, [decision_id])
                conn.execute("DELETE FROM decisiones WHERE id = ? AND case_id = ?", (decision_id, case_id))
                conn.execute(
                    "INSERT INTO decisiones_historial (decision_id, case_id, momento, evento, payload_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        decision_id,
                        case_id,
                        ahora().isoformat(),
                        "borrada",
                        json.dumps({"ambito": decision.ambito, "tipo": decision.tipo, "razon": REDACTADO}),
                    ),
                )
            self._volcar(conn)
            return True

    @staticmethod
    def _volcar(conn: sqlite3.Connection) -> None:
        """Move the write-ahead log into the file so the old words do not stay readable in the ``-wal``."""
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.Error:  # another connection is busy: the next checkpoint will do it
            pass

    def _redactar(self, conn: sqlite3.Connection, case_id: str, ids: Iterable[str]) -> None:
        for decision_id in ids:
            for fila in conn.execute(
                "SELECT seq, payload_json FROM decisiones_historial WHERE decision_id = ? AND case_id = ?",
                (decision_id, case_id),
            ).fetchall():
                datos = json.loads(fila["payload_json"])
                for campo in ("razon", "propuesta", "motivo", "cambios"):
                    if campo in datos:
                        datos[campo] = REDACTADO
                conn.execute(
                    "UPDATE decisiones_historial SET payload_json = ? WHERE seq = ?",
                    (json.dumps(datos, ensure_ascii=False), fila["seq"]),
                )

    def delete_where(self, case_id: str, *, modulo: str) -> int:
        """Delete (and redact) every decision of one module of a case (the user erased the module's data)."""
        borradas = 0
        for decision in self.list(case_id):
            if decision.modulo == modulo and self.delete(case_id, decision.id):
                borradas += 1
        return borradas

    def historial(self, case_id: str) -> _Filas:
        with self._lock:
            filas = (
                self._get()
                .execute(
                    "SELECT decision_id, momento, evento, payload_json FROM decisiones_historial WHERE case_id = ? "
                    "ORDER BY seq",
                    (case_id,),
                )
                .fetchall()
            )
        return [
            {
                "decision_id": f["decision_id"],
                "momento": f["momento"],
                "evento": f["evento"],
                **json.loads(f["payload_json"]),
            }
            for f in filas
        ]

    def delete_for_case(self, case_id: str) -> int:
        """Erase every decision of a case and its history (right to erasure). Returns the decisions removed."""
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute("DELETE FROM decisiones_historial WHERE case_id = ?", (case_id,))
                borradas = conn.execute("DELETE FROM decisiones WHERE case_id = ?", (case_id,)).rowcount
            self._volcar(conn)
            return borradas

    def exportar(self, case_id: str) -> dict[str, Any]:
        """Everything kept about the case's decisions, as JSON-ready data (access / portability)."""
        return {
            "decisiones": [json.loads(d.model_dump_json()) for d in self.list(case_id)],
            "historial": self.historial(case_id),
        }


_almacenes: dict[str, DecisionStore] = {}
_almacenes_lock = RLock()


def almacen(db_path: str) -> DecisionStore:
    """The process-wide store for ``db_path`` (one connection per file, like the pending actions)."""
    clave = str(Path(db_path))
    with _almacenes_lock:
        store = _almacenes.get(clave)
        if store is None:
            store = _almacenes[clave] = DecisionStore(clave)
        return store


def olvidar_almacenes() -> None:
    """Close every cached store (tests that recreate their database files)."""
    with _almacenes_lock:
        for store in _almacenes.values():
            store.close()
        _almacenes.clear()


def exportar_decisiones(db_path: str, case_id: str) -> dict[str, Any]:
    """The account export's entry for one case; empty without the table (never creates it)."""
    if not tabla_existe(db_path):
        return {"decisiones": [], "historial": []}
    return almacen(db_path).exportar(case_id)


__all__ = [
    "REDACTADO",
    "DecisionStore",
    "almacen",
    "exportar_decisiones",
    "olvidar_almacenes",
    "tabla_existe",
]

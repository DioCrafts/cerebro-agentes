"""The tasks' tables, in the same SQLite file as the cases (one backup, one erasure).

* ``tareas`` — one row per task: its state columns (for the engine's queries) and the whole task as JSON.
  ``version`` gives optimistic concurrency (a save over a newer version is refused) and ``bloqueado_*`` a
  lease, so two workers (or a worker and the API) never advance the same task at once; a worker that dies
  loses its lease and another one resumes the task.
* ``tareas_historial`` — the append-only log of each task: event, step id, states, counts. Never a value the
  user gave (no goal, no result, no argument): it is the audit trail.
* ``tareas_senales`` — events of the bus that concern a waiting step (written by the bus consumer, in the
  bus's own transaction), until the engine has handled them.

The same DDL is the baseline of the migrations (``plataforma/migraciones/catalogo/tareas.py``; a test compares
both byte for byte): change the schema there with a new migration, never here alone.

When a case is erased (``SQLiteCaseStateStore.delete_case``, e.g. ``DELETE /me``) a trigger on
``case_states`` erases its tasks, their history and their signals in the same transaction.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from asistente.nucleo.tareas.modelo import EN_MARCHA, EntradaHistorial, Tarea, ahora
from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.sqlite import abrir_sqlite

TABLAS = ("tareas", "tareas_historial", "tareas_senales")

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS tareas (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        case_id TEXT NOT NULL,
        estado TEXT NOT NULL,
        proxima_revision TEXT,
        version INTEGER NOT NULL DEFAULT 0,
        bloqueado_por TEXT,
        bloqueado_hasta TEXT,
        actualizada TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tareas_case ON tareas(case_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_tareas_estado ON tareas(estado)",
    """
    CREATE TABLE IF NOT EXISTS tareas_historial (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        tarea_id TEXT NOT NULL,
        case_id TEXT NOT NULL,
        momento TEXT NOT NULL,
        evento TEXT NOT NULL,
        paso_id TEXT,
        detalle_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tareas_historial ON tareas_historial(tarea_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_tareas_historial_case ON tareas_historial(case_id)",
    """
    CREATE TABLE IF NOT EXISTS tareas_senales (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        tarea_id TEXT NOT NULL,
        paso_id TEXT NOT NULL,
        case_id TEXT NOT NULL,
        evento_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        creada TEXT NOT NULL,
        procesada INTEGER NOT NULL DEFAULT 0,
        UNIQUE (tarea_id, paso_id, evento_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tareas_senales ON tareas_senales(procesada, tarea_id)",
)

_DISPARADOR = """
    CREATE TRIGGER IF NOT EXISTS tareas_borrar_con_caso AFTER DELETE ON case_states
    BEGIN
        DELETE FROM tareas WHERE case_id = OLD.case_id;
        DELETE FROM tareas_historial WHERE case_id = OLD.case_id;
        DELETE FROM tareas_senales WHERE case_id = OLD.case_id;
    END
"""


class ConflictoDeVersionError(RuntimeError):
    """Somebody saved the task in between: reload it and try again."""


def crear_tablas(conn: sqlite3.Connection) -> None:
    """Create the tables (idempotent; safe inside an open transaction) and, if possible, the erasure trigger."""
    asegurar_esquema(conn, "tareas")  # migrations (asistente/plataforma/migraciones/catalogo/tareas.py)
    for sentencia in _DDL:
        conn.execute(sentencia)
    crear_disparador(conn)


def crear_disparador(conn: sqlite3.Connection) -> bool:
    existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'case_states'").fetchone()
    if existe is None:
        return False
    conn.execute(_DISPARADOR)
    return True


def tabla_existe(db_path: str) -> bool:
    """Whether ``db_path`` exists and has the tasks table (read-only check: never creates the file)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        return (
            conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tareas'").fetchone() is not None
        )
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _iso(momento: datetime) -> str:
    return momento.astimezone(UTC).isoformat()


# ``list`` is also a method of the store below, so the builtin needs another name inside the class.
_Tareas = list[Tarea]
_Filas = list[dict[str, Any]]
_Ids = list[str]
_Historial = list[EntradaHistorial]
_Seqs = list[int]


class TareaStore:
    """SQLite store of the long-running tasks. Tables and trigger are created on first use."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._con_disparador = False

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = abrir_sqlite(self.db_path, timeout=30.0, check_same_thread=False, filas=True)
            # Erased tasks are overwritten in the file, not just unlinked.
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                crear_tablas(conn)
            self._conn = conn
        if not self._con_disparador:
            with self._conn:
                self._con_disparador = crear_disparador(self._conn)
        return self._conn

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection]:
        with self._lock:
            conn = self._get()
            with conn:
                yield conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._con_disparador = False

    # ------------------------------------------------------------------ tasks

    def add(self, tarea: Tarea, *, evento: str = "creada", **detalle: Any) -> Tarea:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO tareas (id, case_id, estado, proxima_revision, version, actualizada, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    tarea.id,
                    tarea.case_id,
                    tarea.estado,
                    tarea.proxima_revision.isoformat() if tarea.proxima_revision else None,
                    tarea.version,
                    _iso(tarea.actualizada),
                    tarea.model_dump_json(),
                ),
            )
            self._historial(conn, tarea, evento, None, {"estado": tarea.estado, **detalle})
        return tarea

    def get(self, tarea_id: str) -> Tarea | None:
        with self._lock:
            fila = self._get().execute("SELECT payload_json FROM tareas WHERE id = ?", (tarea_id,)).fetchone()
        return Tarea.model_validate_json(fila["payload_json"]) if fila else None

    def list(self, case_id: str) -> _Tareas:
        """Tasks of a case, newest first."""
        with self._lock:
            filas = (
                self._get()
                .execute("SELECT payload_json FROM tareas WHERE case_id = ? ORDER BY seq DESC", (case_id,))
                .fetchall()
            )
        return [Tarea.model_validate_json(f["payload_json"]) for f in filas]

    def ids_en_marcha(self) -> _Ids:
        """Every task the engine should look at (active or waiting), oldest first."""
        marcas = ",".join("?" * len(EN_MARCHA))
        with self._lock:
            filas = (
                self._get()
                .execute(f"SELECT id FROM tareas WHERE estado IN ({marcas}) ORDER BY seq", tuple(sorted(EN_MARCHA)))
                .fetchall()
            )
        return [str(f["id"]) for f in filas]

    def en_marcha_de_caso(self, case_id: str, conn: sqlite3.Connection | None = None) -> _Tareas:
        marcas = ",".join("?" * len(EN_MARCHA))
        sql = f"SELECT payload_json FROM tareas WHERE case_id = ? AND estado IN ({marcas}) ORDER BY seq"
        params = (case_id, *sorted(EN_MARCHA))
        if conn is not None:
            filas = conn.execute(sql, params).fetchall()
        else:
            with self._lock:
                filas = self._get().execute(sql, params).fetchall()
        return [Tarea.model_validate_json(f[0]) for f in filas]

    def guardar(
        self,
        tarea: Tarea,
        *,
        evento: str | None = None,
        paso_id: str | None = None,
        eventos: Sequence[tuple[str, str | None, Mapping[str, Any]]] = (),
        **detalle: Any,
    ) -> Tarea:
        """Save ``tarea`` over the stored version it was read at; the saved copy has ``version + 1``.

        Raises :class:`ConflictoDeVersionError` when another writer saved it in between. ``evento`` (and every
        ``(evento, paso_id, detalle)`` of ``eventos``) adds a line to the history in the same transaction.
        """
        nueva = tarea.model_copy(update={"version": tarea.version + 1, "actualizada": ahora()})
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE tareas SET estado = ?, proxima_revision = ?, version = ?, actualizada = ?, payload_json = ? "
                "WHERE id = ? AND version = ?",
                (
                    nueva.estado,
                    nueva.proxima_revision.isoformat() if nueva.proxima_revision else None,
                    nueva.version,
                    _iso(nueva.actualizada),
                    nueva.model_dump_json(),
                    tarea.id,
                    tarea.version,
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictoDeVersionError(f"la tarea {tarea.id} cambió mientras se guardaba")
            for nombre, paso, datos in eventos:
                self._historial(conn, nueva, nombre, paso, datos)
            if evento is not None:
                self._historial(conn, nueva, evento, paso_id, detalle)
        return nueva

    def anotar(self, tarea: Tarea, evento: str, paso_id: str | None = None, **detalle: Any) -> None:
        """Append one line to the task's history (its own transaction)."""
        with self._tx() as conn:
            self._historial(conn, tarea, evento, paso_id, detalle)

    @staticmethod
    def _historial(
        conn: sqlite3.Connection, tarea: Tarea, evento: str, paso_id: str | None, detalle: Mapping[str, Any]
    ) -> None:
        # Only scalars: a caller cannot slip a structure with values into the audit trail by mistake.
        limpio = {k: v for k, v in detalle.items() if isinstance(v, str | int | bool) or v is None}
        conn.execute(
            "INSERT INTO tareas_historial (tarea_id, case_id, momento, evento, paso_id, detalle_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tarea.id, tarea.case_id, _iso(ahora()), evento, paso_id, json.dumps(limpio, ensure_ascii=False)),
        )

    def historial(self, tarea_id: str) -> _Historial:
        with self._lock:
            filas = (
                self._get()
                .execute("SELECT * FROM tareas_historial WHERE tarea_id = ? ORDER BY seq", (tarea_id,))
                .fetchall()
            )
        return [
            EntradaHistorial(
                seq=int(f["seq"]),
                tarea_id=str(f["tarea_id"]),
                momento=datetime.fromisoformat(f["momento"]),
                evento=str(f["evento"]),
                paso_id=f["paso_id"],
                detalle=json.loads(f["detalle_json"]),
            )
            for f in filas
        ]

    # ------------------------------------------------------------------ leases

    def reclamar(self, tarea_id: str, trabajador: str, *, segundos: float = 300.0) -> bool:
        """Take the task's lease (or renew one's own). False while another worker holds a live lease."""
        momento = ahora()
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE tareas SET bloqueado_por = ?, bloqueado_hasta = ? WHERE id = ? AND "
                "(bloqueado_hasta IS NULL OR bloqueado_hasta < ? OR bloqueado_por = ?)",
                (trabajador, _iso(momento + timedelta(seconds=segundos)), tarea_id, _iso(momento), trabajador),
            )
            return cursor.rowcount == 1

    def soltar(self, tarea_id: str, trabajador: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE tareas SET bloqueado_por = NULL, bloqueado_hasta = NULL WHERE id = ? AND bloqueado_por = ?",
                (tarea_id, trabajador),
            )

    # ------------------------------------------------------------------ signals

    def senales(self, tarea_id: str) -> _Filas:
        """Unhandled signals of a task (events a waiting step received), oldest first."""
        with self._lock:
            filas = (
                self._get()
                .execute(
                    "SELECT seq, paso_id, evento_id, tipo FROM tareas_senales WHERE tarea_id = ? AND procesada = 0 "
                    "ORDER BY seq",
                    (tarea_id,),
                )
                .fetchall()
            )
        return [dict(f) for f in filas]

    def tareas_con_senales(self) -> _Ids:
        with self._lock:
            filas = (
                self._get()
                .execute("SELECT DISTINCT tarea_id FROM tareas_senales WHERE procesada = 0 ORDER BY tarea_id")
                .fetchall()
            )
        return [str(f[0]) for f in filas]

    def marcar_senales(self, seqs: _Seqs) -> None:
        if not seqs:
            return
        with self._tx() as conn:
            conn.execute(
                f"UPDATE tareas_senales SET procesada = 1 WHERE seq IN ({','.join('?' * len(seqs))})", tuple(seqs)
            )

    # ------------------------------------------------------------------ privacy

    def exportar(self, case_id: str) -> _Filas:
        """Every task of a case with its history (access / portability: ``GET /me/export``)."""
        salida = []
        for tarea in self.list(case_id):
            datos = json.loads(tarea.model_dump_json())
            datos["historial"] = [json.loads(e.model_dump_json()) for e in self.historial(tarea.id)]
            salida.append(datos)
        return salida

    def borrar_caso(self, case_id: str) -> int:
        """Erase every task of a case now (the trigger does it too when the case itself is erased)."""
        with self._tx() as conn:
            borradas = conn.execute("DELETE FROM tareas WHERE case_id = ?", (case_id,)).rowcount
            conn.execute("DELETE FROM tareas_historial WHERE case_id = ?", (case_id,))
            conn.execute("DELETE FROM tareas_senales WHERE case_id = ?", (case_id,))
        return int(borradas)

    def proxima_revision_de_caso(self, case_id: str) -> date | None:
        fechas = [t.proxima_revision for t in self.en_marcha_de_caso(case_id) if t.proxima_revision is not None]
        return min(fechas) if fechas else None


def exportar_tareas(db_path: str, case_id: str) -> _Filas:
    """The tasks of a case for ``GET /me/export`` (empty, without creating anything, when there are none)."""
    if not tabla_existe(db_path):
        return []
    store = TareaStore(db_path)
    try:
        return store.exportar(case_id)
    finally:
        store.close()


__all__ = [
    "TABLAS",
    "ConflictoDeVersionError",
    "TareaStore",
    "crear_disparador",
    "crear_tablas",
    "exportar_tareas",
    "tabla_existe",
]

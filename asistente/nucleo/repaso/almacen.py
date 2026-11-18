"""The passes, one row per case and period, in the cases' database (one backup, one erasure).

A row (:class:`Pasada`) keeps what a pass decided and wrote: the ranked points as they were (the snapshot the model
read and the gate checked), the accepted text, the notification it became, and — while a batch is in flight — the
batch and the request id to collect. It is also the watermark: what is newer than the case's previous pass is
«new» in the next one.

Privacy: the snapshot holds the same titles and figures as the digest notification itself; nothing else. The rows
go with the case (trigger on ``case_states``, so no erasure path can leave them behind), are exported with
:func:`exportar_repaso`, and rows older than :data:`DIAS_RETENCION` are purged (the latest of each case stays: it is
the watermark). Batch ``custom_id`` values are hashes: no personal data travels in them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.repaso.priorizacion import Priorizacion
from asistente.nucleo.repaso.puerta import Redaccion, Violacion
from asistente.nucleo.repaso.senales import Marca
from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.migraciones.catalogo.repaso import DISPARADOR, INDICE_LOTES, PASADAS

EstadoPasada = Literal["pendiente_lote", "aplicando", "aplicada", "descartada"]
OrigenPasada = Literal["modelo", "mixto", "determinista"]

DIAS_RETENCION = 120
# A row left «aplicando» this long (a crash while applying a batch result) is collected again.
ARRIENDO_APLICAR = timedelta(hours=1)


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pasada(_Modelo):
    case_id: str = Field(min_length=1)
    periodo: str = Field(min_length=1, max_length=20)
    key: str = Field(min_length=1)
    frecuencia: Literal["semanal", "diaria"]
    hoy: date
    estado: EstadoPasada
    origen: OrigenPasada | None = None
    notification_id: str | None = None
    lote_id: str | None = None
    custom_id: str | None = None
    lote_enviado: datetime | None = None
    evento_hasta: int = 0
    creada: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actualizada: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prioridad: Priorizacion
    redaccion: Redaccion | None = None
    violaciones: list[Violacion] = Field(default_factory=list)
    intentos: int = 0
    # Sources that failed while collecting (logged).
    errores: list[str] = Field(default_factory=list)


def custom_id_de(case_id: str, periodo: str) -> str:
    """The batch request id of a case's pass: a hash (the batch API sees no case id)."""
    return "repaso_" + hashlib.sha256(f"{case_id}\x00{periodo}".encode()).hexdigest()[:40]


def _iso(momento: datetime) -> str:
    return momento.astimezone(UTC).isoformat()


class RepasoStore:
    """SQLite store of the passes. A short connection per call (the job and the API share the file)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                asegurar_esquema(conn, "repaso")  # migrations (asistente/plataforma)
                conn.execute(PASADAS)
                conn.execute(INDICE_LOTES)
                existe = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'case_states'"
                ).fetchone()
                if existe is not None:
                    conn.execute(DISPARADOR)
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _pasada(fila: sqlite3.Row) -> Pasada:
        return Pasada.model_validate_json(fila["payload_json"])

    def get(self, case_id: str, periodo: str) -> Pasada | None:
        with self._lock, self._conectar() as conn:
            fila = conn.execute(
                "SELECT payload_json FROM repaso_pasadas WHERE case_id = ? AND periodo = ?", (case_id, periodo)
            ).fetchone()
        return self._pasada(fila) if fila else None

    def guardar(self, pasada: Pasada) -> Pasada:
        """Insert or replace the case's row for the period."""
        pasada = pasada.model_copy(update={"actualizada": datetime.now(UTC)})
        with self._lock, self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO repaso_pasadas (case_id, periodo, estado, origen, notification_id, lote_id, custom_id,
                    evento_hasta, creada, actualizada, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (case_id, periodo) DO UPDATE SET
                    estado = excluded.estado, origen = excluded.origen, notification_id = excluded.notification_id,
                    lote_id = excluded.lote_id, custom_id = excluded.custom_id, evento_hasta = excluded.evento_hasta,
                    actualizada = excluded.actualizada, payload_json = excluded.payload_json
                """,
                (
                    pasada.case_id,
                    pasada.periodo,
                    pasada.estado,
                    pasada.origen,
                    pasada.notification_id,
                    pasada.lote_id,
                    pasada.custom_id,
                    pasada.evento_hasta,
                    _iso(pasada.creada),
                    _iso(pasada.actualizada),
                    pasada.model_dump_json(),
                ),
            )
        return pasada

    def reclamar_lote(self, case_id: str, periodo: str, *, ahora: datetime | None = None) -> bool:
        """Claim a row waiting for its batch result (or left «aplicando» by a crashed run): True for the one run
        that applies it, so a result is applied once even when two runs collect the same batch."""
        momento = ahora or datetime.now(UTC)
        limite = _iso(momento - ARRIENDO_APLICAR)
        with self._lock, self._conectar() as conn:
            fila = conn.execute(
                "SELECT payload_json FROM repaso_pasadas WHERE case_id = ? AND periodo = ? AND "
                "(estado = 'pendiente_lote' OR (estado = 'aplicando' AND actualizada < ?))",
                (case_id, periodo, limite),
            ).fetchone()
            if fila is None:
                return False
            pasada = self._pasada(fila).model_copy(update={"estado": "aplicando", "actualizada": momento})
            cursor = conn.execute(
                "UPDATE repaso_pasadas SET estado = 'aplicando', actualizada = ?, payload_json = ? "
                "WHERE case_id = ? AND periodo = ? AND (estado = 'pendiente_lote' OR (estado = 'aplicando' AND "
                "actualizada < ?))",
                (_iso(momento), pasada.model_dump_json(), case_id, periodo, limite),
            )
            return cursor.rowcount == 1

    def marca(self, case_id: str, *, antes_de: str) -> Marca:
        """The watermark: the case's latest applied pass of another period (``Marca()`` when there is none)."""
        with self._lock, self._conectar() as conn:
            fila = conn.execute(
                "SELECT creada, evento_hasta FROM repaso_pasadas WHERE case_id = ? AND periodo != ? AND estado = "
                "'aplicada' ORDER BY creada DESC, seq DESC LIMIT 1",
                (case_id, antes_de),
            ).fetchone()
        if fila is None:
            return Marca()
        return Marca(momento=datetime.fromisoformat(fila["creada"]), evento_id=int(fila["evento_hasta"] or 0))

    def en_lote(self, *, ahora: datetime | None = None) -> list[Pasada]:
        """Rows waiting for a batch result (and rows a crashed run left «aplicando» for too long)."""
        limite = _iso((ahora or datetime.now(UTC)) - ARRIENDO_APLICAR)
        with self._lock, self._conectar() as conn:
            filas = conn.execute(
                "SELECT payload_json FROM repaso_pasadas WHERE estado = 'pendiente_lote' "
                "OR (estado = 'aplicando' AND actualizada < ?) ORDER BY seq",
                (limite,),
            ).fetchall()
        return [self._pasada(f) for f in filas]

    def de_caso(self, case_id: str) -> list[Pasada]:
        """The case's passes, newest first."""
        with self._lock, self._conectar() as conn:
            filas = conn.execute(
                "SELECT payload_json FROM repaso_pasadas WHERE case_id = ? ORDER BY seq DESC", (case_id,)
            ).fetchall()
        return [self._pasada(f) for f in filas]

    def por_notificacion(self, case_id: str, notification_id: str) -> Pasada | None:
        with self._lock, self._conectar() as conn:
            fila = conn.execute(
                "SELECT payload_json FROM repaso_pasadas WHERE case_id = ? AND notification_id = ?",
                (case_id, notification_id),
            ).fetchone()
        return self._pasada(fila) if fila else None

    def borrar_caso(self, case_id: str) -> int:
        with self._lock, self._conectar() as conn:
            return conn.execute("DELETE FROM repaso_pasadas WHERE case_id = ?", (case_id,)).rowcount

    def purgar(self, *, ahora: datetime | None = None, dias: int = DIAS_RETENCION) -> int:
        """Delete finished rows older than ``dias`` (each case's latest row stays: it is the watermark)."""
        limite = _iso((ahora or datetime.now(UTC)) - timedelta(days=dias))
        with self._lock, self._conectar() as conn:
            return conn.execute(
                "DELETE FROM repaso_pasadas WHERE actualizada < ? AND estado IN ('aplicada', 'descartada') "
                "AND seq NOT IN (SELECT MAX(seq) FROM repaso_pasadas GROUP BY case_id)",
                (limite,),
            ).rowcount

    def exportar(self, case_id: str) -> list[dict[str, Any]]:
        return [json.loads(p.model_dump_json()) for p in self.de_caso(case_id)]


def tabla_existe(db_path: str) -> bool:
    """Whether ``db_path`` has the passes' table (read-only: never creates the file)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        fila = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'repaso_pasadas'").fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def exportar_repaso(db_path: str, case_id: str) -> list[dict[str, Any]]:
    """The account export's entry for one case; empty without the table (never creates it)."""
    if not tabla_existe(db_path):
        return []
    return RepasoStore(db_path).exportar(case_id)


__all__ = [
    "ARRIENDO_APLICAR",
    "DIAS_RETENCION",
    "EstadoPasada",
    "OrigenPasada",
    "Pasada",
    "RepasoStore",
    "custom_id_de",
    "exportar_repaso",
    "tabla_existe",
]

"""Actions that reach outside the app wait here for the user's explicit confirmation.

A module tool with ``efecto="actua"`` (send, contract, cancel…) is offered to the model like any other, but
calling it never runs it: the core stores the call as a pending action, tells the model (and the user) that
it is waiting, and runs it only after the owner of the case confirms it (``POST /acciones/{id}/confirmar``).
A rejected or already-decided action can never run. Same SQLite file as the cases, so one backup and one
erasure cover it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from asistente.plataforma.migraciones import asegurar_esquema

logger = logging.getLogger(__name__)

EstadoAccion = Literal["pendiente", "ejecutando", "ejecutada", "fallida", "rechazada"]


class AccionPendiente(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"acc_{uuid4().hex}")
    case_id: str = Field(min_length=1)
    modulo: str = Field(min_length=1)
    herramienta: str = Field(min_length=1)
    argumentos: dict[str, Any] = Field(default_factory=dict)
    # What will happen if the user confirms, in Spanish.
    resumen: str = Field(min_length=1)
    estado: EstadoAccion = "pendiente"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    # When the confirmed action started running (``ejecutando``): an old one is a run that died half-way.
    iniciada_at: datetime | None = None
    # The tool's answer once it ran (or why it failed).
    resultado: str | None = None
    es_error: bool = False


MENSAJE_INTERRUMPIDA = (
    "La ejecución se interrumpió y no se pudo confirmar si terminó. Revisa el resultado antes de repetirla."
)
MENSAJE_ERROR_INTERNO = "La acción falló por un error interno. No se ha cambiado nada; inténtalo más tarde."
MENSAJE_SIN_GUARDAR = "La acción se hizo, pero no se pudo guardar su resultado en el expediente; inténtalo de nuevo."


def completar_accion(
    acciones: AccionStore,
    accion_id: str,
    *,
    ejecutar: Callable[[], tuple[str, bool]],
    guardar: Callable[[], None] | None = None,
    intentos: int = 3,
) -> AccionPendiente:
    """Run an action already moved to ``ejecutando`` and ALWAYS leave it decided (``ejecutada`` or ``fallida``).

    ``ejecutar`` returns ``(texto, es_error)``; an exception becomes a generic failure (its text goes to the log
    only). ``guardar`` writes what the action changed; it is retried on a version conflict (it must re-read the
    case itself), and when it still fails the action ends ``fallida``. Whatever happens, the row never stays in
    ``ejecutando``.
    """
    from asistente.nucleo.decision.state_store import CaseVersionConflictError

    texto, es_error = MENSAJE_ERROR_INTERNO, True
    try:
        try:
            texto, es_error = ejecutar()
        except Exception:
            logger.exception("action %s failed", accion_id)
            texto, es_error = MENSAJE_ERROR_INTERNO, True
        if not es_error and guardar is not None:
            for intento in range(max(1, intentos)):
                try:
                    guardar()
                    break
                except CaseVersionConflictError:
                    if intento + 1 >= max(1, intentos):
                        logger.warning("action %s: the case kept changing; result not saved", accion_id)
                        texto, es_error = MENSAJE_SIN_GUARDAR, True
                except Exception:
                    logger.exception("action %s: saving its result failed", accion_id)
                    texto, es_error = MENSAJE_SIN_GUARDAR, True
                    break
    finally:
        final = acciones.transicion(
            accion_id,
            desde="ejecutando",
            hacia="fallida" if es_error else "ejecutada",
            resultado=texto,
            es_error=es_error,
        )
    if final is None:  # someone else decided it meanwhile (the stuck-actions sweep): report what is stored
        actual = acciones.get(accion_id)
        if actual is None:
            raise LookupError(accion_id)
        return actual
    return final


def describir(modulo_nombre: str, descripcion_herramienta: str, argumentos: Mapping[str, Any]) -> str:
    """One readable line: which module, what the tool does, with which data."""
    que = descripcion_herramienta.strip().split(". ")[0].rstrip(".")
    datos = "; ".join(f"{clave}: {valor}" for clave, valor in argumentos.items()) or "sin datos adicionales"
    return f"{modulo_nombre} — {que}. Datos: {datos}."


# ``list`` is also a method of the store below, so the builtin needs another name inside the class.
_Filas = list[dict[str, Any]]


class AccionStore:
    """SQLite store of pending actions. The table is created on first use."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            with conn:
                asegurar_esquema(conn, "acciones")  # migrations (asistente/plataforma)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS acciones_pendientes (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        case_id TEXT NOT NULL,
                        estado TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_acciones_case ON acciones_pendientes(case_id, seq)")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def add(self, accion: AccionPendiente) -> AccionPendiente:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "INSERT INTO acciones_pendientes (id, case_id, estado, payload_json) VALUES (?, ?, ?, ?)",
                    (accion.id, accion.case_id, accion.estado, accion.model_dump_json()),
                )
        return accion

    def get(self, accion_id: str) -> AccionPendiente | None:
        with self._lock:
            row = (
                self._get()
                .execute("SELECT payload_json FROM acciones_pendientes WHERE id = ?", (accion_id,))
                .fetchone()
            )
        return AccionPendiente.model_validate_json(row["payload_json"]) if row else None

    def list(self, case_id: str, *, estado: EstadoAccion | None = None) -> list[AccionPendiente]:
        """Actions of a case, newest first."""
        query = "SELECT payload_json FROM acciones_pendientes WHERE case_id = ?"
        params: tuple[Any, ...] = (case_id,)
        if estado is not None:
            query += " AND estado = ?"
            params += (estado,)
        with self._lock:
            rows = self._get().execute(query + " ORDER BY seq DESC", params).fetchall()
        return [AccionPendiente.model_validate_json(row["payload_json"]) for row in rows]

    def transicion(
        self,
        accion_id: str,
        *,
        desde: EstadoAccion,
        hacia: EstadoAccion,
        resultado: str | None = None,
        es_error: bool = False,
    ) -> AccionPendiente | None:
        """Move an action from ``desde`` to ``hacia`` atomically; None when it was not in ``desde``.

        This is what makes a confirmation run at most once: two concurrent confirmations race on the same
        UPDATE and only one of them sees the action still ``pendiente``.
        """
        with self._lock:
            conn = self._get()
            with conn:
                row = conn.execute(
                    "SELECT payload_json FROM acciones_pendientes WHERE id = ? AND estado = ?", (accion_id, desde)
                ).fetchone()
                if row is None:
                    return None
                accion = AccionPendiente.model_validate_json(row["payload_json"])
                cambios: dict[str, Any] = {"estado": hacia}
                if hacia != "ejecutando":
                    cambios["decided_at"] = accion.decided_at or datetime.now(UTC)
                else:
                    cambios["iniciada_at"] = datetime.now(UTC)
                if resultado is not None:
                    cambios.update(resultado=resultado, es_error=es_error)
                actualizada = accion.model_copy(update=cambios)
                cursor = conn.execute(
                    "UPDATE acciones_pendientes SET estado = ?, payload_json = ? WHERE id = ? AND estado = ?",
                    (hacia, actualizada.model_dump_json(), accion_id, desde),
                )
                return actualizada if cursor.rowcount == 1 else None

    def recuperar_atascadas(self, *, mas_de: timedelta = timedelta(minutes=15)) -> int:
        """Actions left in ``ejecutando`` longer than ``mas_de`` (the process died mid-run) end ``fallida``, with a
        message for the user: never stuck forever, never silently re-run (the tool may have half-acted)."""
        limite = datetime.now(UTC) - mas_de
        with self._lock:
            filas = (
                self._get()
                .execute("SELECT id, payload_json FROM acciones_pendientes WHERE estado = 'ejecutando'")
                .fetchall()
            )
        recuperadas = 0
        for fila in filas:
            accion = AccionPendiente.model_validate_json(fila["payload_json"])
            inicio = accion.iniciada_at or accion.created_at
            if inicio > limite:
                continue
            if self.transicion(
                accion.id, desde="ejecutando", hacia="fallida", resultado=MENSAJE_INTERRUMPIDA, es_error=True
            ):
                recuperadas += 1
        return recuperadas

    def delete_for_case(self, case_id: str) -> int:
        with self._lock:
            conn = self._get()
            with conn:
                return conn.execute("DELETE FROM acciones_pendientes WHERE case_id = ?", (case_id,)).rowcount

    def rechazar_pendientes_de(self, case_id: str, modulo: str) -> int:
        """Reject a module's pending actions of a case (the user switched the module off): they can never run."""
        borradas = 0
        for accion in self.list(case_id, estado="pendiente"):
            if accion.modulo == modulo and self.transicion(accion.id, desde="pendiente", hacia="rechazada"):
                borradas += 1
        return borradas

    def exportar(self, case_id: str) -> _Filas:
        return [json.loads(a.model_dump_json()) for a in self.list(case_id)]


__all__ = ["AccionPendiente", "AccionStore", "EstadoAccion", "describir"]

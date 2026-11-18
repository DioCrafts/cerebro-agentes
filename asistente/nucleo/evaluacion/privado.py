"""Accepted real cases: the users' corrections turned into regression cases, kept PRIVATE.

Why a store of its own, outside the repository: a real case — even masked — comes from one person's household.
It must be re-run by every evaluation (that is its value: the same mistake never comes back unnoticed) and it
must leave with that person (export, erasure). So:

* it lives in a private SQLite file (``EVALUACION_CASOS_REALES_DB_PATH``; empty = ``evaluacion_casos_reales.sqlite3``
  next to the cases' database), never under ``asistente/nucleo/evaluacion/casos`` and never in git (``data/`` is
  ignored); CI never sees it;
* each row keeps the ``case_id`` and ``user_id`` it came from, only to export it with ``/me/export`` and erase it
  with the case (a :data:`~asistente.nucleo.decision.state_store_sqlite.AL_BORRAR` hook) or the account (the
  block in ``auth.py`` calls :func:`borrar_de_usuario`);
* when the user deletes a fact the case's snapshot used, the value is redacted here too and the case stops
  running (``vigente`` false): privacy wins over a regression test (the :data:`AL_GUARDAR` hook).

The case itself (:class:`~asistente.nucleo.evaluacion.modelos.CasoEvaluacion`) is what the suite runs; the runner
reads :func:`casos_vigentes` next to the versioned suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, PasoGuion
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

NOMBRE_FICHERO = "evaluacion_casos_reales.sqlite3"
_TABLA = """
    CREATE TABLE IF NOT EXISTS evaluacion_casos_reales (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        case_id TEXT,
        user_id TEXT,
        vigente INTEGER NOT NULL DEFAULT 1,
        payload_json TEXT NOT NULL
    )
"""


class CasoReal(BaseModel):
    """One accepted real case and where it came from (for export and erasure only)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str | None = None
    user_id: str | None = None
    candidato_id: str
    grupo: str
    feedback_id: str | None = None
    caso: CasoEvaluacion
    aceptado_el: datetime
    # "propietario" (the user who gave it) or "operador": never a name.
    aceptado_por: str
    vigente: bool = True
    motivo_baja: str | None = None
    # Warnings the reviewer accepted (e.g. the expectations did not reproduce the captured mistake).
    avisos: list[str] = Field(default_factory=list)
    # The run that actually happened, as a replay script (masked): the case's standing negative control.
    guion_capturado: dict[str, list[PasoGuion]] | None = None


def ruta_por_defecto(db_casos: str | None = None) -> Path:
    """The private file: the setting, or next to the cases' database."""
    configurada = str(getattr(settings, "EVALUACION_CASOS_REALES_DB_PATH", "") or "").strip()
    if configurada:
        return Path(configurada)
    base = db_casos or settings.DECISION_STATE_DB_PATH
    return Path(base).with_name(NOMBRE_FICHERO)


class CasosRealesStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                conn.execute(_TABLA)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evaluacion_casos_reales_case ON evaluacion_casos_reales(case_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evaluacion_casos_reales_user ON evaluacion_casos_reales(user_id)"
                )
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def guardar(self, caso: CasoReal) -> CasoReal:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "INSERT INTO evaluacion_casos_reales (id, case_id, user_id, vigente, payload_json) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET vigente = excluded.vigente, payload_json = excluded.payload_json",
                    (caso.id, caso.case_id, caso.user_id, int(caso.vigente), caso.model_dump_json()),
                )
        return caso

    def get(self, caso_id: str) -> CasoReal | None:
        with self._lock:
            fila = (
                self._get()
                .execute("SELECT payload_json FROM evaluacion_casos_reales WHERE id = ?", (caso_id,))
                .fetchone()
            )
        return CasoReal.model_validate_json(fila["payload_json"]) if fila else None

    def _lista(self, sql: str, params: tuple[Any, ...] = ()) -> list[CasoReal]:
        with self._lock:
            filas = self._get().execute(sql, params).fetchall()
        return [CasoReal.model_validate_json(f["payload_json"]) for f in filas]

    def todos(self) -> list[CasoReal]:
        return self._lista("SELECT payload_json FROM evaluacion_casos_reales ORDER BY seq")

    def vigentes(self) -> list[CasoReal]:
        return self._lista("SELECT payload_json FROM evaluacion_casos_reales WHERE vigente = 1 ORDER BY seq")

    def de_casos(self, case_ids: list[str]) -> list[CasoReal]:
        if not case_ids:
            return []
        marcas = ",".join("?" * len(case_ids))
        return self._lista(
            f"SELECT payload_json FROM evaluacion_casos_reales WHERE case_id IN ({marcas}) ORDER BY seq",  # noqa: S608
            tuple(case_ids),
        )

    def de_usuario(self, user_id: str | None, case_ids: list[str]) -> list[CasoReal]:
        """Rows of a user: by user id and by the user's cases (a row may predate the account link)."""
        vistos: dict[str, CasoReal] = {c.id: c for c in self.de_casos(case_ids)}
        if user_id:
            for c in self._lista(
                "SELECT payload_json FROM evaluacion_casos_reales WHERE user_id = ? ORDER BY seq", (user_id,)
            ):
                vistos.setdefault(c.id, c)
        return list(vistos.values())

    def borrar(self, caso_id: str) -> bool:
        with self._lock:
            conn = self._get()
            with conn:
                cursor = conn.execute("DELETE FROM evaluacion_casos_reales WHERE id = ?", (caso_id,))
        return cursor.rowcount == 1

    def borrar_de(self, *, case_ids: list[str], user_id: str | None = None) -> list[str]:
        """Erase every row of these cases (and of this user). Returns the ids erased."""
        borrados = [c.id for c in self.de_usuario(user_id, case_ids)]
        if not borrados:
            return []
        with self._lock:
            conn = self._get()
            with conn:
                marcas = ",".join("?" * len(borrados))
                conn.execute(
                    f"DELETE FROM evaluacion_casos_reales WHERE id IN ({marcas})", tuple(borrados)
                )  # noqa: S608
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        return borrados


_stores: dict[str, CasosRealesStore] = {}
_stores_lock = RLock()


def store_para(ruta: str | Path) -> CasosRealesStore:
    clave = str(Path(ruta))
    with _stores_lock:
        store = _stores.get(clave)
        if store is None:
            store = _stores[clave] = CasosRealesStore(clave)
        return store


def store_de(db_casos: str | None = None) -> CasosRealesStore:
    return store_para(ruta_por_defecto(db_casos))


def casos_vigentes(ruta: str | Path | None = None) -> list[CasoEvaluacion]:
    """The accepted real cases to run (none when the private file does not exist: nothing is created)."""
    destino = Path(ruta) if ruta is not None else ruta_por_defecto()
    if not destino.exists():
        return []
    return [c.caso for c in store_para(destino).vigentes()]


# --------------------------------------------------------------------------- #
# Erasure and redaction hooks (the cases' store), export
# --------------------------------------------------------------------------- #


def _ruta_de(conn: sqlite3.Connection) -> str:
    fila = conn.execute("PRAGMA database_list").fetchone()
    return str(fila[2] or "") if fila is not None else ""


def al_borrar_caso(conn: sqlite3.Connection, case_id: str) -> None:
    """``delete_case`` hook: the accepted cases of that case go with it. An error aborts the erasure (retry)."""
    ruta_casos = _ruta_de(conn)
    destino = ruta_por_defecto(ruta_casos or None)
    if not destino.exists():
        return
    borrados = store_para(destino).borrar_de(case_ids=[case_id])
    if borrados:
        from asistente.nucleo.evaluacion.revision import reabrir_candidatos_de

        reabrir_candidatos_de(conn, borrados)


def redactar_hechos_borrados(ruta_casos: str | None, case_id: str, hechos_actuales: set[str]) -> int:
    """Accepted cases of ``case_id`` whose snapshot holds a fact the case no longer has: redacted, and retired."""
    from asistente.nucleo.decision.audit import redactar_hecho

    destino = ruta_por_defecto(ruta_casos)
    if not destino.exists():
        return 0
    store = store_para(destino)
    cambiados = 0
    for real in store.de_casos([case_id]):
        expediente = real.caso.expediente
        if expediente is None:
            continue
        sobran = sorted(set(expediente.hechos) - hechos_actuales)
        if not sobran:
            continue
        datos: Any = real.caso.model_dump(mode="json")
        for nombre in sobran:
            datos, _ = redactar_hecho(datos, nombre)
        real = real.model_copy(
            update={
                "caso": CasoEvaluacion.model_validate(datos),
                "vigente": False,
                "motivo_baja": "El usuario borró de su expediente un dato del que dependía: " + ", ".join(sobran) + ".",
            }
        )
        store.guardar(real)
        cambiados += 1
    return cambiados


def exportar(ruta_casos: str | None, *, user_id: str | None, case_ids: list[str]) -> list[dict[str, Any]]:
    destino = ruta_por_defecto(ruta_casos)
    if not destino.exists():
        return []
    return [json.loads(c.model_dump_json()) for c in store_para(destino).de_usuario(user_id, case_ids)]


def borrar_de_usuario(ruta_casos: str | None, *, user_id: str | None, case_ids: list[str]) -> list[str]:
    destino = ruta_por_defecto(ruta_casos)
    if not destino.exists():
        return []
    return store_para(destino).borrar_de(case_ids=case_ids, user_id=user_id)


def ahora() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "NOMBRE_FICHERO",
    "CasoReal",
    "CasosRealesStore",
    "al_borrar_caso",
    "borrar_de_usuario",
    "casos_vigentes",
    "exportar",
    "redactar_hechos_borrados",
    "ruta_por_defecto",
    "store_de",
    "store_para",
]

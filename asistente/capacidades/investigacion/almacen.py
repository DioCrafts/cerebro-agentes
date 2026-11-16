"""What the research capability keeps: each user's web consent and their cached reports. Its own SQLite file.

* ``investigacion_consentimientos`` — append-only history of the user's «use the web for my research» choice
  (the latest one counts), with the version of the wording they saw.
* ``investigacion_informes`` — reports by (user, question, sources, depth, settings, day): asking the same thing
  again the same day costs nothing. Kept :data:`ajustes.Ajustes.cache_dias` days at most.

Privacy: rows are per user (``"local"`` without accounts); :func:`AlmacenInvestigacion.exportar` gives back
everything about a user, :func:`AlmacenInvestigacion.borrar_usuario` erases it, and
:func:`AlmacenInvestigacion.purgar_cuentas_borradas` erases the rows of accounts that no longer exist in the
cases' database (run on every open of the store: an account deleted elsewhere loses its research data even
before anything calls the erase hook).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from asistente.capacidades.investigacion.modelo import Informe, PeticionInvestigacion

logger = logging.getLogger(__name__)

USUARIO_LOCAL = "local"
# Version of the consent wording (frontend InvestigarPanel + docs/capacidades/investigacion.md).
VERSION_TEXTO_CONSENTIMIENTO = "2026-11-investigacion-web-1"

_ESQUEMA = (
    """CREATE TABLE IF NOT EXISTS investigacion_consentimientos (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT NOT NULL,
        concedido INTEGER NOT NULL,
        momento TEXT NOT NULL,
        version_texto TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_investigacion_consentimientos ON investigacion_consentimientos(usuario, seq)",
    """CREATE TABLE IF NOT EXISTS investigacion_informes (
        clave TEXT PRIMARY KEY,
        usuario TEXT NOT NULL,
        dia TEXT NOT NULL,
        creado TEXT NOT NULL,
        informe TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_investigacion_informes_usuario ON investigacion_informes(usuario, creado)",
)


def usuario_de(user_id: str | None) -> str:
    return user_id if user_id else USUARIO_LOCAL


def clave_cache(usuario: str, peticion: PeticionInvestigacion, dia: date, *, configuracion: str) -> str:
    """The cache key: the user, the normalised question, sources, depth, web mode, the settings that change
    the result (``configuracion``) and the day."""
    datos = {
        "usuario": usuario,
        "pregunta": " ".join(peticion.pregunta.lower().split()),
        "ambito": (peticion.ambito or "").strip().lower(),
        "fuentes": list(peticion.fuentes),
        "profundidad": peticion.profundidad,
        "solo_oficiales": peticion.solo_oficiales,
        "configuracion": configuracion,
        "dia": dia.isoformat(),
    }
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class AlmacenInvestigacion:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            for sentencia in _ESQUEMA:
                self._conn.execute(sentencia)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- consent -------------------------------------------------------------

    def registrar_consentimiento(self, user_id: str | None, concedido: bool, *, ahora: datetime | None = None) -> None:
        momento = (ahora or datetime.now(UTC)).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO investigacion_consentimientos (usuario, concedido, momento, version_texto) "
                "VALUES (?, ?, ?, ?)",
                (usuario_de(user_id), int(concedido), momento, VERSION_TEXTO_CONSENTIMIENTO),
            )
            if not concedido:
                # Withdrawing consent also drops the reports that used the web (nothing else depends on them).
                self._conn.execute(
                    "DELETE FROM investigacion_informes WHERE usuario = ? AND informe LIKE '%\"usada\":true%'",
                    (usuario_de(user_id),),
                )

    def consentimiento_web(self, user_id: str | None) -> bool:
        with self._lock:
            fila = self._conn.execute(
                "SELECT concedido FROM investigacion_consentimientos WHERE usuario = ? ORDER BY seq DESC LIMIT 1",
                (usuario_de(user_id),),
            ).fetchone()
        return bool(fila["concedido"]) if fila is not None else False

    def historial_consentimientos(self, user_id: str | None) -> list[dict[str, Any]]:
        with self._lock:
            filas = self._conn.execute(
                "SELECT concedido, momento, version_texto FROM investigacion_consentimientos "
                "WHERE usuario = ? ORDER BY seq",
                (usuario_de(user_id),),
            ).fetchall()
        return [
            {"concedido": bool(f["concedido"]), "momento": f["momento"], "version_texto": f["version_texto"]}
            for f in filas
        ]

    # --- cache ---------------------------------------------------------------

    def informe(self, clave: str) -> Informe | None:
        with self._lock:
            fila = self._conn.execute("SELECT informe FROM investigacion_informes WHERE clave = ?", (clave,)).fetchone()
        if fila is None:
            return None
        try:
            return Informe.model_validate_json(fila["informe"])
        except ValueError:  # a report written by another version of the model: ignore it
            logger.warning("cached research report could not be read; ignored")
            return None

    def guardar(self, clave: str, user_id: str | None, informe: Informe, dia: date) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO investigacion_informes (clave, usuario, dia, creado, informe) "
                "VALUES (?, ?, ?, ?, ?)",
                (clave, usuario_de(user_id), dia.isoformat(), datetime.now(UTC).isoformat(), informe.model_dump_json()),
            )

    def recientes(self, user_id: str | None, limite: int = 20) -> list[Informe]:
        with self._lock:
            filas = self._conn.execute(
                "SELECT informe FROM investigacion_informes WHERE usuario = ? ORDER BY creado DESC LIMIT ?",
                (usuario_de(user_id), limite),
            ).fetchall()
        informes: list[Informe] = []
        for fila in filas:
            try:
                informes.append(Informe.model_validate_json(fila["informe"]))
            except ValueError:
                continue
        return informes

    def purgar(self, dias: int, *, hoy: date) -> int:
        """Delete the reports older than ``dias`` days (0: keep none from before today)."""
        limite = (hoy - timedelta(days=dias)).isoformat()
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM investigacion_informes WHERE dia < ?", (limite,)).rowcount

    # --- export and erasure --------------------------------------------------

    def exportar(self, user_id: str | None) -> dict[str, Any]:
        return {
            "consentimientos_web": self.historial_consentimientos(user_id),
            "informes": [i.model_dump(mode="json") for i in self.recientes(user_id, limite=10_000)],
        }

    def borrar_usuario(self, user_id: str | None) -> int:
        usuario = usuario_de(user_id)
        with self._lock, self._conn:
            borrados = self._conn.execute("DELETE FROM investigacion_informes WHERE usuario = ?", (usuario,)).rowcount
            borrados += self._conn.execute(
                "DELETE FROM investigacion_consentimientos WHERE usuario = ?", (usuario,)
            ).rowcount
        return borrados

    def purgar_cuentas_borradas(self, db_casos: str) -> int:
        """Erase the rows of users that have no account any more in the cases' database (accounts on only)."""
        if not Path(db_casos).exists():
            return 0
        try:
            conn = sqlite3.connect(f"file:{Path(db_casos).resolve()}?mode=ro", uri=True, timeout=30.0)
        except sqlite3.Error:
            return 0
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone() is None:
                return 0
            vivos = {str(f[0]) for f in conn.execute("SELECT id FROM users")}
        except sqlite3.Error:
            logger.warning("could not read the accounts to purge research data", exc_info=True)
            return 0
        finally:
            conn.close()
        with self._lock:
            usuarios = {
                str(f["usuario"])
                for tabla in ("investigacion_informes", "investigacion_consentimientos")
                for f in self._conn.execute(f"SELECT DISTINCT usuario FROM {tabla}")  # noqa: S608 - fixed names
            }
        borrados = 0
        for usuario in usuarios - vivos - {USUARIO_LOCAL}:
            borrados += self.borrar_usuario(usuario)
        return borrados


__all__ = [
    "USUARIO_LOCAL",
    "VERSION_TEXTO_CONSENTIMIENTO",
    "AlmacenInvestigacion",
    "clave_cache",
    "usuario_de",
]

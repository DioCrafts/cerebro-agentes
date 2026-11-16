"""Who receives notices on their devices: Web Push subscriptions and the users' opt-in, per user.

Same SQLite file as the cases and the accounts, so one backup and one erasure cover everything.

* ``push_suscripciones`` — one row per device that switched the notices on: the push service endpoint (an
  unguessable URL; it is a capability, never logged), the browser's ``p256dh`` key and ``auth`` secret
  (needed to encrypt for it), when it was added and the last delivery result. An endpoint belongs to ONE
  owner: when another account subscribes the same browser, the device moves to it.
* ``push_consentimientos`` — append-only history of the user's opt-in / opt-out, with the version of the
  wording they saw (the same evidence pattern as ``user_consents`` of the accounts).

The owner is the account id when ``AUTH_MODE=required`` and :data:`OPERADOR` otherwise (a personal deployment
without accounts: every notice belongs to the person who runs it).

Erasure with the account: two SQLite triggers on ``users`` delete the owner's rows of both tables in the same
transaction as ``AuthStore.delete_user`` (``DELETE /me``), whichever code deletes the user. They are created
as soon as the ``users`` table exists (this store makes sure it does when accounts are on).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from hacienda_gpt import settings

# The owner of everything when there are no accounts.
OPERADOR = "operador"
# Version of the opt-in wording shown in «Avisos en este dispositivo» (frontend + docs/privacidad.md).
VERSION_TEXTO_CONSENTIMIENTO = "2026-11-push-1"
# More devices than this per user is abuse or a bug: the oldest goes.
MAX_DISPOSITIVOS = 10


class Suscripcion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    propietario: str
    endpoint: str
    p256dh: str
    auth: str
    creada: datetime
    ultimo_envio: datetime | None = None
    fallos_seguidos: int = 0

    @property
    def servicio(self) -> str:
        """The push service's host (what the user sees and what is exported; never the full endpoint)."""
        return urlsplit(self.endpoint).hostname or ""


class RegistroConsentimiento(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concedido: bool
    momento: datetime
    version_texto: str


def _ahora() -> datetime:
    return datetime.now(UTC)


class PushStore:
    """SQLite store of the Web Push subscriptions and opt-ins (same database file as the cases)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if settings.AUTH_MODE == "required":
            # The erasure triggers hang off ``users``: make sure the accounts' tables exist first.
            from hacienda_gpt.auth.store import AuthStore

            cuentas = AuthStore(self.db_path)
            try:
                cuentas.owner_of("")
            finally:
                cuentas.close()
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS push_suscripciones (
                    id TEXT PRIMARY KEY,
                    propietario TEXT NOT NULL,
                    endpoint TEXT NOT NULL UNIQUE,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    creada TEXT NOT NULL,
                    ultimo_envio TEXT,
                    fallos_seguidos INTEGER NOT NULL DEFAULT 0
                )
                """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_push_suscripciones_propietario ON push_suscripciones(propietario)"
            )
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS push_consentimientos (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    propietario TEXT NOT NULL,
                    concedido INTEGER NOT NULL,
                    momento TEXT NOT NULL,
                    version_texto TEXT NOT NULL
                )
                """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_push_consentimientos ON push_consentimientos(propietario, seq)"
            )
            self._crear_disparadores()

    def _crear_disparadores(self) -> None:
        existe = self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        if existe is None:
            return
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS push_borrar_suscripciones_con_usuario AFTER DELETE ON users
            BEGIN
                DELETE FROM push_suscripciones WHERE propietario = OLD.id;
                DELETE FROM push_consentimientos WHERE propietario = OLD.id;
            END
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- subscriptions --------------------------------------------------------

    @staticmethod
    def _fila(row: sqlite3.Row) -> Suscripcion:
        return Suscripcion(
            id=row["id"],
            propietario=row["propietario"],
            endpoint=row["endpoint"],
            p256dh=row["p256dh"],
            auth=row["auth"],
            creada=datetime.fromisoformat(row["creada"]),
            ultimo_envio=datetime.fromisoformat(row["ultimo_envio"]) if row["ultimo_envio"] else None,
            fallos_seguidos=int(row["fallos_seguidos"]),
        )

    def suscribir(self, propietario: str, endpoint: str, p256dh: str, auth: str) -> Suscripcion:
        """Add (or refresh) this device for ``propietario``. A device another owner had moves to this one."""
        ahora = _ahora()
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM push_suscripciones WHERE endpoint = ?", (endpoint,))
            nuevo = uuid4().hex
            self._conn.execute(
                "INSERT INTO push_suscripciones (id, propietario, endpoint, p256dh, auth, creada) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (nuevo, propietario, endpoint, p256dh, auth, ahora.isoformat()),
            )
            sobrantes = self._conn.execute(
                "SELECT id FROM push_suscripciones WHERE propietario = ? ORDER BY creada DESC, rowid DESC LIMIT -1 "
                "OFFSET ?",
                (propietario, MAX_DISPOSITIVOS),
            ).fetchall()
            for fila in sobrantes:
                self._conn.execute("DELETE FROM push_suscripciones WHERE id = ?", (fila["id"],))
            row = self._conn.execute("SELECT * FROM push_suscripciones WHERE id = ?", (nuevo,)).fetchone()
        return self._fila(row)

    def de(self, propietario: str) -> list[Suscripcion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM push_suscripciones WHERE propietario = ? ORDER BY creada, rowid", (propietario,)
            ).fetchall()
        return [self._fila(row) for row in rows]

    def baja(self, propietario: str, endpoint: str) -> bool:
        """Remove one device of ``propietario`` (another owner's endpoint is left alone)."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM push_suscripciones WHERE propietario = ? AND endpoint = ?", (propietario, endpoint)
            )
            return cursor.rowcount == 1

    def eliminar(self, suscripcion_id: str) -> None:
        """Forget a device the push service says is gone (404/410)."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM push_suscripciones WHERE id = ?", (suscripcion_id,))

    def borrar_de(self, propietario: str) -> int:
        """Every device of ``propietario`` (withdrawn consent). Returns how many."""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM push_suscripciones WHERE propietario = ?", (propietario,))
            return cursor.rowcount

    def marcar_envio(self, suscripcion_id: str, *, ok: bool) -> None:
        with self._lock, self._conn:
            if ok:
                self._conn.execute(
                    "UPDATE push_suscripciones SET ultimo_envio = ?, fallos_seguidos = 0 WHERE id = ?",
                    (_ahora().isoformat(), suscripcion_id),
                )
            else:
                self._conn.execute(
                    "UPDATE push_suscripciones SET fallos_seguidos = fallos_seguidos + 1 WHERE id = ?",
                    (suscripcion_id,),
                )

    # --- consent --------------------------------------------------------------

    def registrar_consentimiento(self, propietario: str, concedido: bool) -> RegistroConsentimiento:
        """Append a decision (only when it changes, or the wording changed). Records are never updated."""
        actual = self.consentimiento(propietario)
        if (
            actual is not None
            and actual.concedido == concedido
            and actual.version_texto == VERSION_TEXTO_CONSENTIMIENTO
        ):
            return actual
        registro = RegistroConsentimiento(
            concedido=concedido, momento=_ahora(), version_texto=VERSION_TEXTO_CONSENTIMIENTO
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO push_consentimientos (propietario, concedido, momento, version_texto) VALUES (?, ?, ?, ?)",
                (propietario, int(concedido), registro.momento.isoformat(), registro.version_texto),
            )
        return registro

    def historial_consentimiento(self, propietario: str) -> list[RegistroConsentimiento]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT concedido, momento, version_texto FROM push_consentimientos WHERE propietario = ? ORDER BY seq",
                (propietario,),
            ).fetchall()
        return [
            RegistroConsentimiento(
                concedido=bool(row["concedido"]),
                momento=datetime.fromisoformat(row["momento"]),
                version_texto=row["version_texto"],
            )
            for row in rows
        ]

    def consentimiento(self, propietario: str) -> RegistroConsentimiento | None:
        """The latest decision (None: never asked)."""
        historial = self.historial_consentimiento(propietario)
        return historial[-1] if historial else None

    def tiene_consentimiento(self, propietario: str) -> bool:
        registro = self.consentimiento(propietario)
        return registro is not None and registro.concedido

    def exportar(self, propietario: str) -> dict[str, object]:
        """What is stored about the owner, for the right of access (no keys: they are the device's)."""
        return {
            "dispositivos": [
                {
                    "id": s.id,
                    "servicio": s.servicio,
                    "creada": s.creada.isoformat(),
                    "ultimo_envio": s.ultimo_envio.isoformat() if s.ultimo_envio else None,
                }
                for s in self.de(propietario)
            ],
            "consentimientos": [r.model_dump(mode="json") for r in self.historial_consentimiento(propietario)],
        }

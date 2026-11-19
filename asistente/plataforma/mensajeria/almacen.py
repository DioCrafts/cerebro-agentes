"""The messaging gateway's tables, in the cases' database (one backup, one erasure).

What is stored, and why it is the minimum (``asistente/plataforma/migraciones/catalogo/mensajeria.py`` has the DDL):

* pairing and confirmation codes are stored as SHA-256 hashes only (``huella``): the database never holds a code
  that would pair a chat or confirm an action;
* a binding (``Vinculo``) is the channel, the adapter's opaque contact id, the owner and the server conversation it
  talks in — no message text: the turns themselves are audited and remembered exactly as ``/agent`` does it;
* processed message ids and attempts are hashes too, and are purged after ``MENSAJERIA_RETENCION_DIAS``;
* the chat's recent history lives in the gateway's memory (RAM) only, never here.

Every multi-step change (redeem a code and create the binding, redeem a confirmation code, claim a delivery) is ONE
``BEGIN IMMEDIATE`` transaction with a conditional ``UPDATE``/``INSERT``, so two messages racing (a code typed twice,
two processes) change the row once. Isolation: reads for the app take the owner (``user_id``); the gateway reads a
binding by its (channel, contact), which only the channel's adapter supplies. Erasure: the catalogue's triggers on
``case_states`` and ``users`` (also run here when the migrations are off) and :func:`borrar_mensajeria_de_usuario`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import sqlite3
import threading
from typing import Any, Literal
from uuid import uuid4

from asistente.plataforma.mensajeria.contrato import EstadoCanal
from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.migraciones.catalogo import mensajeria as esquema

TipoIntento = Literal["codigo_fallido", "respuesta_desconocido"]
TipoConfirmacion = Literal["accion", "consejo", "desemparejar"]
EstadoEntrega = Literal["enviando", "entregada", "omitida", "fallida"]
MotivoDesconexion = Literal["app", "chat", "borrado", "sustituido"]
ResultadoEmparejar = Literal["ok", "invalido", "limite", "ya_vinculado"]
_MAX_NOMBRE = 80


def ahora() -> datetime:
    return datetime.now(UTC)


def _ts(momento: datetime) -> str:
    # Fixed-width UTC text, so comparing strings compares instants.
    return momento.astimezone(UTC).isoformat(timespec="microseconds")


def _fecha(texto: str | None) -> datetime | None:
    if not texto:
        return None
    valor = datetime.fromisoformat(texto)
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=UTC)


def huella(*partes: str) -> str:
    """A stable hash of some identifiers (never reversible to a code: codes have enough entropy, or are bound)."""
    return hashlib.sha256("\x00".join(partes).encode("utf-8")).hexdigest()[:40]


def nuevo_conversacion_id() -> str:
    """A server conversation id for a chat (the pattern ``TurnoConversacion`` requires)."""
    return f"msj_{uuid4().hex}"


@dataclass(frozen=True)
class Vinculo:
    """A paired chat: who it speaks for and in which conversation."""

    id: str
    canal: str
    contacto: str
    user_id: str | None
    case_id: str
    conversacion_id: str
    nombre_mostrado: str | None
    creado: datetime
    ultimo_uso: datetime | None
    pausado: bool


@dataclass(frozen=True)
class Emparejamiento:
    resultado: ResultadoEmparejar
    vinculo: Vinculo | None = None


@dataclass(frozen=True)
class Desconexion:
    seq: int
    canal: str
    contacto: str
    motivo: str
    intentos: int


def _vinculo(fila: sqlite3.Row) -> Vinculo:
    creado = _fecha(fila["creado"])
    assert creado is not None
    return Vinculo(
        id=fila["id"],
        canal=fila["canal"],
        contacto=fila["contacto"],
        user_id=fila["user_id"] or None,
        case_id=fila["case_id"],
        conversacion_id=fila["conversacion_id"],
        nombre_mostrado=fila["nombre_mostrado"],
        creado=creado,
        ultimo_uso=_fecha(fila["ultimo_uso"]),
        pausado=bool(fila["pausado"]),
    )


def tabla_existe(db_path: str) -> bool:
    """Whether ``db_path`` exists and has the bindings table (read-only check: never creates the file)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        fila = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'mensajeria_vinculos'"
        ).fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


class AlmacenMensajeria:
    """SQLite store of the gateway. Thread-safe; one connection per instance (the API, the worker and the CLI each
    open their own). Build it with :func:`almacen`."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- connection -----------------------------------------------------------------------------------------

    def _abrir(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        # Erased bindings and codes are overwritten in the file, not just unlinked.
        conn.execute("PRAGMA secure_delete=ON;")
        conn.execute("BEGIN")
        try:
            asegurar_esquema(conn, "mensajeria")  # migrations (asistente/plataforma); a no-op when they are off
            for sentencia in esquema.TABLAS_E_INDICES:
                conn.execute(sentencia)
            tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "case_states" in tablas:
                conn.execute(esquema.DISPARADOR_CASO)
            if "users" in tablas:
                conn.execute(esquema.DISPARADOR_USUARIO)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            conn.close()
            raise
        self._conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def _transaccion(self, *, inmediata: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._abrir()
            conn.execute("BEGIN IMMEDIATE" if inmediata else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def _leer(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._abrir().execute(sql, tuple(params)).fetchall())

    # -- pairing codes --------------------------------------------------------------------------------------

    def crear_codigo(
        self, *, huella_codigo: str, canal: str, user_id: str | None, case_id: str, caduca: datetime, momento: datetime
    ) -> str:
        """Store a new pairing code (its hash). The owner's earlier unused codes on that channel stop working: only
        the last code shown in the app pairs."""
        codigo_id = f"cod_{uuid4().hex}"
        with self._transaccion() as conn:
            conn.execute(
                "UPDATE mensajeria_codigos SET usado = ? WHERE user_id = ? AND canal = ? AND usado IS NULL",
                (_ts(momento), user_id or "", canal),
            )
            conn.execute(
                "INSERT INTO mensajeria_codigos (id, huella, canal, user_id, case_id, creado, caduca) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (codigo_id, huella_codigo, canal, user_id or "", case_id, _ts(momento), _ts(caduca)),
            )
        return codigo_id

    def codigos_desde(self, user_id: str | None, desde: datetime) -> int:
        """Codes the owner generated since ``desde`` (the app's rate limit)."""
        filas = self._leer(
            "SELECT COUNT(*) AS n FROM mensajeria_codigos WHERE user_id = ? AND creado >= ?",
            (user_id or "", _ts(desde)),
        )
        return int(filas[0]["n"])

    def codigo_vigente(self, user_id: str | None, canal: str, momento: datetime) -> datetime | None:
        """When the owner's live (unused, unexpired) code on ``canal`` expires, or None."""
        filas = self._leer(
            "SELECT caduca FROM mensajeria_codigos WHERE user_id = ? AND canal = ? AND usado IS NULL AND caduca > ? "
            "ORDER BY creado DESC LIMIT 1",
            (user_id or "", canal, _ts(momento)),
        )
        return _fecha(filas[0]["caduca"]) if filas else None

    def anular_codigos(self, user_id: str | None, canal: str, momento: datetime) -> int:
        with self._transaccion() as conn:
            return conn.execute(
                "UPDATE mensajeria_codigos SET usado = ? WHERE user_id = ? AND canal = ? AND usado IS NULL",
                (_ts(momento), user_id or "", canal),
            ).rowcount

    def emparejar(
        self,
        *,
        huella_codigo: str,
        canal: str,
        contacto: str,
        nombre_mostrado: str | None,
        maximo_por_usuario: int,
        momento: datetime,
    ) -> Emparejamiento:
        """Redeem a pairing code from ``contacto`` and create its binding, atomically.

        ``invalido``: no live code with that hash on that channel (unknown, expired or used). ``limite``: the owner
        already has ``maximo_por_usuario`` chats on that channel (the code is NOT spent, so they can unpair one in the
        app and try again). ``ya_vinculado``: the contact is already paired (to anyone)."""
        with self._transaccion() as conn:
            if conn.execute(
                "SELECT 1 FROM mensajeria_vinculos WHERE canal = ? AND contacto = ?", (canal, contacto)
            ).fetchone():
                return Emparejamiento("ya_vinculado")
            fila = conn.execute(
                "SELECT * FROM mensajeria_codigos WHERE huella = ? AND canal = ? AND usado IS NULL AND caduca > ?",
                (huella_codigo, canal, _ts(momento)),
            ).fetchone()
            if fila is None:
                return Emparejamiento("invalido")
            ya = conn.execute(
                "SELECT COUNT(*) FROM mensajeria_vinculos WHERE user_id = ? AND canal = ?", (fila["user_id"], canal)
            ).fetchone()[0]
            if int(ya) >= max(1, maximo_por_usuario):
                return Emparejamiento("limite")
            usado = conn.execute(
                "UPDATE mensajeria_codigos SET usado = ? WHERE id = ? AND usado IS NULL", (_ts(momento), fila["id"])
            )
            if usado.rowcount != 1:  # pragma: no cover - the write lock makes this impossible
                return Emparejamiento("invalido")
            vinculo_id = f"vin_{uuid4().hex}"
            nombre = " ".join((nombre_mostrado or "").split())[:_MAX_NOMBRE] or None
            conn.execute(
                "INSERT INTO mensajeria_vinculos (id, canal, contacto, user_id, case_id, conversacion_id, "
                "nombre_mostrado, creado, ultimo_uso, pausado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    vinculo_id,
                    canal,
                    contacto,
                    fila["user_id"],
                    fila["case_id"],
                    nuevo_conversacion_id(),
                    nombre,
                    _ts(momento),
                    _ts(momento),
                ),
            )
            creado = conn.execute("SELECT * FROM mensajeria_vinculos WHERE id = ?", (vinculo_id,)).fetchone()
        return Emparejamiento("ok", _vinculo(creado))

    # -- bindings ---------------------------------------------------------------------------------------------

    def vinculo_de(self, canal: str, contacto: str) -> Vinculo | None:
        filas = self._leer("SELECT * FROM mensajeria_vinculos WHERE canal = ? AND contacto = ?", (canal, contacto))
        return _vinculo(filas[0]) if filas else None

    def vinculo(self, vinculo_id: str, *, user_id: str | None = None, de_cualquiera: bool = False) -> Vinculo | None:
        """One binding; only the owner's unless ``de_cualquiera`` (the gateway's own reads)."""
        if de_cualquiera:
            filas = self._leer("SELECT * FROM mensajeria_vinculos WHERE id = ?", (vinculo_id,))
        else:
            filas = self._leer(
                "SELECT * FROM mensajeria_vinculos WHERE id = ? AND user_id = ?", (vinculo_id, user_id or "")
            )
        return _vinculo(filas[0]) if filas else None

    def vinculos_de_usuario(self, user_id: str | None) -> list[Vinculo]:
        filas = self._leer("SELECT * FROM mensajeria_vinculos WHERE user_id = ? ORDER BY creado DESC", (user_id or "",))
        return [_vinculo(f) for f in filas]

    def vinculos(self, *, canal: str | None = None) -> list[Vinculo]:
        """Every binding (the notices' poll), oldest first."""
        if canal is None:
            filas = self._leer("SELECT * FROM mensajeria_vinculos ORDER BY creado")
        else:
            filas = self._leer("SELECT * FROM mensajeria_vinculos WHERE canal = ? ORDER BY creado", (canal,))
        return [_vinculo(f) for f in filas]

    def tocar(self, vinculo_id: str, momento: datetime) -> None:
        with self._transaccion() as conn:
            conn.execute("UPDATE mensajeria_vinculos SET ultimo_uso = ? WHERE id = ?", (_ts(momento), vinculo_id))

    def nueva_conversacion(self, vinculo_id: str) -> str:
        nuevo = nuevo_conversacion_id()
        with self._transaccion() as conn:
            conn.execute("UPDATE mensajeria_vinculos SET conversacion_id = ? WHERE id = ?", (nuevo, vinculo_id))
        return nuevo

    def pausar(self, vinculo_id: str, pausado: bool) -> bool:
        with self._transaccion() as conn:
            return (
                conn.execute(
                    "UPDATE mensajeria_vinculos SET pausado = ? WHERE id = ?", (1 if pausado else 0, vinculo_id)
                ).rowcount
                == 1
            )

    def desvincular(
        self, vinculo_id: str, *, motivo: MotivoDesconexion, encolar: bool, momento: datetime
    ) -> Vinculo | None:
        """Remove a binding with its confirmations and delivery records; ``encolar`` queues the contact for the
        worker to remove on the channel (when the caller cannot reach the channel itself: the API process)."""
        with self._transaccion() as conn:
            fila = conn.execute("SELECT * FROM mensajeria_vinculos WHERE id = ?", (vinculo_id,)).fetchone()
            if fila is None:
                return None
            if encolar:
                conn.execute(
                    "INSERT INTO mensajeria_desconexiones (canal, contacto, motivo, solicitado) VALUES (?, ?, ?, ?)",
                    (fila["canal"], fila["contacto"], motivo, _ts(momento)),
                )
            conn.execute("DELETE FROM mensajeria_entregas WHERE vinculo_id = ?", (vinculo_id,))
            conn.execute("DELETE FROM mensajeria_confirmaciones WHERE vinculo_id = ?", (vinculo_id,))
            conn.execute("DELETE FROM mensajeria_vinculos WHERE id = ?", (vinculo_id,))
        return _vinculo(fila)

    # -- deduplication and attempts ----------------------------------------------------------------------------

    def marcar_procesado(self, huella_mensaje: str, momento: datetime) -> bool:
        """True the first time a message is seen; False for a redelivery (it must not run again)."""
        with self._transaccion() as conn:
            return (
                conn.execute(
                    "INSERT OR IGNORE INTO mensajeria_procesados (huella, recibido) VALUES (?, ?)",
                    (huella_mensaje, _ts(momento)),
                ).rowcount
                == 1
            )

    def anotar_intento(self, tipo: TipoIntento, huella_contacto: str, momento: datetime) -> None:
        with self._transaccion() as conn:
            conn.execute(
                "INSERT INTO mensajeria_intentos (tipo, huella, momento) VALUES (?, ?, ?)",
                (tipo, huella_contacto, _ts(momento)),
            )

    def contar_intentos(self, tipo: TipoIntento, desde: datetime, *, huella_contacto: str | None = None) -> int:
        if huella_contacto is None:
            filas = self._leer(
                "SELECT COUNT(*) AS n FROM mensajeria_intentos WHERE tipo = ? AND momento >= ?", (tipo, _ts(desde))
            )
        else:
            filas = self._leer(
                "SELECT COUNT(*) AS n FROM mensajeria_intentos WHERE tipo = ? AND huella = ? AND momento >= ?",
                (tipo, huella_contacto, _ts(desde)),
            )
        return int(filas[0]["n"])

    # -- confirmation codes ------------------------------------------------------------------------------------

    def crear_confirmacion(
        self,
        *,
        vinculo_id: str,
        tipo: TipoConfirmacion,
        objeto_id: str,
        huella_codigo: str,
        caduca: datetime,
        momento: datetime,
    ) -> bool:
        """Store a confirmation code (its hash) for one item; an earlier live code for the same item stops working.
        False (nothing stored) when a live code of this chat already has that hash: the caller draws another."""
        with self._transaccion() as conn:
            if conn.execute(
                "SELECT 1 FROM mensajeria_confirmaciones WHERE vinculo_id = ? AND huella = ? AND usado IS NULL "
                "AND caduca > ?",
                (vinculo_id, huella_codigo, _ts(momento)),
            ).fetchone():
                return False
            conn.execute(
                "UPDATE mensajeria_confirmaciones SET usado = ? WHERE vinculo_id = ? AND tipo = ? AND objeto_id = ? "
                "AND usado IS NULL",
                (_ts(momento), vinculo_id, tipo, objeto_id),
            )
            conn.execute(
                "INSERT INTO mensajeria_confirmaciones (id, vinculo_id, tipo, objeto_id, huella, creado, caduca) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"cnf_{uuid4().hex}", vinculo_id, tipo, objeto_id, huella_codigo, _ts(momento), _ts(caduca)),
            )
        return True

    def canjear_confirmacion(
        self, *, vinculo_id: str, huella_codigo: str, tipos: Sequence[TipoConfirmacion], momento: datetime
    ) -> tuple[TipoConfirmacion, str] | None:
        """Spend a live code of this chat for one of ``tipos``: (tipo, objeto_id), or None (unknown, of another
        chat, expired, already used, or of another kind). Single use: one conditional UPDATE."""
        marcas = ",".join("?" * len(tipos))
        with self._transaccion() as conn:
            fila = conn.execute(
                f"SELECT id, tipo, objeto_id FROM mensajeria_confirmaciones WHERE vinculo_id = ? AND huella = ? "
                f"AND usado IS NULL AND caduca > ? AND tipo IN ({marcas}) ORDER BY creado DESC LIMIT 1",
                (vinculo_id, huella_codigo, _ts(momento), *tipos),
            ).fetchone()
            if fila is None:
                return None
            gastado = conn.execute(
                "UPDATE mensajeria_confirmaciones SET usado = ? WHERE id = ? AND usado IS NULL",
                (_ts(momento), fila["id"]),
            )
            if gastado.rowcount != 1:  # pragma: no cover - the write lock makes this impossible
                return None
        tipo: TipoConfirmacion = fila["tipo"]
        return tipo, str(fila["objeto_id"])

    # -- notices delivered ---------------------------------------------------------------------------------------

    def entregas_de(self, vinculo_id: str) -> set[str]:
        filas = self._leer("SELECT notification_id FROM mensajeria_entregas WHERE vinculo_id = ?", (vinculo_id,))
        return {f["notification_id"] for f in filas}

    def reclamar_entrega(self, vinculo_id: str, notification_id: str, momento: datetime, *, max_intentos: int) -> bool:
        """Take a notice for delivery to a chat: once (a delivered or skipped one is never sent again); a failed
        one is retried until ``max_intentos``."""
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT estado, intentos FROM mensajeria_entregas WHERE vinculo_id = ? AND notification_id = ?",
                (vinculo_id, notification_id),
            ).fetchone()
            if fila is None:
                conn.execute(
                    "INSERT INTO mensajeria_entregas (vinculo_id, notification_id, estado, intentos, momento) "
                    "VALUES (?, ?, 'enviando', 1, ?)",
                    (vinculo_id, notification_id, _ts(momento)),
                )
                return True
            if fila["estado"] != "fallida" or int(fila["intentos"]) >= max_intentos:
                return False
            conn.execute(
                "UPDATE mensajeria_entregas SET estado = 'enviando', intentos = intentos + 1, momento = ? "
                "WHERE vinculo_id = ? AND notification_id = ? AND estado = 'fallida'",
                (_ts(momento), vinculo_id, notification_id),
            )
            return True

    def terminar_entrega(self, vinculo_id: str, notification_id: str, estado: EstadoEntrega, momento: datetime) -> None:
        with self._transaccion() as conn:
            conn.execute(
                "INSERT INTO mensajeria_entregas (vinculo_id, notification_id, estado, intentos, momento) "
                "VALUES (?, ?, ?, 0, ?) ON CONFLICT(vinculo_id, notification_id) DO UPDATE SET estado = excluded.estado, "
                "momento = excluded.momento",
                (vinculo_id, notification_id, estado, _ts(momento)),
            )

    # -- disconnections ------------------------------------------------------------------------------------------

    def encolar_desconexion(self, canal: str, contacto: str, motivo: MotivoDesconexion, momento: datetime) -> None:
        with self._transaccion() as conn:
            conn.execute(
                "INSERT INTO mensajeria_desconexiones (canal, contacto, motivo, solicitado) VALUES (?, ?, ?, ?)",
                (canal, contacto, motivo, _ts(momento)),
            )

    def desconexiones(self, *, canal: str | None = None, limite: int = 20) -> list[Desconexion]:
        sql = "SELECT * FROM mensajeria_desconexiones"
        params: list[Any] = []
        if canal is not None:
            sql += " WHERE canal = ?"
            params.append(canal)
        filas = self._leer(sql + " ORDER BY seq LIMIT ?", (*params, max(1, limite)))
        return [Desconexion(int(f["seq"]), f["canal"], f["contacto"], f["motivo"], int(f["intentos"])) for f in filas]

    def terminar_desconexion(self, seq: int, *, hecha: bool, max_intentos: int) -> None:
        """Done (or given up after ``max_intentos``): the row goes. Otherwise one more attempt is counted."""
        with self._transaccion() as conn:
            if hecha:
                conn.execute("DELETE FROM mensajeria_desconexiones WHERE seq = ?", (seq,))
                return
            conn.execute("UPDATE mensajeria_desconexiones SET intentos = intentos + 1 WHERE seq = ?", (seq,))
            conn.execute(
                "DELETE FROM mensajeria_desconexiones WHERE seq = ? AND intentos >= ?", (seq, max(1, max_intentos))
            )

    # -- channel state ---------------------------------------------------------------------------------------------

    def publicar_estado(self, estado: EstadoCanal, momento: datetime) -> None:
        with self._transaccion() as conn:
            conn.execute(
                "INSERT INTO mensajeria_canales (canal, estado_json, actualizado) VALUES (?, ?, ?) "
                "ON CONFLICT(canal) DO UPDATE SET estado_json = excluded.estado_json, actualizado = excluded.actualizado",
                (estado.canal, estado.model_dump_json(), _ts(momento)),
            )

    def estados(self) -> list[tuple[EstadoCanal, datetime]]:
        salida: list[tuple[EstadoCanal, datetime]] = []
        for fila in self._leer("SELECT * FROM mensajeria_canales ORDER BY canal"):
            try:
                estado = EstadoCanal.model_validate_json(fila["estado_json"])
            except ValueError:
                continue
            actualizado = _fecha(fila["actualizado"])
            assert actualizado is not None
            salida.append((estado, actualizado))
        return salida

    # -- housekeeping ------------------------------------------------------------------------------------------------

    def purgar(self, momento: datetime, *, dias: int) -> dict[str, int]:
        """Drop processed ids and attempts older than ``dias``, spent or expired codes older than a day, and delivery
        records older than 90 days (by then their notices are long past the age a notice is still sent at)."""
        limite = _ts(momento - timedelta(days=max(1, dias)))
        dia = _ts(momento - timedelta(days=1))
        trimestre = _ts(momento - timedelta(days=90))
        with self._transaccion() as conn:
            return {
                "procesados": conn.execute("DELETE FROM mensajeria_procesados WHERE recibido < ?", (limite,)).rowcount,
                "intentos": conn.execute("DELETE FROM mensajeria_intentos WHERE momento < ?", (limite,)).rowcount,
                "codigos": conn.execute(
                    "DELETE FROM mensajeria_codigos WHERE (usado IS NOT NULL AND usado < ?) OR caduca < ?", (dia, dia)
                ).rowcount,
                "confirmaciones": conn.execute(
                    "DELETE FROM mensajeria_confirmaciones WHERE (usado IS NOT NULL AND usado < ?) OR caduca < ?",
                    (dia, dia),
                ).rowcount,
                "entregas": conn.execute("DELETE FROM mensajeria_entregas WHERE momento < ?", (trimestre,)).rowcount,
            }

    # -- export and erasure -----------------------------------------------------------------------------------------

    def exportar(self, user_id: str | None) -> dict[str, Any]:
        """The owner's paired chats and the notices delivered to them. Never a code, never a hash."""
        vinculos = self.vinculos_de_usuario(user_id)
        chats: list[dict[str, Any]] = []
        for v in vinculos:
            entregas = self._leer(
                "SELECT notification_id, estado, momento FROM mensajeria_entregas WHERE vinculo_id = ? "
                "ORDER BY momento",
                (v.id,),
            )
            chats.append(
                {
                    "id": v.id,
                    "canal": v.canal,
                    "contacto": v.contacto,
                    "nombre_mostrado": v.nombre_mostrado,
                    "case_id": v.case_id,
                    "conversacion_id": v.conversacion_id,
                    "creado": v.creado.isoformat(),
                    "ultimo_uso": v.ultimo_uso.isoformat() if v.ultimo_uso else None,
                    "avisos_en_pausa": v.pausado,
                    "avisos_entregados": [dict(f) for f in entregas],
                }
            )
        return {"chats_vinculados": chats}

    def borrar_de_usuario(self, user_id: str, case_ids: Sequence[str], momento: datetime) -> int:
        """Erase the account's bindings (and those of its cases), their confirmations, deliveries and codes; queue the
        contacts for disconnection. Returns the bindings removed."""
        with self._transaccion() as conn:
            marcas = ",".join("?" * len(case_ids)) or "''"
            filas = conn.execute(
                f"SELECT id, canal, contacto FROM mensajeria_vinculos WHERE user_id = ? OR case_id IN ({marcas})",
                (user_id, *case_ids),
            ).fetchall()
            for fila in filas:
                conn.execute(
                    "INSERT INTO mensajeria_desconexiones (canal, contacto, motivo, solicitado) VALUES (?, ?, ?, ?)",
                    (fila["canal"], fila["contacto"], "borrado", _ts(momento)),
                )
                conn.execute("DELETE FROM mensajeria_entregas WHERE vinculo_id = ?", (fila["id"],))
                conn.execute("DELETE FROM mensajeria_confirmaciones WHERE vinculo_id = ?", (fila["id"],))
                conn.execute("DELETE FROM mensajeria_vinculos WHERE id = ?", (fila["id"],))
            conn.execute(
                f"DELETE FROM mensajeria_codigos WHERE user_id = ? OR case_id IN ({marcas})", (user_id, *case_ids)
            )
        with self._lock:
            try:
                self._abrir().execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.Error:  # pragma: no cover - a busy checkpoint is retried by SQLite later
                pass
        return len(filas)


_almacenes: dict[str, AlmacenMensajeria] = {}
_almacenes_lock = threading.Lock()


def almacen(db_path: str) -> AlmacenMensajeria:
    """The process-wide store of a database."""
    clave = str(Path(db_path))
    with _almacenes_lock:
        store = _almacenes.get(clave)
        if store is None:
            store = _almacenes[clave] = AlmacenMensajeria(clave)
        return store


def olvidar_almacenes() -> None:
    """Close and forget every store (tests)."""
    with _almacenes_lock:
        for store in _almacenes.values():
            store.close()
        _almacenes.clear()


def exportar_mensajeria(db_path: str, *, user_id: str) -> dict[str, Any]:
    """For ``GET /me/export``: the paired chats and their deliveries (empty when the gateway was never used)."""
    if not tabla_existe(db_path):
        return {"chats_vinculados": []}
    store = AlmacenMensajeria(db_path)
    try:
        return store.exportar(user_id)
    finally:
        store.close()


def borrar_mensajeria_de_usuario(db_path: str, *, user_id: str, case_ids: Sequence[str]) -> int:
    """For ``DELETE /me``: the triggers erase it too; this also queues the disconnections and checkpoints the WAL."""
    if not tabla_existe(db_path):
        return 0
    store = AlmacenMensajeria(db_path)
    try:
        return store.borrar_de_usuario(user_id, list(case_ids), ahora())
    finally:
        store.close()


__all__ = [
    "AlmacenMensajeria",
    "Desconexion",
    "Emparejamiento",
    "EstadoEntrega",
    "MotivoDesconexion",
    "ResultadoEmparejar",
    "TipoConfirmacion",
    "TipoIntento",
    "Vinculo",
    "ahora",
    "almacen",
    "borrar_mensajeria_de_usuario",
    "exportar_mensajeria",
    "huella",
    "nuevo_conversacion_id",
    "olvidar_almacenes",
    "tabla_existe",
]

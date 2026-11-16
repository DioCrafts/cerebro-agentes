"""What the module stores, in the cases' SQLite file (one backup, one erasure): never a message body.

* ``correo_conexiones`` — one Gmail connection per case: owner, account address, granted scopes, the
  ENCRYPTED refresh token (``cifrado.py``), the last ``historyId`` and sync status. No access token is
  stored (they live in memory for their hour).
* ``correo_oauth_estados`` — authorisation requests in flight: SHA-256 of the ``state``, the PKCE verifier
  (encrypted), owner and creation time; single use, 10 minutes.
* ``correo_elementos`` — derived items: message and thread ids, date, sender DOMAIN (the full address only
  for contacts the user pinned), category, phishing reasons (our wording), extracted fields with their
  literal snippet (≤ 200 characters), deadline, organisation; the subject cut to 120 characters only for
  the categories the user acts on (not promotions nor «otro»).
* ``correo_propuestas`` — data proposed to other modules, waiting for the user's confirmation.
* ``correo_contactos`` — addresses the user pinned (kept in full on their items).

Every row is erased with its case (``al_borrar_caso`` on ``SQLiteCaseStateStore.delete_case``, in the same
transaction) and exported with :func:`exportar` (without the token).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt import settings

logger = logging.getLogger(__name__)

EstadoPropuesta = Literal["pendiente", "aplicando", "confirmada", "rechazada", "fallida"]
TipoPropuesta = Literal["herramienta", "hechos", "nota"]
VIDA_ESTADO_OAUTH = timedelta(minutes=10)
MAX_ASUNTO = 120

_TABLAS = ("correo_conexiones", "correo_oauth_estados", "correo_elementos", "correo_propuestas", "correo_contactos")
_ESQUEMA = """
CREATE TABLE IF NOT EXISTS correo_conexiones (
    case_id TEXT PRIMARY KEY,
    user_id TEXT,
    cuenta TEXT,
    alcance TEXT NOT NULL,
    token_cifrado TEXT NOT NULL,
    conectada_el TEXT NOT NULL,
    history_id TEXT,
    ultima_sincronizacion TEXT,
    ultimo_error TEXT,
    estado TEXT NOT NULL DEFAULT 'conectada'
);
CREATE TABLE IF NOT EXISTS correo_oauth_estados (
    estado_hash TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    user_id TEXT,
    verificador_cifrado TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correo_elementos (
    case_id TEXT NOT NULL,
    id TEXT NOT NULL,
    fecha TEXT NOT NULL,
    categoria TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (case_id, id)
);
CREATE INDEX IF NOT EXISTS idx_correo_elementos_fecha ON correo_elementos(case_id, fecha);
CREATE TABLE IF NOT EXISTS correo_propuestas (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL,
    elemento_id TEXT NOT NULL,
    estado TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (case_id, elemento_id)
);
CREATE TABLE IF NOT EXISTS correo_contactos (
    case_id TEXT NOT NULL,
    direccion TEXT NOT NULL,
    PRIMARY KEY (case_id, direccion)
);
"""


def ruta_db() -> str:
    """The cases' SQLite file (read at call time, so tests and the API agree)."""
    return str(settings.DECISION_STATE_DB_PATH)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampoGuardado(_M):
    campo: str = Field(max_length=40)
    valor: str = Field(max_length=200)
    literal: str = Field(max_length=200)
    fragmento: str = Field(max_length=200)


class Elemento(_M):
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    thread_id: str = Field(max_length=64)
    fecha: date
    dominio: str = Field(max_length=253)
    categoria: str
    categoria_aparente: str
    sospechoso: bool = False
    motivos: list[str] = Field(default_factory=list, max_length=20)
    asunto: str | None = Field(default=None, max_length=MAX_ASUNTO)
    campos: list[CampoGuardado] = Field(default_factory=list, max_length=12)
    plazo: date | None = None
    requiere_accion: bool = False
    organismo: str | None = Field(default=None, max_length=120)
    organismo_verificado: bool = False
    area: str = ""
    comercio: str | None = Field(default=None, max_length=60)
    # Full address only when the user pinned this contact.
    remitente: str | None = Field(default=None, max_length=320)
    origen: Literal["reglas", "llm"] = "reglas"
    sincronizado_el: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def campo(self, nombre: str) -> CampoGuardado | None:
        return next((c for c in self.campos if c.campo == nombre), None)


class OrigenPropuesta(_M):
    dominio: str
    asunto: str | None = None
    fecha: date


class Propuesta(_M):
    id: str = Field(default_factory=lambda: f"prop_{uuid4().hex}", pattern=r"^prop_[0-9a-f]{32}$")
    case_id: str
    elemento_id: str
    destino: str  # module key
    tipo: TipoPropuesta
    herramienta: str | None = None
    argumentos: dict[str, Any] = Field(default_factory=dict)
    hechos: dict[str, Any] = Field(default_factory=dict)
    titulo: str = Field(max_length=200)
    explicacion: str = Field(max_length=1200)
    # Each proposed value with the literal snippet it was read from.
    evidencias: list[CampoGuardado] = Field(default_factory=list)
    origen: OrigenPropuesta
    estado: EstadoPropuesta = "pendiente"
    creada: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decidida: datetime | None = None
    resultado: str | None = None


class Conexion(_M):
    case_id: str
    user_id: str | None
    cuenta: str | None
    alcance: list[str]
    token_cifrado: str
    conectada_el: datetime
    history_id: str | None = None
    ultima_sincronizacion: datetime | None = None
    ultimo_error: str | None = None
    estado: Literal["conectada", "reconectar"] = "conectada"


_lock = threading.RLock()
_wal: set[str] = set()


@contextmanager
def conectar(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    ruta = db_path or ruta_db()
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        if ruta not in _wal:
            conn.execute("PRAGMA journal_mode=WAL;")
            with _lock:
                _wal.add(ruta)
        conn.executescript(_ESQUEMA)  # idempotent and cheap: a recreated file always gets its tables
        with conn:
            yield conn
    finally:
        conn.close()


def _hash(estado: str) -> str:
    return hashlib.sha256(estado.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Connections and OAuth states
# --------------------------------------------------------------------------- #


def guardar_conexion(conexion: Conexion, db_path: str | None = None) -> None:
    with conectar(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO correo_conexiones (case_id, user_id, cuenta, alcance, token_cifrado, conectada_el,"
            " history_id, ultima_sincronizacion, ultimo_error, estado) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                conexion.case_id,
                conexion.user_id,
                conexion.cuenta,
                json.dumps(conexion.alcance),
                conexion.token_cifrado,
                conexion.conectada_el.isoformat(),
                conexion.history_id,
                conexion.ultima_sincronizacion.isoformat() if conexion.ultima_sincronizacion else None,
                conexion.ultimo_error,
                conexion.estado,
            ),
        )


def conexion_de(case_id: str, db_path: str | None = None) -> Conexion | None:
    with conectar(db_path) as conn:
        fila = conn.execute("SELECT * FROM correo_conexiones WHERE case_id = ?", (case_id,)).fetchone()
    if fila is None:
        return None
    datos = dict(fila)
    datos["alcance"] = json.loads(datos["alcance"])
    return Conexion.model_validate(datos)


def actualizar_conexion(case_id: str, db_path: str | None = None, **cambios: Any) -> None:
    permitidos = {"history_id", "ultima_sincronizacion", "ultimo_error", "estado", "token_cifrado", "cuenta"}
    columnas = [c for c in cambios if c in permitidos]
    if not columnas:
        return
    valores = [cambios[c].isoformat() if isinstance(cambios[c], datetime) else cambios[c] for c in columnas]
    with conectar(db_path) as conn:
        conn.execute(
            f"UPDATE correo_conexiones SET {', '.join(f'{c} = ?' for c in columnas)} WHERE case_id = ?",  # noqa: S608
            (*valores, case_id),
        )


def borrar_conexion(case_id: str, db_path: str | None = None) -> bool:
    with conectar(db_path) as conn:
        return conn.execute("DELETE FROM correo_conexiones WHERE case_id = ?", (case_id,)).rowcount > 0


def guardar_estado_oauth(estado: str, *, case_id: str, user_id: str | None, verificador_cifrado: str, db_path: str | None = None) -> None:
    ahora = datetime.now(UTC)
    with conectar(db_path) as conn:
        conn.execute("DELETE FROM correo_oauth_estados WHERE creado < ?", ((ahora - VIDA_ESTADO_OAUTH).isoformat(),))
        conn.execute(
            "INSERT INTO correo_oauth_estados (estado_hash, case_id, user_id, verificador_cifrado, creado) VALUES (?,?,?,?,?)",
            (_hash(estado), case_id, user_id, verificador_cifrado, ahora.isoformat()),
        )


def consumir_estado_oauth(estado: str, db_path: str | None = None) -> dict[str, Any] | None:
    """The request in flight for ``estado`` (deleted: single use), or None when unknown or expired."""
    with conectar(db_path) as conn:
        fila = conn.execute("SELECT * FROM correo_oauth_estados WHERE estado_hash = ?", (_hash(estado),)).fetchone()
        if fila is None:
            return None
        conn.execute("DELETE FROM correo_oauth_estados WHERE estado_hash = ?", (_hash(estado),))
    datos = dict(fila)
    if datetime.fromisoformat(datos["creado"]) < datetime.now(UTC) - VIDA_ESTADO_OAUTH:
        return None
    return datos


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #


def ids_guardados(case_id: str, db_path: str | None = None) -> set[str]:
    with conectar(db_path) as conn:
        return {f["id"] for f in conn.execute("SELECT id FROM correo_elementos WHERE case_id = ?", (case_id,))}


def guardar_elementos(case_id: str, elementos: list[Elemento], db_path: str | None = None) -> int:
    with conectar(db_path) as conn:
        for elemento in elementos:
            conn.execute(
                "INSERT OR REPLACE INTO correo_elementos (case_id, id, fecha, categoria, payload_json) VALUES (?,?,?,?,?)",
                (case_id, elemento.id, elemento.fecha.isoformat(), elemento.categoria, elemento.model_dump_json()),
            )
    return len(elementos)


def elementos(case_id: str, *, desde: date | None = None, db_path: str | None = None) -> list[Elemento]:
    """Items of a case, newest first."""
    consulta = "SELECT payload_json FROM correo_elementos WHERE case_id = ?"
    params: tuple[Any, ...] = (case_id,)
    if desde is not None:
        consulta += " AND fecha >= ?"
        params += (desde.isoformat(),)
    with conectar(db_path) as conn:
        filas = conn.execute(consulta + " ORDER BY fecha DESC, id", params).fetchall()
    return [Elemento.model_validate_json(f["payload_json"]) for f in filas]


def elemento(case_id: str, elemento_id: str, db_path: str | None = None) -> Elemento | None:
    with conectar(db_path) as conn:
        fila = conn.execute(
            "SELECT payload_json FROM correo_elementos WHERE case_id = ? AND id = ?", (case_id, elemento_id)
        ).fetchone()
    return Elemento.model_validate_json(fila["payload_json"]) if fila else None


def purgar_anteriores(case_id: str, limite: date, db_path: str | None = None) -> int:
    """Forget items older than ``limite`` (retention) that no pending proposal uses."""
    with conectar(db_path) as conn:
        return conn.execute(
            "DELETE FROM correo_elementos WHERE case_id = ? AND fecha < ? AND id NOT IN "
            "(SELECT elemento_id FROM correo_propuestas WHERE case_id = ? AND estado = 'pendiente')",
            (case_id, limite.isoformat(), case_id),
        ).rowcount


def borrar_datos(case_id: str, db_path: str | None = None) -> int:
    """Erase the derived items, proposals and pinned contacts of a case (the connection is kept)."""
    with conectar(db_path) as conn:
        return sum(
            conn.execute(f"DELETE FROM {tabla} WHERE case_id = ?", (case_id,)).rowcount  # noqa: S608
            for tabla in ("correo_elementos", "correo_propuestas", "correo_contactos")
        )


# --------------------------------------------------------------------------- #
# Proposals
# --------------------------------------------------------------------------- #


def anadir_propuesta(propuesta: Propuesta, db_path: str | None = None) -> bool:
    """Store a proposal; False when the item already had one (one proposal per message, ever)."""
    with conectar(db_path) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO correo_propuestas (id, case_id, elemento_id, estado, payload_json) VALUES (?,?,?,?,?)",
            (propuesta.id, propuesta.case_id, propuesta.elemento_id, propuesta.estado, propuesta.model_dump_json()),
        )
        return cursor.rowcount == 1


def propuestas(case_id: str, *, estado: EstadoPropuesta | None = None, db_path: str | None = None) -> list[Propuesta]:
    consulta = "SELECT payload_json FROM correo_propuestas WHERE case_id = ?"
    params: tuple[Any, ...] = (case_id,)
    if estado is not None:
        consulta += " AND estado = ?"
        params += (estado,)
    with conectar(db_path) as conn:
        filas = conn.execute(consulta + " ORDER BY seq DESC", params).fetchall()
    return [Propuesta.model_validate_json(f["payload_json"]) for f in filas]


def propuesta(propuesta_id: str, db_path: str | None = None) -> Propuesta | None:
    with conectar(db_path) as conn:
        fila = conn.execute("SELECT payload_json FROM correo_propuestas WHERE id = ?", (propuesta_id,)).fetchone()
    return Propuesta.model_validate_json(fila["payload_json"]) if fila else None


def transicion(
    propuesta_id: str,
    *,
    desde: EstadoPropuesta,
    hacia: EstadoPropuesta,
    resultado: str | None = None,
    db_path: str | None = None,
) -> Propuesta | None:
    """Move a proposal between states atomically (a confirmation runs at most once); None if not in ``desde``."""
    with conectar(db_path) as conn:
        fila = conn.execute(
            "SELECT payload_json FROM correo_propuestas WHERE id = ? AND estado = ?", (propuesta_id, desde)
        ).fetchone()
        if fila is None:
            return None
        actual = Propuesta.model_validate_json(fila["payload_json"])
        cambios: dict[str, Any] = {"estado": hacia}
        if hacia != "aplicando":
            cambios["decidida"] = datetime.now(UTC)
        if resultado is not None:
            cambios["resultado"] = resultado[:1000]
        nueva = actual.model_copy(update=cambios)
        cursor = conn.execute(
            "UPDATE correo_propuestas SET estado = ?, payload_json = ? WHERE id = ? AND estado = ?",
            (hacia, nueva.model_dump_json(), propuesta_id, desde),
        )
        return nueva if cursor.rowcount == 1 else None


# --------------------------------------------------------------------------- #
# Pinned contacts
# --------------------------------------------------------------------------- #


def contactos(case_id: str, db_path: str | None = None) -> list[str]:
    with conectar(db_path) as conn:
        return [
            f["direccion"]
            for f in conn.execute("SELECT direccion FROM correo_contactos WHERE case_id = ? ORDER BY direccion", (case_id,))
        ]


def fijar_contacto(case_id: str, direccion: str, db_path: str | None = None) -> None:
    with conectar(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO correo_contactos (case_id, direccion) VALUES (?, ?)", (case_id, direccion))


def soltar_contacto(case_id: str, direccion: str, db_path: str | None = None) -> bool:
    with conectar(db_path) as conn:
        borrado = conn.execute(
            "DELETE FROM correo_contactos WHERE case_id = ? AND direccion = ?", (case_id, direccion)
        ).rowcount
        # The full address leaves the items too: only the domain stays.
        for fila in conn.execute(
            "SELECT id, payload_json FROM correo_elementos WHERE case_id = ?", (case_id,)
        ).fetchall():
            item = Elemento.model_validate_json(fila["payload_json"])
            if item.remitente == direccion:
                conn.execute(
                    "UPDATE correo_elementos SET payload_json = ? WHERE case_id = ? AND id = ?",
                    (item.model_copy(update={"remitente": None}).model_dump_json(), case_id, fila["id"]),
                )
    return borrado > 0


# --------------------------------------------------------------------------- #
# Export and erasure
# --------------------------------------------------------------------------- #


def _existen(conn: sqlite3.Connection) -> set[str]:
    return {
        f[0]
        for f in conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({','.join('?' * len(_TABLAS))})",  # noqa: S608
            _TABLAS,
        )
    }


def exportar(case_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Everything stored for a case, for the user's export (the encrypted token and OAuth states left out:
    they are credentials, not data about the user)."""
    conexion = conexion_de(case_id, db_path)
    return {
        "conexion": conexion.model_dump(mode="json", exclude={"token_cifrado"}) if conexion else None,
        "elementos": [e.model_dump(mode="json") for e in elementos(case_id, db_path=db_path)],
        "propuestas": [p.model_dump(mode="json") for p in propuestas(case_id, db_path=db_path)],
        "contactos_fijados": contactos(case_id, db_path),
    }


# Tokens of erased cases to revoke at Google after the erasure (best effort, outside the transaction).
_revocar_despues: list[tuple[str, str, str | None]] = []


def al_borrar_caso(conn: sqlite3.Connection, case_id: str) -> None:
    """``SQLiteCaseStateStore.delete_case`` hook: every row of the case goes in the same transaction."""
    existentes = _existen(conn)
    if "correo_conexiones" in existentes:
        for fila in conn.execute(
            "SELECT token_cifrado, user_id FROM correo_conexiones WHERE case_id = ?", (case_id,)
        ).fetchall():
            with _lock:
                _revocar_despues.append((case_id, fila[0], fila[1]))
    for tabla in _TABLAS:
        if tabla in existentes:
            conn.execute(f"DELETE FROM {tabla} WHERE case_id = ?", (case_id,))  # noqa: S608
    if _revocar_despues:
        hilo = threading.Thread(target=_revocar_pendientes, name="correo-revocar", daemon=True)
        hilo.start()


def _revocar_pendientes() -> None:
    from hacienda_gpt.modulos.correo.oauth import revocar_token_cifrado

    while True:
        with _lock:
            if not _revocar_despues:
                return
            case_id, cifrado, user_id = _revocar_despues.pop()
        try:
            revocar_token_cifrado(cifrado, case_id=case_id, user_id=user_id)
        except Exception:  # noqa: BLE001 - best effort; the token is already gone from our database
            logger.warning("Could not revoke the Gmail grant of an erased case at Google")


def instalar() -> None:
    """Hook the erasure into the case store (idempotent; done when the package is imported)."""
    from hacienda_gpt.decision import state_store_sqlite

    if al_borrar_caso not in state_store_sqlite.AL_BORRAR:
        state_store_sqlite.AL_BORRAR.append(al_borrar_caso)


__all__ = [
    "CampoGuardado",
    "Conexion",
    "Elemento",
    "OrigenPropuesta",
    "Propuesta",
    "al_borrar_caso",
    "borrar_datos",
    "conexion_de",
    "elementos",
    "exportar",
    "instalar",
    "propuestas",
    "ruta_db",
    "transicion",
]

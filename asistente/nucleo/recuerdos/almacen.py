"""The conversation memory's tables, in the same SQLite file as the cases (one backup, one erasure).

Every row carries its owner — ``(case_id, user_id)``, ``''`` for an absent part — and EVERY query of this
module filters on both columns: a conversation id, a remembered item's id or a batch result of another owner is
simply «not found». Conversations are unique per owner (``UNIQUE (case_id, user_id, id)``), so a client that
sends somebody else's conversation id only ever writes into a conversation of its own.

Privacy mechanics:

* ``secure_delete`` is on and every erasure ends with a WAL checkpoint, so erased words are overwritten in the
  file and do not survive in the ``-wal``;
* forgetting one item leaves a word-free tombstone (which conversation and which message it came from), so
  summarising that conversation again does not bring it back; forgetting everything deletes the conversations
  too, so nothing is left to learn it from;
* raw messages are purged after ``MEMORIA_RETENCION_MENSAJES_DIAS``; a conversation that was never summarised
  goes with them;
* the case and the account erase the memory through triggers (``catalogo/recuerdos.py``) besides the explicit
  calls of ``DELETE /me``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.cerebro.contrato import OrigenRecuerdo, RecuerdoMemoria
from asistente.nucleo.recuerdos.recuperacion import analizar, vector_a_bytes, vector_de_bytes
from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.migraciones.catalogo import recuerdos as esquema

TipoGuardado = Literal["dato", "preferencia", "contexto"]
EstadoRecuerdo = Literal["activo", "sustituido", "olvidado"]
EstadoConversacion = Literal["abierta", "en_lote", "resumida", "sin_resumen"]
TIPOS_GUARDADOS: tuple[TipoGuardado, ...] = ("dato", "preferencia", "contexto")
MAX_TITULO = 80


def ahora() -> datetime:
    """The memory's clock (UTC), in one place so tests can fix it."""
    return datetime.now(UTC)


def _zona() -> Any:
    try:
        return ZoneInfo("Europe/Madrid")
    except ZoneInfoNotFoundError:  # pragma: no cover - only without a tz database
        return UTC


def fecha_local(momento: datetime) -> date:
    """The Spanish calendar day of ``momento`` (naive datetimes are taken as UTC)."""
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(_zona()).date()


def en_utc(momento: datetime) -> datetime:
    return momento.replace(tzinfo=UTC) if momento.tzinfo is None else momento.astimezone(UTC)


def _iso(momento: datetime) -> str:
    # Always with microseconds: stored moments are compared as text.
    return en_utc(momento).isoformat(timespec="microseconds")


def _momento(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


@dataclass(frozen=True)
class Titular:
    """Whose memory: a case and/or a user. ``''`` for an absent part; both empty means nobody (no memory)."""

    case_id: str
    user_id: str

    @classmethod
    def de(cls, case_id: str | None, user_id: str | None) -> Titular:
        return cls(case_id=(case_id or "").strip(), user_id=(user_id or "").strip())

    @property
    def vacio(self) -> bool:
        return not self.case_id and not self.user_id

    @property
    def clave_interruptor(self) -> str:
        """The «pause the memory» switch is the user's (every case of theirs); without a user, the case's."""
        return self.user_id or f"caso:{self.case_id}"


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fuente(_Modelo):
    """One place a remembered item rests on: a conversation, the day, the user's literal words, which message."""

    conversacion_id: str
    fecha: date
    cita: str = Field(min_length=1, max_length=400)
    n: int = Field(default=0, ge=0)


class Recuerdo(_Modelo):
    """A remembered item as stored («lo que sé de ti»)."""

    id: str
    tipo: TipoGuardado
    clave: str
    texto: str = Field(min_length=1, max_length=600)
    origen: Fuente
    fuentes: list[Fuente] = Field(default_factory=list)
    creado: datetime
    actualizado: datetime
    corregido: bool = False
    sensible: bool = False
    sustituido_por: str | None = None
    estado: EstadoRecuerdo = "activo"
    usos: int = 0
    ultimo_uso: datetime | None = None

    def como_memoria(self, relevancia: float = 0.0) -> RecuerdoMemoria:
        return RecuerdoMemoria(
            id=self.id,
            tipo=self.tipo,
            texto=self.texto,
            origen=OrigenRecuerdo(
                conversacion_id=self.origen.conversacion_id, fecha=self.origen.fecha, cita=self.origen.cita
            ),
            relevancia=relevancia,
            corregido=self.corregido,
        )


class Conversacion(_Modelo):
    id: str
    titulo: str
    creada: datetime
    actualizada: datetime
    estado: EstadoConversacion
    resumen: str | None = None
    # The user's words the summary rests on (their first question, in their own words), kept with the summary.
    cita: str | None = None
    resumida_en: datetime | None = None
    mensajes: int = 0
    resumidos: int = 0
    intentos: int = 0


class Mensaje(_Modelo):
    n: int
    rol: Literal["user", "assistant"]
    texto: str
    fecha: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class FilaLote:
    """A conversation waiting in a provider batch (ids and counts only, never text)."""

    custom_id: str
    lote_id: str
    proveedor: str
    modelo: str
    titular: Titular
    conversacion_id: str
    mensajes: int
    enviado_el: datetime


def nuevo_id() -> str:
    return f"r_{secrets.token_hex(6)}"


def titulo_provisional(pregunta: str) -> str:
    """The first line of the first question, shortened (until the summariser gives a real title)."""
    linea = next((ln.strip() for ln in pregunta.splitlines() if ln.strip()), "").strip()
    linea = " ".join(linea.split())
    if len(linea) > MAX_TITULO:
        linea = linea[: MAX_TITULO - 1].rsplit(" ", 1)[0] + "…"
    return linea or "Conversación"


def terminos_de_recuerdo(texto: str, clave: str, cita: str = "") -> str:
    """The index terms of an item: its text, its key and the user's own words (how they would ask again)."""
    return " ".join(analizar(f"{texto} {clave.replace('.', ' ').replace('_', ' ')} {cita}"))


def terminos_de_conversacion(titulo: str, resumen: str | None, cita: str | None = None) -> str:
    return " ".join(analizar(f"{titulo} {resumen or ''} {cita or ''}"))


def tabla_existe(db_path: str) -> bool:
    """Whether ``db_path`` exists and has the memory's tables (read-only check: never creates the file)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        fila = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recuerdos'").fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _crear_tablas(conn: sqlite3.Connection) -> None:
    asegurar_esquema(conn, "recuerdos")  # migrations (asistente/plataforma); a no-op when they are off
    for sentencia in esquema.TABLAS_E_INDICES:
        conn.execute(sentencia)


def _crear_disparadores(conn: sqlite3.Connection) -> bool:
    """The erasure triggers, once the cases' and the accounts' tables exist (migrations off: as the store did)."""
    tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "case_states" in tablas:
        conn.execute(esquema.DISPARADOR_CASO)
    if "users" in tablas:
        conn.execute(esquema.DISPARADOR_USUARIO)
    return {"case_states", "users"} <= tablas


_DONDE = "case_id = ? AND user_id = ?"


def _p(titular: Titular) -> tuple[str, str]:
    return (titular.case_id, titular.user_id)


class AlmacenRecuerdos:
    """SQLite store of the conversation memory. Tables and triggers are created on first use."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._con_disparadores = False

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            # Erased words are overwritten in the file, not just unlinked.
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                _crear_tablas(conn)
            self._conn = conn
        if not self._con_disparadores:
            with self._conn:
                self._con_disparadores = _crear_disparadores(self._conn)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._con_disparadores = False

    @staticmethod
    def _volcar(conn: sqlite3.Connection) -> None:
        """Move the write-ahead log into the file so erased words do not stay readable in the ``-wal``."""
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.Error:  # another connection is busy: the next checkpoint will do it
            pass

    # ------------------------------------------------------------------ conversations and messages

    def registrar_turno(
        self,
        titular: Titular,
        conversacion_id: str,
        *,
        fecha: datetime,
        pregunta: str,
        respuesta: str,
        meta: dict[str, Any] | None = None,
    ) -> int:
        """Append the user's question and the answer to the conversation (created on its first turn).

        Returns the conversation's message count. A conversation summarised before goes back to «abierta» so the
        new messages are summarised too.
        """
        momento = _iso(fecha)
        with self._lock:
            conn = self._get()
            with conn:
                fila = conn.execute(
                    f"SELECT mensajes FROM recuerdos_conversaciones WHERE {_DONDE} AND id = ?",
                    (*_p(titular), conversacion_id),
                ).fetchone()
                if fila is None:
                    conn.execute(
                        "INSERT INTO recuerdos_conversaciones (id, case_id, user_id, titulo, creada, actualizada, "
                        "estado) VALUES (?, ?, ?, ?, ?, ?, 'abierta')",
                        (conversacion_id, *_p(titular), titulo_provisional(pregunta), momento, momento),
                    )
                    previos = 0
                else:
                    previos = int(fila["mensajes"])
                conn.executemany(
                    "INSERT INTO recuerdos_mensajes (conversacion_id, case_id, user_id, n, rol, texto, fecha, "
                    "meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (conversacion_id, *_p(titular), previos + 1, "user", pregunta, momento, "{}"),
                        (
                            conversacion_id,
                            *_p(titular),
                            previos + 2,
                            "assistant",
                            respuesta,
                            momento,
                            json.dumps(meta or {}, ensure_ascii=False),
                        ),
                    ],
                )
                conn.execute(
                    "UPDATE recuerdos_conversaciones SET mensajes = ?, actualizada = ?, "
                    "estado = CASE WHEN estado IN ('resumida', 'sin_resumen') THEN 'abierta' ELSE estado END, "
                    f"intentos = 0 WHERE {_DONDE} AND id = ?",
                    (previos + 2, momento, *_p(titular), conversacion_id),
                )
        return previos + 2

    @staticmethod
    def _conversacion(fila: sqlite3.Row) -> Conversacion:
        return Conversacion(
            id=fila["id"],
            titulo=fila["titulo"],
            creada=datetime.fromisoformat(fila["creada"]),
            actualizada=datetime.fromisoformat(fila["actualizada"]),
            estado=fila["estado"],
            resumen=fila["resumen"],
            cita=fila["cita"],
            resumida_en=_momento(fila["resumida_en"]),
            mensajes=fila["mensajes"],
            resumidos=fila["resumidos"],
            intentos=fila["intentos"],
        )

    def conversaciones(self, titular: Titular, *, limite: int = 500) -> list[Conversacion]:
        """The owner's conversations, most recent first."""
        with self._lock:
            filas = (
                self._get()
                .execute(
                    f"SELECT * FROM recuerdos_conversaciones WHERE {_DONDE} ORDER BY actualizada DESC, seq DESC "
                    "LIMIT ?",
                    (*_p(titular), limite),
                )
                .fetchall()
            )
        return [self._conversacion(f) for f in filas]

    def conversacion(self, titular: Titular, conversacion_id: str) -> Conversacion | None:
        with self._lock:
            fila = (
                self._get()
                .execute(
                    f"SELECT * FROM recuerdos_conversaciones WHERE {_DONDE} AND id = ?",
                    (*_p(titular), conversacion_id),
                )
                .fetchone()
            )
        return self._conversacion(fila) if fila else None

    def mensajes(self, titular: Titular, conversacion_id: str, *, desde: int = 0) -> list[Mensaje]:
        """The retained messages of a conversation of the owner (``n > desde``), in order."""
        with self._lock:
            filas = (
                self._get()
                .execute(
                    f"SELECT n, rol, texto, fecha, meta_json FROM recuerdos_mensajes WHERE {_DONDE} "
                    "AND conversacion_id = ? AND n > ? ORDER BY n",
                    (*_p(titular), conversacion_id, desde),
                )
                .fetchall()
            )
        return [
            Mensaje(
                n=f["n"],
                rol=f["rol"],
                texto=f["texto"],
                fecha=datetime.fromisoformat(f["fecha"]),
                meta=json.loads(f["meta_json"] or "{}"),
            )
            for f in filas
        ]

    def sin_resumir(self, titular: Titular, *, excluir: str | None, limite: int = 200) -> list[tuple[str, Mensaje]]:
        """The owner's USER messages not covered by a summary yet (other conversations), newest first."""
        with self._lock:
            filas = (
                self._get()
                .execute(
                    "SELECT m.conversacion_id, m.n, m.texto, m.fecha FROM recuerdos_mensajes m "
                    "JOIN recuerdos_conversaciones c ON c.case_id = m.case_id AND c.user_id = m.user_id "
                    "AND c.id = m.conversacion_id "
                    "WHERE m.case_id = ? AND m.user_id = ? AND m.rol = 'user' AND m.n > c.resumidos "
                    "AND m.conversacion_id != ? ORDER BY m.fecha DESC, m.seq DESC LIMIT ?",
                    (*_p(titular), excluir or "", limite),
                )
                .fetchall()
            )
        return [
            (
                f["conversacion_id"],
                Mensaje(n=f["n"], rol="user", texto=f["texto"], fecha=datetime.fromisoformat(f["fecha"])),
            )
            for f in filas
        ]

    def mensajes_retenidos(self, titular: Titular) -> dict[str, int]:
        """conversation id -> retained messages, for the owner."""
        with self._lock:
            filas = (
                self._get()
                .execute(
                    f"SELECT conversacion_id, COUNT(*) AS c FROM recuerdos_mensajes WHERE {_DONDE} "
                    "GROUP BY conversacion_id",
                    _p(titular),
                )
                .fetchall()
            )
        return {f["conversacion_id"]: int(f["c"]) for f in filas}

    def pendientes(
        self, *, hasta: datetime, limite: int, titular: Titular | None = None
    ) -> list[tuple[Titular, Conversacion]]:
        """Conversations idle since ``hasta`` with messages not summarised yet, oldest first (of every owner, or
        of ``titular``)."""
        filtro, params = "", tuple[str, ...]()
        if titular is not None:
            filtro, params = f" AND {_DONDE}", _p(titular)
        with self._lock:
            filas = (
                self._get()
                .execute(
                    "SELECT * FROM recuerdos_conversaciones WHERE estado = 'abierta' AND mensajes > resumidos "
                    f"AND actualizada <= ?{filtro} ORDER BY actualizada, seq LIMIT ?",
                    (_iso(hasta), *params, limite),
                )
                .fetchall()
            )
        return [(Titular(f["case_id"], f["user_id"]), self._conversacion(f)) for f in filas]

    def guardar_resumen(
        self,
        titular: Titular,
        conversacion_id: str,
        *,
        titulo: str,
        resumen: str | None,
        cita: str | None,
        cubiertos: int,
        momento: datetime,
        vector: Sequence[float] | None = None,
    ) -> bool:
        """Store a summary covering the first ``cubiertos`` messages (it stays «abierta» if more arrived since).
        ``cita`` None keeps the one stored before (the first question may be gone from the retained messages)."""
        with self._lock:
            conn = self._get()
            with conn:
                previa = conn.execute(
                    f"SELECT cita FROM recuerdos_conversaciones WHERE {_DONDE} AND id = ?",
                    (*_p(titular), conversacion_id),
                ).fetchone()
                cita_final = cita or (previa["cita"] if previa is not None else None)
                cursor = conn.execute(
                    "UPDATE recuerdos_conversaciones SET titulo = ?, resumen = ?, cita = ?, resumida_en = ?, "
                    "resumidos = ?, intentos = 0, terminos = ?, embedding = ?, "
                    "estado = CASE WHEN mensajes > ? THEN 'abierta' ELSE 'resumida' END "
                    f"WHERE {_DONDE} AND id = ?",
                    (
                        titulo,
                        resumen,
                        cita_final,
                        _iso(momento),
                        cubiertos,
                        terminos_de_conversacion(titulo, resumen, cita_final),
                        vector_a_bytes(vector) if vector else None,
                        cubiertos,
                        *_p(titular),
                        conversacion_id,
                    ),
                )
        return cursor.rowcount == 1

    def fijar_estado(self, titular: Titular, conversacion_id: str, estado: EstadoConversacion) -> None:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    f"UPDATE recuerdos_conversaciones SET estado = ? WHERE {_DONDE} AND id = ?",
                    (estado, *_p(titular), conversacion_id),
                )

    def fallo_de_resumen(self, titular: Titular, conversacion_id: str, *, max_intentos: int) -> None:
        """One more failed attempt; after ``max_intentos`` the conversation stops being retried («sin_resumen»)."""
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "UPDATE recuerdos_conversaciones SET intentos = intentos + 1, "
                    "estado = CASE WHEN intentos + 1 >= ? THEN 'sin_resumen' ELSE 'abierta' END "
                    f"WHERE {_DONDE} AND id = ?",
                    (max_intentos, *_p(titular), conversacion_id),
                )

    # ------------------------------------------------------------------ remembered items

    @staticmethod
    def _recuerdo(fila: sqlite3.Row) -> Recuerdo:
        fuentes = [Fuente.model_validate(f) for f in json.loads(fila["fuentes_json"] or "[]")]
        origen = Fuente(
            conversacion_id=fila["conversacion_id"],
            fecha=date.fromisoformat(fila["fecha"]),
            cita=fila["cita"],
            n=next(
                (f.n for f in fuentes if f.conversacion_id == fila["conversacion_id"] and f.cita == fila["cita"]), 0
            ),
        )
        return Recuerdo(
            id=fila["id"],
            tipo=fila["tipo"],
            clave=fila["clave"],
            texto=fila["texto"],
            origen=origen,
            fuentes=fuentes or [origen],
            creado=datetime.fromisoformat(fila["creado"]),
            actualizado=datetime.fromisoformat(fila["actualizado"]),
            corregido=bool(fila["corregido"]),
            sensible=bool(fila["sensible"]),
            sustituido_por=fila["sustituido_por"],
            estado=fila["estado"],
            usos=fila["usos"],
            ultimo_uso=_momento(fila["ultimo_uso"]),
        )

    def recuerdos(self, titular: Titular, *, estados: Sequence[EstadoRecuerdo] = ("activo",)) -> list[Recuerdo]:
        """The owner's items in those states (never tombstones), newest first."""
        validos = [e for e in estados if e != "olvidado"]
        if not validos:
            return []
        marcas = ",".join("?" * len(validos))
        with self._lock:
            filas = (
                self._get()
                .execute(
                    f"SELECT * FROM recuerdos WHERE {_DONDE} AND estado IN ({marcas}) ORDER BY actualizado DESC, "
                    "seq DESC",
                    (*_p(titular), *validos),
                )
                .fetchall()
            )
        return [self._recuerdo(f) for f in filas]

    def recuerdo(self, titular: Titular, recuerdo_id: str) -> Recuerdo | None:
        """One item of the owner (None for another owner's, or a forgotten one)."""
        with self._lock:
            fila = (
                self._get()
                .execute(
                    f"SELECT * FROM recuerdos WHERE {_DONDE} AND id = ? AND estado != 'olvidado'",
                    (*_p(titular), recuerdo_id),
                )
                .fetchone()
            )
        return self._recuerdo(fila) if fila else None

    def vectores(self, titular: Titular) -> dict[str, list[float]]:
        """Stored embeddings of the owner's active items and summaries (id -> vector)."""
        salida: dict[str, list[float]] = {}
        with self._lock:
            conn = self._get()
            for fila in conn.execute(
                f"SELECT id, embedding FROM recuerdos WHERE {_DONDE} AND estado = 'activo' AND embedding IS NOT NULL",
                _p(titular),
            ):
                vector = vector_de_bytes(fila["embedding"])
                if vector:
                    salida[fila["id"]] = vector
            for fila in conn.execute(
                f"SELECT id, embedding FROM recuerdos_conversaciones WHERE {_DONDE} AND embedding IS NOT NULL",
                _p(titular),
            ):
                vector = vector_de_bytes(fila["embedding"])
                if vector:
                    salida[f"c:{fila['id']}"] = vector
        return salida

    def terminos(self, titular: Titular) -> tuple[dict[str, str], dict[str, str]]:
        """The stored index terms: (item id -> terms, conversation id -> terms)."""
        with self._lock:
            conn = self._get()
            items = {
                f["id"]: f["terminos"]
                for f in conn.execute(
                    f"SELECT id, terminos FROM recuerdos WHERE {_DONDE} AND estado = 'activo'", _p(titular)
                )
            }
            conversaciones = {
                f["id"]: f["terminos"]
                for f in conn.execute(
                    f"SELECT id, terminos FROM recuerdos_conversaciones WHERE {_DONDE} AND resumen IS NOT NULL",
                    _p(titular),
                )
            }
        return items, conversaciones

    def _escribir(
        self, conn: sqlite3.Connection, titular: Titular, r: Recuerdo, vector: Sequence[float] | None
    ) -> None:
        conn.execute(
            "INSERT INTO recuerdos (id, case_id, user_id, tipo, clave, texto, conversacion_id, fecha, cita, "
            "fuentes_json, creado, actualizado, corregido, sensible, sustituido_por, estado, usos, ultimo_uso, "
            "terminos, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET tipo = excluded.tipo, clave = excluded.clave, texto = excluded.texto, "
            "conversacion_id = excluded.conversacion_id, fecha = excluded.fecha, cita = excluded.cita, "
            "fuentes_json = excluded.fuentes_json, actualizado = excluded.actualizado, "
            "corregido = excluded.corregido, sensible = excluded.sensible, sustituido_por = excluded.sustituido_por, "
            "estado = excluded.estado, usos = excluded.usos, ultimo_uso = excluded.ultimo_uso, "
            "terminos = excluded.terminos, embedding = COALESCE(excluded.embedding, recuerdos.embedding) "
            "WHERE recuerdos.case_id = excluded.case_id AND recuerdos.user_id = excluded.user_id",
            (
                r.id,
                *_p(titular),
                r.tipo,
                r.clave,
                r.texto,
                r.origen.conversacion_id,
                r.origen.fecha.isoformat(),
                r.origen.cita,
                json.dumps([f.model_dump(mode="json") for f in r.fuentes], ensure_ascii=False),
                _iso(r.creado),
                _iso(r.actualizado),
                int(r.corregido),
                int(r.sensible),
                r.sustituido_por,
                r.estado,
                r.usos,
                _iso(r.ultimo_uso) if r.ultimo_uso else None,
                terminos_de_recuerdo(r.texto, r.clave, " ".join(f.cita for f in r.fuentes)),
                vector_a_bytes(vector) if vector else None,
            ),
        )

    def guardar(
        self, titular: Titular, *recuerdos: Recuerdo, vectores: Mapping[str, Sequence[float]] | None = None
    ) -> None:
        """Insert or update items of the owner, in one transaction (an id of another owner is never touched)."""
        with self._lock:
            conn = self._get()
            with conn:
                for r in recuerdos:
                    self._escribir(conn, titular, r, (vectores or {}).get(r.id))

    def bloqueados(self, titular: Titular) -> set[tuple[str, int]]:
        """(conversation, message) pairs the user made the memory forget: never learnt from again."""
        salida: set[tuple[str, int]] = set()
        with self._lock:
            for fila in self._get().execute(
                f"SELECT fuentes_json FROM recuerdos WHERE {_DONDE} AND estado = 'olvidado'", _p(titular)
            ):
                for f in json.loads(fila["fuentes_json"] or "[]"):
                    salida.add((str(f.get("conversacion_id", "")), int(f.get("n", 0))))
        return salida

    def olvidar(self, titular: Titular, ids: Iterable[str]) -> int:
        """Forget items: their words go; a word-free tombstone (conversation, message) stays while the
        conversation's messages exist, so summarising it again does not bring them back."""
        olvidados = 0
        with self._lock:
            conn = self._get()
            with conn:
                for recuerdo_id in ids:
                    fila = conn.execute(
                        f"SELECT fuentes_json, conversacion_id FROM recuerdos WHERE {_DONDE} AND id = ? "
                        "AND estado != 'olvidado'",
                        (*_p(titular), recuerdo_id),
                    ).fetchone()
                    if fila is None:
                        continue
                    marcas = [
                        {"conversacion_id": f.get("conversacion_id", ""), "n": int(f.get("n", 0))}
                        for f in json.loads(fila["fuentes_json"] or "[]")
                    ]
                    conn.execute(
                        "UPDATE recuerdos SET texto = '', cita = '', clave = '', terminos = '', embedding = NULL, "
                        "fuentes_json = ?, sustituido_por = NULL, estado = 'olvidado', usos = 0, ultimo_uso = NULL "
                        f"WHERE {_DONDE} AND id = ?",
                        (json.dumps(marcas), *_p(titular), recuerdo_id),
                    )
                    olvidados += 1
            self._volcar(conn)
        return olvidados

    def eliminar(self, titular: Titular, ids: Iterable[str]) -> int:
        """Delete items outright (no tombstone): the cap on the number of items."""
        lista = list(ids)
        if not lista:
            return 0
        with self._lock:
            conn = self._get()
            with conn:
                marcas = ",".join("?" * len(lista))
                borrados = conn.execute(
                    f"DELETE FROM recuerdos WHERE {_DONDE} AND id IN ({marcas})", (*_p(titular), *lista)
                ).rowcount
            self._volcar(conn)
        return borrados

    def registrar_usos(self, titular: Titular, ids: Sequence[str], momento: datetime) -> None:
        if not ids:
            return
        with self._lock:
            conn = self._get()
            with conn:
                marcas = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE recuerdos SET usos = usos + 1, ultimo_uso = ? WHERE {_DONDE} AND id IN ({marcas}) "
                    "AND estado = 'activo'",
                    (_iso(momento), *_p(titular), *ids),
                )

    # ------------------------------------------------------------------ erasure

    def borrar_conversacion(self, titular: Titular, conversacion_id: str) -> bool:
        """Delete a conversation: its messages, its summary, its pending batch, and every item (and tombstone)
        that rested only on it. Items that also rest on another conversation stay, with that origin."""
        with self._lock:
            conn = self._get()
            with conn:
                existe = conn.execute(
                    f"SELECT 1 FROM recuerdos_conversaciones WHERE {_DONDE} AND id = ?",
                    (*_p(titular), conversacion_id),
                ).fetchone()
                if existe is None:
                    return False
                conn.execute(
                    f"DELETE FROM recuerdos_mensajes WHERE {_DONDE} AND conversacion_id = ?",
                    (*_p(titular), conversacion_id),
                )
                conn.execute(
                    f"DELETE FROM recuerdos_lotes WHERE {_DONDE} AND conversacion_id = ?",
                    (*_p(titular), conversacion_id),
                )
                conn.execute(
                    f"DELETE FROM recuerdos_conversaciones WHERE {_DONDE} AND id = ?", (*_p(titular), conversacion_id)
                )
                self._quitar_fuentes(conn, titular, {conversacion_id})
            self._volcar(conn)
        return True

    def _quitar_fuentes(self, conn: sqlite3.Connection, titular: Titular, conversaciones: set[str]) -> None:
        """Drop the given conversations from every item's sources (an item with none left is deleted)."""
        for fila in conn.execute(f"SELECT * FROM recuerdos WHERE {_DONDE}", _p(titular)).fetchall():
            fuentes = json.loads(fila["fuentes_json"] or "[]")
            restantes = [f for f in fuentes if f.get("conversacion_id") not in conversaciones]
            if len(restantes) == len(fuentes) and fila["conversacion_id"] not in conversaciones:
                continue
            if not restantes:
                conn.execute(f"DELETE FROM recuerdos WHERE {_DONDE} AND id = ?", (*_p(titular), fila["id"]))
                continue
            if fila["estado"] == "olvidado":
                conn.execute(
                    f"UPDATE recuerdos SET fuentes_json = ? WHERE {_DONDE} AND id = ?",
                    (json.dumps(restantes), *_p(titular), fila["id"]),
                )
                continue
            origen = max(restantes, key=lambda f: (str(f.get("fecha", "")), int(f.get("n", 0))))
            conn.execute(
                "UPDATE recuerdos SET fuentes_json = ?, conversacion_id = ?, fecha = ?, cita = ? "
                f"WHERE {_DONDE} AND id = ?",
                (
                    json.dumps(restantes, ensure_ascii=False),
                    origen["conversacion_id"],
                    origen["fecha"],
                    origen["cita"],
                    *_p(titular),
                    fila["id"],
                ),
            )

    def borrar_titular(self, titular: Titular) -> dict[str, int]:
        """«Olvidar todo»: every conversation, message, item (and tombstone) and pending batch of the owner.
        The on/off switch is kept (it is the user's choice, not something learnt)."""
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(f"DELETE FROM recuerdos_lotes WHERE {_DONDE}", _p(titular))
                mensajes = conn.execute(f"DELETE FROM recuerdos_mensajes WHERE {_DONDE}", _p(titular)).rowcount
                recuerdos = conn.execute(
                    f"DELETE FROM recuerdos WHERE {_DONDE} AND estado != 'olvidado'", _p(titular)
                ).rowcount
                conn.execute(f"DELETE FROM recuerdos WHERE {_DONDE}", _p(titular))
                conversaciones = conn.execute(
                    f"DELETE FROM recuerdos_conversaciones WHERE {_DONDE}", _p(titular)
                ).rowcount
            self._volcar(conn)
        return {"recuerdos": recuerdos, "conversaciones": conversaciones, "mensajes": mensajes}

    def borrar_de_usuario(self, user_id: str, case_ids: Sequence[str]) -> int:
        """Account erasure: everything of the user, and everything of their cases. Returns the rows removed."""
        filtro = "user_id = ?"
        params: list[str] = [user_id]
        if case_ids:
            filtro += f" OR case_id IN ({','.join('?' * len(case_ids))})"
            params.extend(case_ids)
        total = 0
        with self._lock:
            conn = self._get()
            with conn:
                for tabla in ("recuerdos_lotes", "recuerdos_mensajes", "recuerdos", "recuerdos_conversaciones"):
                    total += conn.execute(f"DELETE FROM {tabla} WHERE {filtro}", params).rowcount
                claves = [user_id, *(f"caso:{c}" for c in case_ids)]
                conn.execute(
                    f"DELETE FROM recuerdos_interruptor WHERE titular IN ({','.join('?' * len(claves))})", claves
                )
            self._volcar(conn)
        return total

    def purgar_mensajes(self, *, antes_de: datetime) -> dict[str, int]:
        """Retention: delete raw messages older than ``antes_de`` (every owner). A conversation never
        summarised goes with its last message; tombstones of conversations with no message left go too (there
        is nothing left to learn them from)."""
        with self._lock:
            conn = self._get()
            with conn:
                mensajes = conn.execute("DELETE FROM recuerdos_mensajes WHERE fecha < ?", (_iso(antes_de),)).rowcount
                vacias = conn.execute(
                    "SELECT c.case_id, c.user_id, c.id, c.resumen FROM recuerdos_conversaciones c "
                    "WHERE NOT EXISTS (SELECT 1 FROM recuerdos_mensajes m WHERE m.case_id = c.case_id "
                    "AND m.user_id = c.user_id AND m.conversacion_id = c.id)"
                ).fetchall()
                sin_mensajes: dict[Titular, set[str]] = {}
                conversaciones = 0
                for fila in vacias:
                    titular = Titular(fila["case_id"], fila["user_id"])
                    sin_mensajes.setdefault(titular, set()).add(fila["id"])
                    if fila["resumen"] is None:
                        conn.execute(
                            f"DELETE FROM recuerdos_lotes WHERE {_DONDE} AND conversacion_id = ?",
                            (*_p(titular), fila["id"]),
                        )
                        conversaciones += conn.execute(
                            f"DELETE FROM recuerdos_conversaciones WHERE {_DONDE} AND id = ?",
                            (*_p(titular), fila["id"]),
                        ).rowcount
                # Tombstones only matter while their conversation can be summarised again.
                for titular, ids in sin_mensajes.items():
                    for t in conn.execute(
                        f"SELECT id, fuentes_json FROM recuerdos WHERE {_DONDE} AND estado = 'olvidado'",
                        _p(titular),
                    ).fetchall():
                        marcas = json.loads(t["fuentes_json"] or "[]")
                        quedan = [f for f in marcas if f.get("conversacion_id") not in ids]
                        if len(quedan) == len(marcas):
                            continue
                        if quedan:
                            conn.execute(
                                f"UPDATE recuerdos SET fuentes_json = ? WHERE {_DONDE} AND id = ?",
                                (json.dumps(quedan), *_p(titular), t["id"]),
                            )
                        else:
                            conn.execute(f"DELETE FROM recuerdos WHERE {_DONDE} AND id = ?", (*_p(titular), t["id"]))
            if mensajes or conversaciones:
                self._volcar(conn)
        return {"mensajes": mensajes, "conversaciones": conversaciones}

    # ------------------------------------------------------------------ the user's switch

    def activa(self, titular: Titular) -> bool:
        """Whether the user lets the memory learn and be used (default: yes)."""
        with self._lock:
            fila = (
                self._get()
                .execute("SELECT activa FROM recuerdos_interruptor WHERE titular = ?", (titular.clave_interruptor,))
                .fetchone()
            )
        return True if fila is None else bool(fila["activa"])

    def fijar_activa(self, titular: Titular, activa: bool, momento: datetime) -> None:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "INSERT INTO recuerdos_interruptor (titular, activa, cambiado_el) VALUES (?, ?, ?) "
                    "ON CONFLICT(titular) DO UPDATE SET activa = excluded.activa, cambiado_el = excluded.cambiado_el",
                    (titular.clave_interruptor, int(activa), _iso(momento)),
                )

    # ------------------------------------------------------------------ batches

    def guardar_lote(self, filas: Sequence[FilaLote]) -> None:
        with self._lock:
            conn = self._get()
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO recuerdos_lotes (custom_id, lote_id, proveedor, modelo, case_id, user_id, "
                    "conversacion_id, mensajes, enviado_el) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            f.custom_id,
                            f.lote_id,
                            f.proveedor,
                            f.modelo,
                            *_p(f.titular),
                            f.conversacion_id,
                            f.mensajes,
                            _iso(f.enviado_el),
                        )
                        for f in filas
                    ],
                )
                for f in filas:
                    conn.execute(
                        f"UPDATE recuerdos_conversaciones SET estado = 'en_lote' WHERE {_DONDE} AND id = ? "
                        "AND estado = 'abierta'",
                        (*_p(f.titular), f.conversacion_id),
                    )

    def lotes_en_curso(self) -> dict[str, list[FilaLote]]:
        """Batch id -> its rows (every owner)."""
        salida: dict[str, list[FilaLote]] = {}
        with self._lock:
            for f in self._get().execute("SELECT * FROM recuerdos_lotes ORDER BY enviado_el, custom_id"):
                salida.setdefault(f["lote_id"], []).append(
                    FilaLote(
                        custom_id=f["custom_id"],
                        lote_id=f["lote_id"],
                        proveedor=f["proveedor"],
                        modelo=f["modelo"],
                        titular=Titular(f["case_id"], f["user_id"]),
                        conversacion_id=f["conversacion_id"],
                        mensajes=f["mensajes"],
                        enviado_el=datetime.fromisoformat(f["enviado_el"]),
                    )
                )
        return salida

    def quitar_de_lote(self, fila: FilaLote, *, reabrir: bool) -> None:
        """A batch row is done; ``reabrir`` puts its conversation back in the queue (it was not summarised)."""
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute("DELETE FROM recuerdos_lotes WHERE custom_id = ?", (fila.custom_id,))
                if reabrir:
                    conn.execute(
                        f"UPDATE recuerdos_conversaciones SET estado = 'abierta' WHERE {_DONDE} AND id = ? "
                        "AND estado = 'en_lote'",
                        (*_p(fila.titular), fila.conversacion_id),
                    )

    # ------------------------------------------------------------------ export

    def exportar(self, titular: Titular) -> dict[str, Any]:
        """Everything kept about the owner, as JSON-ready data (access / portability). Tombstones hold no
        words and are not listed."""
        conversaciones = []
        for c in self.conversaciones(titular, limite=100_000):
            datos = c.model_dump(mode="json")
            datos["mensajes_retenidos"] = [m.model_dump(mode="json") for m in self.mensajes(titular, c.id)]
            conversaciones.append(datos)
        return {
            "memoria_activa": self.activa(titular),
            "conversaciones": conversaciones,
            "recuerdos": [r.model_dump(mode="json") for r in self.recuerdos(titular, estados=("activo", "sustituido"))],
        }

    def titulares_de_caso(self, case_id: str) -> list[Titular]:
        """Every owner key stored under a case (the export of a case covers all of them)."""
        with self._lock:
            filas = (
                self._get()
                .execute(
                    "SELECT DISTINCT case_id, user_id FROM recuerdos_conversaciones WHERE case_id = ? "
                    "UNION SELECT DISTINCT case_id, user_id FROM recuerdos WHERE case_id = ?",
                    (case_id, case_id),
                )
                .fetchall()
            )
        return [Titular(f["case_id"], f["user_id"]) for f in filas]


_almacenes: dict[str, AlmacenRecuerdos] = {}
_almacenes_lock = RLock()


def almacen(db_path: str) -> AlmacenRecuerdos:
    """The process-wide store for ``db_path`` (one connection per file)."""
    clave = str(Path(db_path))
    with _almacenes_lock:
        store = _almacenes.get(clave)
        if store is None:
            store = _almacenes[clave] = AlmacenRecuerdos(clave)
        return store


def olvidar_almacenes() -> None:
    """Close every cached store (tests that recreate their database files)."""
    with _almacenes_lock:
        for store in _almacenes.values():
            store.close()
        _almacenes.clear()


__all__ = [
    "TIPOS_GUARDADOS",
    "AlmacenRecuerdos",
    "Conversacion",
    "EstadoConversacion",
    "EstadoRecuerdo",
    "FilaLote",
    "Fuente",
    "Mensaje",
    "Recuerdo",
    "TipoGuardado",
    "Titular",
    "ahora",
    "almacen",
    "en_utc",
    "fecha_local",
    "nuevo_id",
    "olvidar_almacenes",
    "tabla_existe",
    "terminos_de_conversacion",
    "terminos_de_recuerdo",
    "titulo_provisional",
]

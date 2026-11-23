"""The council's sessions, in the cases' database (one backup, one erasure).

One row per session (``consejo_sesiones``, migrations owner ``consejo``): the state machine, the worker's lease, the
estimate and the cap the user confirmed, the cost spent, and one JSON column per part of the deliberation (the
pseudonymised dossier, the proposals, the anonymous critiques, the rankings, the ruling). ``extra_json`` keeps what
the contract has no field for: who convened it and why, the options suggested, the live progress of the phases.

The state machine (``EstadoSesion``)::

    pendiente_confirmacion ──confirmar──► en_cola ──reclamar──► instruyendo ─► deliberando ─► sintetizando
           │  └──rechazar──► rechazada        │                      │ (lease renewed on every phase)
           └──cancelar / caducar──► cancelada ◄┴──cancelar─────────────┤
                                                        terminada ◄──┴──► fallida

Every transition is ONE conditional ``UPDATE … WHERE estado IN (…)`` (or a ``BEGIN IMMEDIATE`` transaction for the
claim), so two requests racing (a double «Confirmar», the job and the API thread claiming at once) change the row
once. A worker writes results only while it still holds the lease (``arriendo_id``): a session cancelled meanwhile,
or taken over after the lease expired, never gets a stale result. At most one session per case runs at a time
(the claim skips a case with a live lease).

Isolation: every read and write takes ``case_id`` (the API passes the case it already checked belongs to the
caller), so a session id from another case is «not found». Erasure: triggers on ``case_states`` and ``users``
(the catalogue's DDL, run here too when the migrations are off), plus :meth:`AlmacenConsejo.borrar_de_usuario` for
the account erasure (it also checkpoints the WAL). Retention: :meth:`purgar` drops finished sessions older than
``CONSEJO_RETENCION_DIAS``.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from asistente.nucleo.consejo.contrato import (
    CosteSesion,
    Critica,
    Dictamen,
    EstadoSesion,
    ExpedienteConsejo,
    Propuesta,
    Ranking,
    SesionConsejo,
)
from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.migraciones.catalogo import consejo as esquema
from asistente.plataforma.sqlite import abrir_sqlite

EN_MARCHA: tuple[EstadoSesion, ...] = ("instruyendo", "deliberando", "sintetizando")
TERMINALES: tuple[EstadoSesion, ...] = ("terminada", "fallida", "cancelada", "rechazada")
ABIERTOS: tuple[EstadoSesion, ...] = ("pendiente_confirmacion", "en_cola", *EN_MARCHA)
MAX_PROGRESO = 80

_PROPUESTAS = TypeAdapter(list[Propuesta])
_CRITICAS = TypeAdapter(list[Critica])
_RANKINGS = TypeAdapter(list[Ranking])

OrigenSesion = Literal["cerebro", "usuario", "cli"]


class PasoProgreso(BaseModel):
    """One progress event of a running session (for the live view and the audit trail)."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["fase", "miembro", "aviso"]
    fase: str | None = None
    estado: str = ""
    miembro_id: str | None = None
    en: datetime


class ExtraSesion(BaseModel):
    """What a session keeps beyond the contract's fields."""

    model_config = ConfigDict(extra="forbid")

    origen: OrigenSesion = "usuario"
    motivo: str = Field(default="", max_length=600)  # why it was convened (the detector's reasons, the brain's)
    opciones_sugeridas: list[str] = Field(default_factory=list, max_length=6)
    # The user's own leaning when they gave it on convening (sealed in the dossier), and their previous messages of
    # the conversation (masked, at most four): the dossier reads the leaning from them.
    preferencia: str | None = Field(default=None, max_length=600)
    mensajes_previos: list[str] = Field(default_factory=list, max_length=4)
    minutos_estimados: int = 0
    llamadas_estimadas: int = 0
    miembros: list[str] = Field(default_factory=list)  # the seats' names, for the convocation card
    desglose: dict[str, Decimal] = Field(default_factory=dict)
    precios_conocidos: bool = True
    progreso: list[PasoProgreso] = Field(default_factory=list)
    confirmada: datetime | None = None
    terminada: datetime | None = None


@dataclass
class Registro:
    """A stored session: the contract's object plus the row's own fields."""

    sesion: SesionConsejo
    extra: ExtraSesion
    arriendo_id: str | None
    arriendo_hasta: datetime | None
    intentos: int
    notificacion_id: str | None
    pregunta_huella: str


def nuevo_id() -> str:
    return f"cons_{uuid4().hex}"


def huella_pregunta(case_id: str, pregunta: str) -> str:
    """A hash of the case and the normalised question (the same convocation is not created twice)."""
    from asistente.nucleo.memoria.modelo import normalizar_texto

    return hashlib.sha256(f"{case_id}\x00{normalizar_texto(pregunta)}".encode()).hexdigest()[:40]


def ahora() -> datetime:
    return datetime.now(UTC)


def _ts(momento: datetime) -> str:
    # Fixed-width UTC text, so comparing strings compares instants.
    return momento.astimezone(UTC).isoformat(timespec="microseconds")


def _fecha(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def tabla_existe(db_path: str) -> bool:
    """Whether ``db_path`` exists and has the sessions table (read-only check: never creates the file)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        fila = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'consejo_sesiones'").fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


class AlmacenConsejo:
    """SQLite store of the council's sessions. A short connection per call (the API, its background thread and the
    job share the file); tables and triggers are created on first use."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()

    # -- connection -----------------------------------------------------------------------------------------

    @contextmanager
    def _conectar(self) -> Generator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = abrir_sqlite(self.db_path, timeout=30.0, isolation_level=None, filas=True)
        try:
            # Erased deliberations are overwritten in the file, not just unlinked.
            conn.execute("PRAGMA secure_delete=ON;")
            conn.execute("BEGIN")
            try:
                asegurar_esquema(conn, "consejo")  # migrations (asistente/plataforma); a no-op when they are off
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
                raise
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaccion(self, *, inmediata: bool = False) -> Generator[sqlite3.Connection]:
        with self._lock, self._conectar() as conn:
            conn.execute("BEGIN IMMEDIATE" if inmediata else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # -- rows -----------------------------------------------------------------------------------------------

    @staticmethod
    def _registro(fila: sqlite3.Row) -> Registro:
        coste = CosteSesion.model_validate_json(fila["coste_json"] or "{}")
        expediente = ExpedienteConsejo.model_validate_json(fila["expediente_json"]) if fila["expediente_json"] else None
        dictamen = Dictamen.model_validate_json(fila["dictamen_json"]) if fila["dictamen_json"] else None
        sesion = SesionConsejo(
            id=fila["id"],
            case_id=fila["case_id"],
            user_id=fila["user_id"] or None,
            estado=fila["estado"],
            fase=fila["fase"],
            pregunta_usuario=fila["pregunta_usuario"],
            creada=datetime.fromisoformat(fila["creada"]),
            actualizada=datetime.fromisoformat(fila["actualizada"]),
            estimacion_usd=Decimal(fila["estimacion_usd"]),
            tope_usd=Decimal(fila["tope_usd"]),
            coste=coste,
            expediente=expediente,
            propuestas=_PROPUESTAS.validate_json(fila["propuestas_json"] or "[]"),
            criticas=_CRITICAS.validate_json(fila["criticas_json"] or "[]"),
            rankings=_RANKINGS.validate_json(fila["rankings_json"] or "[]"),
            dictamen=dictamen,
            error=fila["error"],
        )
        return Registro(
            sesion=sesion,
            extra=ExtraSesion.model_validate_json(fila["extra_json"] or "{}"),
            arriendo_id=fila["arriendo_id"],
            arriendo_hasta=_fecha(fila["arriendo_hasta"]),
            intentos=int(fila["intentos"]),
            notificacion_id=fila["notificacion_id"],
            pregunta_huella=fila["pregunta_huella"],
        )

    def crear(self, sesion: SesionConsejo, extra: ExtraSesion, *, huella: str) -> Registro:
        with self._transaccion() as conn:
            conn.execute(
                """
                INSERT INTO consejo_sesiones (id, case_id, user_id, estado, fase, pregunta_usuario, pregunta_huella,
                    creada, actualizada, estimacion_usd, tope_usd, coste_json, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sesion.id,
                    sesion.case_id,
                    sesion.user_id or "",
                    sesion.estado,
                    sesion.fase,
                    sesion.pregunta_usuario,
                    huella,
                    _ts(sesion.creada),
                    _ts(sesion.actualizada),
                    str(sesion.estimacion_usd),
                    str(sesion.tope_usd),
                    sesion.coste.model_dump_json(),
                    extra.model_dump_json(),
                ),
            )
        registro = self.obtener(sesion.id, case_id=sesion.case_id)
        assert registro is not None
        return registro

    def obtener(self, sesion_id: str, *, case_id: str | None) -> Registro | None:
        """One session; with ``case_id`` only if it belongs to that case (``None``: the job's internal reads)."""
        sql = "SELECT * FROM consejo_sesiones WHERE id = ?"
        params: list[Any] = [sesion_id]
        if case_id is not None:
            sql += " AND case_id = ?"
            params.append(case_id)
        with self._lock, self._conectar() as conn:
            fila = conn.execute(sql, params).fetchone()
        return self._registro(fila) if fila else None

    def listar(self, case_id: str, *, limite: int = 50) -> list[Registro]:
        with self._lock, self._conectar() as conn:
            filas = conn.execute(
                "SELECT * FROM consejo_sesiones WHERE case_id = ? ORDER BY creada DESC, seq DESC LIMIT ?",
                (case_id, max(1, min(limite, 500))),
            ).fetchall()
        return [self._registro(f) for f in filas]

    def abierta_igual(self, case_id: str, huella: str) -> Registro | None:
        """An open session (pending, queued or running) of the case for the same question."""
        marcas = ",".join("?" * len(ABIERTOS))
        with self._lock, self._conectar() as conn:
            fila = conn.execute(
                f"SELECT * FROM consejo_sesiones WHERE case_id = ? AND pregunta_huella = ? AND estado IN ({marcas}) "
                "ORDER BY creada DESC LIMIT 1",
                (case_id, huella, *ABIERTOS),
            ).fetchone()
        return self._registro(fila) if fila else None

    # -- transitions ------------------------------------------------------------------------------------------

    def transicion(
        self,
        sesion_id: str,
        *,
        case_id: str,
        desde: Sequence[EstadoSesion],
        a: EstadoSesion,
        error: str | None = None,
        extra: ExtraSesion | None = None,
        momento: datetime | None = None,
    ) -> bool:
        """``desde`` → ``a`` atomically; False when the session is not (or no longer) in one of ``desde``.

        Leaving a running state clears the lease (a running worker then cannot write its result)."""
        cuando = momento or ahora()
        marcas = ",".join("?" * len(desde))
        sets = ["estado = ?", "actualizada = ?"]
        params: list[Any] = [a, _ts(cuando)]
        if a in TERMINALES or a == "en_cola":
            sets += ["arriendo_id = NULL", "arriendo_hasta = NULL"]
        if a == "rechazada":
            sets.append("fase = NULL")
        if error is not None:
            sets.append("error = ?")
            params.append(error[:1000])
        if extra is not None:
            sets.append("extra_json = ?")
            params.append(extra.model_dump_json())
        with self._transaccion() as conn:
            cursor = conn.execute(
                f"UPDATE consejo_sesiones SET {', '.join(sets)} WHERE id = ? AND case_id = ? AND estado IN ({marcas})",
                (*params, sesion_id, case_id, *desde),
            )
            return cursor.rowcount == 1

    def reclamar(
        self,
        *,
        momento: datetime | None = None,
        arriendo_s: int,
        max_intentos: int,
        limite: int = 1,
        sesion_id: str | None = None,
        case_id: str | None = None,
    ) -> list[tuple[Registro, str]]:
        """Claim up to ``limite`` sessions to run: queued ones, or running ones whose lease expired (a crashed
        worker) and that have runs left. Never two of the same case, never a case with a live lease. Returns each
        claimed session with its new lease id."""
        cuando = momento or ahora()
        en_marcha = ",".join("?" * len(EN_MARCHA))
        sql = (
            f"SELECT * FROM consejo_sesiones WHERE (estado = 'en_cola' OR (estado IN ({en_marcha}) AND "
            "(arriendo_hasta IS NULL OR arriendo_hasta < ?) AND intentos < ?))"
        )
        params: list[Any] = [*EN_MARCHA, _ts(cuando), max_intentos]
        if sesion_id is not None:
            sql += " AND id = ?"
            params.append(sesion_id)
        if case_id is not None:
            sql += " AND case_id = ?"
            params.append(case_id)
        sql += " ORDER BY creada, seq"
        reclamadas: list[tuple[Registro, str]] = []
        with self._transaccion(inmediata=True) as conn:
            candidatas = conn.execute(sql, params).fetchall()
            ocupados = {
                f["case_id"]
                for f in conn.execute(
                    f"SELECT case_id FROM consejo_sesiones WHERE estado IN ({en_marcha}) AND arriendo_hasta >= ?",
                    (*EN_MARCHA, _ts(cuando)),
                )
            }
            for fila in candidatas:
                if len(reclamadas) >= limite:
                    break
                if fila["case_id"] in ocupados:
                    continue
                arriendo = uuid4().hex
                conn.execute(
                    "UPDATE consejo_sesiones SET estado = 'instruyendo', fase = 'instruccion', arriendo_id = ?, "
                    "arriendo_hasta = ?, intentos = intentos + 1, actualizada = ?, error = NULL WHERE id = ?",
                    (arriendo, _ts(cuando + timedelta(seconds=arriendo_s)), _ts(cuando), fila["id"]),
                )
                ocupados.add(fila["case_id"])
                nueva = conn.execute("SELECT * FROM consejo_sesiones WHERE id = ?", (fila["id"],)).fetchone()
                reclamadas.append((self._registro(nueva), arriendo))
        return reclamadas

    def renovar(
        self,
        sesion_id: str,
        arriendo_id: str,
        *,
        arriendo_s: int,
        estado: EstadoSesion | None = None,
        fase: str | None = None,
        paso: PasoProgreso | None = None,
        expediente: ExpedienteConsejo | None = None,
        coste: CosteSesion | None = None,
        momento: datetime | None = None,
    ) -> bool:
        """Renew the lease (and record the phase / a progress step); False when the lease is no longer ours (the
        session was cancelled or taken over): the worker must stop."""
        cuando = momento or ahora()
        en_marcha = ",".join("?" * len(EN_MARCHA))
        with self._transaccion(inmediata=True) as conn:
            fila = conn.execute(
                f"SELECT extra_json FROM consejo_sesiones WHERE id = ? AND arriendo_id = ? AND estado IN ({en_marcha})",
                (sesion_id, arriendo_id, *EN_MARCHA),
            ).fetchone()
            if fila is None:
                return False
            sets = ["arriendo_hasta = ?", "actualizada = ?"]
            params: list[Any] = [_ts(cuando + timedelta(seconds=arriendo_s)), _ts(cuando)]
            if estado is not None:
                sets.append("estado = ?")
                params.append(estado)
            if fase is not None:
                sets.append("fase = ?")
                params.append(fase)
            if paso is not None:
                extra = ExtraSesion.model_validate_json(fila["extra_json"] or "{}")
                extra.progreso = [*extra.progreso, paso][-MAX_PROGRESO:]
                sets.append("extra_json = ?")
                params.append(extra.model_dump_json())
            if expediente is not None:
                sets.append("expediente_json = ?")
                params.append(expediente.model_dump_json())
            if coste is not None:
                sets.append("coste_json = ?")
                params.append(coste.model_dump_json())
            conn.execute(f"UPDATE consejo_sesiones SET {', '.join(sets)} WHERE id = ?", (*params, sesion_id))
            return True

    def guardar_resultado(self, sesion: SesionConsejo, arriendo_id: str, *, extra: ExtraSesion) -> bool:
        """Write a finished run (``terminada`` or ``fallida``) — only while the lease is still ours."""
        en_marcha = ",".join("?" * len(EN_MARCHA))
        with self._transaccion(inmediata=True) as conn:
            cursor = conn.execute(
                f"""
                UPDATE consejo_sesiones SET estado = ?, fase = ?, actualizada = ?, coste_json = ?, error = ?,
                    expediente_json = ?, propuestas_json = ?, criticas_json = ?, rankings_json = ?, dictamen_json = ?,
                    extra_json = ?, arriendo_id = NULL, arriendo_hasta = NULL
                WHERE id = ? AND arriendo_id = ? AND estado IN ({en_marcha})
                """,
                (
                    sesion.estado,
                    sesion.fase,
                    _ts(sesion.actualizada),
                    sesion.coste.model_dump_json(),
                    sesion.error,
                    sesion.expediente.model_dump_json() if sesion.expediente is not None else None,
                    _PROPUESTAS.dump_json(sesion.propuestas).decode(),
                    _CRITICAS.dump_json(sesion.criticas).decode(),
                    _RANKINGS.dump_json(sesion.rankings).decode(),
                    sesion.dictamen.model_dump_json() if sesion.dictamen is not None else None,
                    extra.model_dump_json(),
                    sesion.id,
                    arriendo_id,
                    *EN_MARCHA,
                ),
            )
            return cursor.rowcount == 1

    def anotar_notificacion(self, sesion_id: str, notification_id: str) -> None:
        with self._transaccion() as conn:
            conn.execute("UPDATE consejo_sesiones SET notificacion_id = ? WHERE id = ?", (notification_id, sesion_id))

    def agotadas(self, *, momento: datetime | None = None, max_intentos: int) -> list[Registro]:
        """Running sessions whose lease expired and that have no runs left (the job marks them «fallida»)."""
        cuando = momento or ahora()
        en_marcha = ",".join("?" * len(EN_MARCHA))
        with self._lock, self._conectar() as conn:
            filas = conn.execute(
                f"SELECT * FROM consejo_sesiones WHERE estado IN ({en_marcha}) AND arriendo_hasta < ? "
                "AND intentos >= ?",
                (*EN_MARCHA, _ts(cuando), max_intentos),
            ).fetchall()
        return [self._registro(f) for f in filas]

    def fallar_agotada(self, sesion_id: str, *, error: str, momento: datetime | None = None) -> bool:
        """Mark an exhausted session «fallida» (only if its lease is still expired: no worker came back)."""
        cuando = momento or ahora()
        en_marcha = ",".join("?" * len(EN_MARCHA))
        with self._transaccion(inmediata=True) as conn:
            cursor = conn.execute(
                f"UPDATE consejo_sesiones SET estado = 'fallida', error = ?, actualizada = ?, arriendo_id = NULL, "
                f"arriendo_hasta = NULL WHERE id = ? AND estado IN ({en_marcha}) AND arriendo_hasta < ?",
                (error[:1000], _ts(cuando), sesion_id, *EN_MARCHA, _ts(cuando)),
            )
            return cursor.rowcount == 1

    def caducar_pendientes(self, *, antes_de: datetime) -> list[Registro]:
        """Convocations never confirmed nor declined since before ``antes_de`` → «cancelada» (returned)."""
        with self._transaccion(inmediata=True) as conn:
            filas = conn.execute(
                "SELECT * FROM consejo_sesiones WHERE estado = 'pendiente_confirmacion' AND creada < ?",
                (_ts(antes_de),),
            ).fetchall()
            if filas:
                conn.execute(
                    "UPDATE consejo_sesiones SET estado = 'cancelada', error = ?, actualizada = ? "
                    "WHERE estado = 'pendiente_confirmacion' AND creada < ?",
                    ("La propuesta caducó sin que la confirmaras.", _ts(ahora()), _ts(antes_de)),
                )
        return [self._registro(f) for f in filas]

    # -- erasure, retention, export -----------------------------------------------------------------------------

    def borrar(self, sesion_id: str, *, case_id: str) -> bool:
        with self._transaccion() as conn:
            cursor = conn.execute("DELETE FROM consejo_sesiones WHERE id = ? AND case_id = ?", (sesion_id, case_id))
            borrada = cursor.rowcount == 1
        if borrada:
            self._volcar()
        return borrada

    def borrar_de_caso(self, case_id: str) -> int:
        with self._transaccion() as conn:
            n = conn.execute("DELETE FROM consejo_sesiones WHERE case_id = ?", (case_id,)).rowcount
        self._volcar()
        return n

    def borrar_de_usuario(self, user_id: str, case_ids: Sequence[str]) -> int:
        """Account erasure: the user's sessions and their cases' (the triggers would do it too)."""
        ids = list(dict.fromkeys(case_ids))
        with self._transaccion() as conn:
            n = conn.execute("DELETE FROM consejo_sesiones WHERE user_id = ?", (user_id,)).rowcount
            for inicio in range(0, len(ids), 500):
                trozo = ids[inicio : inicio + 500]
                marcas = ",".join("?" * len(trozo))
                n += conn.execute(f"DELETE FROM consejo_sesiones WHERE case_id IN ({marcas})", trozo).rowcount
        self._volcar()
        return n

    def purgar(self, *, antes_de: datetime) -> int:
        """Finished sessions last updated before ``antes_de`` (retention)."""
        marcas = ",".join("?" * len(TERMINALES))
        with self._transaccion() as conn:
            n = conn.execute(
                f"DELETE FROM consejo_sesiones WHERE estado IN ({marcas}) AND actualizada < ?",
                (*TERMINALES, _ts(antes_de)),
            ).rowcount
        if n:
            self._volcar()
        return n

    def _volcar(self) -> None:
        """Checkpoint the WAL so erased rows do not linger in it."""
        try:
            with self._lock, self._conectar() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.Error:  # best effort: the rows are already gone from the table
            pass

    def exportar(self, case_id: str) -> list[dict[str, Any]]:
        """Every session of the case, complete (the account export)."""
        salida: list[dict[str, Any]] = []
        for registro in self.listar(case_id, limite=500):
            datos = registro.sesion.model_dump(mode="json")
            datos["origen"] = registro.extra.origen
            datos["motivo"] = registro.extra.motivo
            datos["opciones_sugeridas"] = list(registro.extra.opciones_sugeridas)
            datos["progreso"] = [p.model_dump(mode="json") for p in registro.extra.progreso]
            salida.append(datos)
        return salida


_almacenes: dict[str, AlmacenConsejo] = {}
_almacenes_lock = RLock()


def almacen(db_path: str) -> AlmacenConsejo:
    with _almacenes_lock:
        store = _almacenes.get(str(db_path))
        if store is None:
            store = _almacenes[str(db_path)] = AlmacenConsejo(db_path)
        return store


def exportar_consejo(db_path: str, case_id: str) -> list[dict[str, Any]]:
    """The account export's entry for one case; never creates the table."""
    if not tabla_existe(db_path):
        return []
    return almacen(db_path).exportar(case_id)


def borrar_consejo_de_usuario(db_path: str, *, user_id: str, case_ids: Sequence[str]) -> int:
    """Account erasure; never creates the table."""
    if not tabla_existe(db_path):
        return 0
    return almacen(db_path).borrar_de_usuario(user_id, case_ids)


__all__ = [
    "ABIERTOS",
    "EN_MARCHA",
    "TERMINALES",
    "AlmacenConsejo",
    "ExtraSesion",
    "OrigenSesion",
    "PasoProgreso",
    "Registro",
    "almacen",
    "borrar_consejo_de_usuario",
    "exportar_consejo",
    "huella_pregunta",
    "nuevo_id",
    "tabla_existe",
]

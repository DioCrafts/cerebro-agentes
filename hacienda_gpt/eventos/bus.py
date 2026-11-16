"""A small durable event bus on SQLite: outbox, consumers with cursors, deliveries with retries.

How an event flows (at-least-once, every step idempotent):

1. **Outbox.** A producer writes the event into ``eventos`` — for a profile change, in the SAME transaction
   that saves the case (see :mod:`hacienda_gpt.eventos.productores`), so the change and its event commit or
   roll back together. Producers that can re-run pass a ``clave`` and a re-run writes nothing.
2. **Consumers.** Each consumer keeps a cursor (the last event id it has fully handled). The built-in one,
   :class:`RepartoAModulos`, turns an event into deliveries: one per (module, case) that the event concerns
   (see :mod:`hacienda_gpt.eventos.rutas`) and whose module is on for the case's owner. The fan-out of one
   event and the cursor move are one transaction, and deliveries are unique per (event, module, case), so
   two workers, or a crash and a retry, never deliver twice. A consumer that fails on an event retries it
   with backoff and, after the last attempt, writes it as a dead letter and moves on.
3. **Deliveries.** A worker claims a batch with a lease (``BEGIN IMMEDIATE``: one claimer at a time), runs
   the module (:func:`hacienda_gpt.eventos.manejadores.despertar_modulo`) outside any transaction, and marks
   the row done only if it still holds the lease. A failure is retried with exponential backoff; after
   ``max_intentos`` the row is ``muerto`` (dead letter) and ``ops doctor`` reports it. A worker that dies
   mid-run loses its lease and another one runs the delivery again: handlers are idempotent (notices are
   keyed), which is what makes at-least-once safe.
4. **Timers.** A module can ask to be woken on a date (``proxima_revision``); the bus keeps one timer per
   case and module and, when the date comes, emits a ``fecha.umbral`` event for that module only.

No external broker: any process with the database file can produce, and ``python -m
hacienda_gpt.cli.eventos worker`` (or the API's in-process dispatcher) consumes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Protocol
from uuid import uuid4

from hacienda_gpt import settings
from hacienda_gpt.eventos.esquema import crear_tablas, hay_tablas
from hacienda_gpt.eventos.tipos import (
    FECHA_UMBRAL,
    GLOBALES,
    MODULO_DESACTIVADO,
    Evento,
    validar,
)

logger = logging.getLogger(__name__)

# Delivery states.
PENDIENTE = "pendiente"
EN_CURSO = "en_curso"
HECHO = "hecho"
OMITIDO = "omitido"
MUERTO = "muerto"
# The module column of a consumer's dead letter (it concerns no module).
PREFIJO_CONSUMIDOR = "consumidor:"


def _numero(nombre: str, defecto: float) -> float:
    try:
        return float(getattr(settings, nombre, defecto))
    except (TypeError, ValueError):
        return defecto


@dataclass(frozen=True)
class Configuracion:
    max_intentos: int = 5
    backoff_base_s: float = 30.0
    backoff_max_s: float = 3600.0
    arriendo_s: float = 300.0
    lote: int = 50

    @classmethod
    def desde_settings(cls) -> Configuracion:
        return cls(
            max_intentos=max(1, int(_numero("EVENTOS_MAX_INTENTOS", 5))),
            backoff_base_s=max(0.0, _numero("EVENTOS_BACKOFF_SEGUNDOS", 30)),
            arriendo_s=max(1.0, _numero("EVENTOS_ARRIENDO_SEGUNDOS", 300)),
        )

    def espera(self, intentos: int) -> timedelta:
        """Backoff after the ``intentos``-th failed attempt: base, 2×base, 4×base… capped."""
        return timedelta(seconds=min(self.backoff_max_s, self.backoff_base_s * (2 ** max(0, intentos - 1))))


def _iso(momento: datetime) -> str:
    return momento.astimezone(UTC).isoformat()


def _momento(texto: str) -> datetime:
    valor = datetime.fromisoformat(texto)
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=UTC)


def emitir_en(
    conn: sqlite3.Connection,
    tipo: str,
    *,
    case_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
    clave: str | None = None,
    ahora: datetime | None = None,
) -> int | None:
    """Write an event on ``conn`` WITHOUT committing: the caller's transaction decides.

    Returns the new event id, or None when an event with the same ``clave`` already exists.
    """
    texto = validar(tipo, case_id, payload or {})
    cursor = conn.execute(
        "INSERT OR IGNORE INTO eventos (uid, tipo, case_id, payload_json, clave, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uuid4().hex, tipo, case_id, texto, clave, _iso(ahora or datetime.now(UTC))),
    )
    return int(cursor.lastrowid) if cursor.rowcount == 1 and cursor.lastrowid is not None else None


def _evento(fila: Any) -> Evento:
    return Evento(
        id=int(fila["id"]),
        uid=str(fila["uid"]),
        tipo=str(fila["tipo"]),
        case_id=fila["case_id"],
        payload=json.loads(fila["payload_json"]),
        created_at=_momento(str(fila["created_at"])),
    )


class Consumidor(Protocol):
    """Reads the log in order with its own cursor. ``procesar`` runs inside the bus's transaction and must
    write only through ``conn`` (so its effects and the cursor move are atomic) and be idempotent."""

    nombre: str

    def procesar(self, bus: Bus, evento: Evento, conn: sqlite3.Connection) -> None: ...


@dataclass
class RepartoAModulos:
    """The consumer that wakes modules: one delivery per (module, case) the event concerns."""

    nombre: str = "modulos"

    def procesar(self, bus: Bus, evento: Evento, conn: sqlite3.Connection) -> None:
        from hacienda_gpt.eventos.rutas import le_afecta
        from hacienda_gpt.modulos.registro import hechos_compartidos

        if evento.tipo == MODULO_DESACTIVADO:
            # The module is off: nothing to wake. Its timer and its pending deliveries for the case go.
            bus.cancelar(conn, evento.case_id or "", evento.modulo or "")
            return
        compartidos = hechos_compartidos()
        casos = bus.casos_abiertos(conn) if evento.tipo in GLOBALES else [evento.case_id or ""]
        ahora = _iso(bus.ahora())
        for case_id in casos:
            for modulo in bus.modulos_de(case_id):
                if not le_afecta(modulo, evento, compartidos):
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO eventos_entregas
                        (evento_id, tipo, modulo, case_id, estado, intentos, siguiente_intento, evento_creado, creado)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (evento.id, evento.tipo, modulo.clave, case_id, PENDIENTE, ahora, _iso(evento.created_at), ahora),
                )


@dataclass
class Bus:
    """The bus over one database file. Thread-safe; open one per process (or per worker thread)."""

    db_path: str
    config: Configuracion = field(default_factory=Configuracion.desde_settings)
    # Injected in tests; by default the clock, today in Spain, and the modules on for a case's owner.
    reloj: Callable[[], datetime] | None = None
    hoy: Callable[[], date] | None = None
    modulos_activos: Callable[[str], Sequence[Any]] | None = None
    consumidores: list[Any] = field(default_factory=lambda: [RepartoAModulos()])

    def __post_init__(self) -> None:
        self.db_path = str(Path(self.db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA secure_delete=ON;")
        with self._lock:
            crear_tablas(self._conn)
        self._notificaciones: Any | None = None
        self._casos: Any | None = None
        self._auth: Any | None = None

    # ------------------------------------------------------------------ basics

    def ahora(self) -> datetime:
        return self.reloj() if self.reloj is not None else datetime.now(UTC)

    def dia(self) -> date:
        if self.hoy is not None:
            return self.hoy()
        from hacienda_gpt.proactive.jobs import hoy_en_espana

        return hoy_en_espana()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
            for store in (self._notificaciones, self._casos):
                if store is not None:
                    store.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction (``BEGIN IMMEDIATE``: writers queue here, so claims never overlap)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _leer(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def notificaciones(self) -> Any:
        if self._notificaciones is None:
            from hacienda_gpt.proactive.store import NotificationStore

            self._notificaciones = NotificationStore(self.db_path)
        return self._notificaciones

    def casos(self) -> Any:
        if self._casos is None:
            from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore

            self._casos = SQLiteCaseStateStore(self.db_path)
        return self._casos

    def modulos_de(self, case_id: str) -> Sequence[Any]:
        """Modules on for the owner of ``case_id`` (operator switch, defaults and the owner's choices)."""
        if self.modulos_activos is not None:
            return self.modulos_activos(case_id)
        from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso

        if settings.AUTH_MODE == "required" and self._auth is None:
            from hacienda_gpt.auth.store import AuthStore

            self._auth = AuthStore(self.db_path)
        return modulos_activos_para_caso(case_id, self._auth)

    def casos_abiertos(self, conn: sqlite3.Connection) -> list[str]:
        try:
            filas = conn.execute("SELECT case_id FROM case_states WHERE status != 'closed' ORDER BY case_id").fetchall()
        except sqlite3.OperationalError:  # no case table in this file
            return []
        return [str(f[0]) for f in filas]

    # ------------------------------------------------------------------ producing

    def emitir(
        self,
        tipo: str,
        *,
        case_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        clave: str | None = None,
    ) -> int | None:
        """Write one event (committed). None when ``clave`` was already emitted."""
        with self._tx() as conn:
            return emitir_en(conn, tipo, case_id=case_id, payload=payload, clave=clave, ahora=self.ahora())

    def eventos(self, *, desde_id: int = 0, limite: int = 1000) -> list[Evento]:
        filas = self._leer("SELECT * FROM eventos WHERE id > ? ORDER BY id LIMIT ?", (desde_id, limite))
        return [_evento(f) for f in filas]

    def evento(self, evento_id: int) -> Evento | None:
        filas = self._leer("SELECT * FROM eventos WHERE id = ?", (evento_id,))
        return _evento(filas[0]) if filas else None

    # ------------------------------------------------------------------ consumers

    def avanzar_consumidores(self) -> int:
        """Let every consumer read the events past its cursor. Returns the events handled."""
        return sum(self._avanzar(consumidor) for consumidor in self.consumidores)

    def _avanzar(self, consumidor: Any) -> int:
        nombre = str(consumidor.nombre)
        hechos = 0
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO eventos_cursores (consumidor, ultimo_id) VALUES (?, 0)", (nombre,))
            cursor = conn.execute("SELECT * FROM eventos_cursores WHERE consumidor = ?", (nombre,)).fetchone()
            ahora = self.ahora()
            if cursor["siguiente_intento"] and _momento(cursor["siguiente_intento"]) > ahora:
                return 0
            ultimo, intentos, error = int(cursor["ultimo_id"]), int(cursor["intentos"]), cursor["error"]
            siguiente: str | None = None
            filas = conn.execute(
                "SELECT * FROM eventos WHERE id > ? ORDER BY id LIMIT ?", (ultimo, self.config.lote)
            ).fetchall()
            for fila in filas:
                evento = _evento(fila)
                conn.execute("SAVEPOINT consumidor")
                try:
                    consumidor.procesar(self, evento, conn)
                except Exception as exc:
                    conn.execute("ROLLBACK TO consumidor")
                    conn.execute("RELEASE consumidor")
                    intentos += 1
                    error = f"{type(exc).__name__}: {exc}"[:500]
                    logger.warning(
                        "Consumer %s failed on event %s (attempt %d): %s", nombre, evento.id, intentos, error
                    )
                    if intentos < self.config.max_intentos:
                        siguiente = _iso(ahora + self.config.espera(intentos))
                        break
                    self._carta_muerta(conn, evento, f"{PREFIJO_CONSUMIDOR}{nombre}", intentos, error)
                else:
                    conn.execute("RELEASE consumidor")
                ultimo, intentos, error = evento.id, 0, None
                hechos += 1
            conn.execute(
                """
                UPDATE eventos_cursores SET ultimo_id = ?, intentos = ?, siguiente_intento = ?, error = ?,
                    actualizado = ? WHERE consumidor = ?
                """,
                (ultimo, intentos, siguiente, error, _iso(ahora), nombre),
            )
        return hechos

    def _carta_muerta(self, conn: sqlite3.Connection, evento: Evento, destino: str, intentos: int, error: str) -> None:
        ahora = _iso(self.ahora())
        conn.execute(
            """
            INSERT OR REPLACE INTO eventos_entregas
                (evento_id, tipo, modulo, case_id, estado, intentos, siguiente_intento, error, evento_creado, creado,
                 terminado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evento.id,
                evento.tipo,
                destino,
                evento.case_id or "",
                MUERTO,
                intentos,
                ahora,
                error,
                _iso(evento.created_at),
                ahora,
                ahora,
            ),
        )

    def cancelar(self, conn: sqlite3.Connection, case_id: str, modulo: str) -> None:
        """A module was switched off for a case: drop its timer and skip its pending deliveries."""
        conn.execute("DELETE FROM eventos_temporizadores WHERE case_id = ? AND modulo = ?", (case_id, modulo))
        conn.execute(
            "UPDATE eventos_entregas SET estado = ?, terminado = ?, error = ? "
            "WHERE case_id = ? AND modulo = ? AND estado = ?",
            (OMITIDO, _iso(self.ahora()), "módulo desactivado", case_id, modulo, PENDIENTE),
        )

    # ------------------------------------------------------------------ deliveries

    def reclamar(self, trabajador: str, limite: int | None = None) -> list[dict[str, Any]]:
        """Claim due deliveries for ``trabajador`` (a lease of ``arriendo_s``). Expired leases are taken over;
        one whose attempts are spent goes to the dead letters instead."""
        ahora = self.ahora()
        ahora_iso = _iso(ahora)
        with self._tx() as conn:
            vencidas = conn.execute(
                "SELECT id, intentos FROM eventos_entregas WHERE estado = ? AND bloqueado_hasta < ?",
                (EN_CURSO, ahora_iso),
            ).fetchall()
            for fila in vencidas:
                if int(fila["intentos"]) >= self.config.max_intentos:
                    conn.execute(
                        "UPDATE eventos_entregas SET estado = ?, terminado = ?, bloqueado_por = NULL, "
                        "error = COALESCE(error, 'el trabajador no terminó (arriendo vencido)') WHERE id = ?",
                        (MUERTO, ahora_iso, fila["id"]),
                    )
            filas = conn.execute(
                """
                SELECT * FROM eventos_entregas
                WHERE (estado = ? AND siguiente_intento <= ?) OR (estado = ? AND bloqueado_hasta < ?)
                ORDER BY id LIMIT ?
                """,
                (PENDIENTE, ahora_iso, EN_CURSO, ahora_iso, limite or self.config.lote),
            ).fetchall()
            hasta = _iso(ahora + timedelta(seconds=self.config.arriendo_s))
            for fila in filas:
                conn.execute(
                    "UPDATE eventos_entregas SET estado = ?, bloqueado_por = ?, bloqueado_hasta = ?, "
                    "intentos = intentos + 1 WHERE id = ?",
                    (EN_CURSO, trabajador, hasta, fila["id"]),
                )
        return [{**dict(f), "intentos": int(f["intentos"]) + 1} for f in filas]

    def _terminar(self, entrega: Mapping[str, Any], trabajador: str, **cambios: Any) -> bool:
        columnas = ", ".join(f"{k} = ?" for k in cambios)
        with self._tx() as conn:
            cursor = conn.execute(
                f"UPDATE eventos_entregas SET {columnas}, bloqueado_por = NULL, bloqueado_hasta = NULL "
                "WHERE id = ? AND estado = ? AND bloqueado_por = ?",
                (*cambios.values(), entrega["id"], EN_CURSO, trabajador),
            )
            return cursor.rowcount == 1

    def procesar_entrega(self, entrega: Mapping[str, Any], trabajador: str) -> str:
        """Run one claimed delivery. Returns its new state."""
        from hacienda_gpt.decision.schemas import CaseStatus
        from hacienda_gpt.eventos.manejadores import despertar_modulo

        inicio = time.perf_counter()
        ahora = self.ahora
        evento = self.evento(int(entrega["evento_id"]))
        motivo: str | None = None
        case = None
        modulo = None
        if evento is None:
            motivo = "el evento ya no existe"
        else:
            case = self.casos().get_case(entrega["case_id"])
            if case is None or case.status == CaseStatus.CLOSED:
                motivo = "el expediente no existe o está cerrado"
            else:
                modulo = next((m for m in self.modulos_de(case.case_id) if m.clave == entrega["modulo"]), None)
                if modulo is None:
                    motivo = "el módulo no está activado para este expediente"
        if evento is not None and case is None:
            # The case was erased meanwhile: nothing of it may stay in the bus either.
            with self._tx() as conn:
                borrar_caso_en(conn, str(entrega["case_id"]))
            return OMITIDO
        if motivo is not None or evento is None or case is None or modulo is None:
            self._terminar(entrega, trabajador, estado=OMITIDO, terminado=_iso(ahora()), error=motivo)
            return OMITIDO
        try:
            resultado = despertar_modulo(modulo, evento, case, hoy=self.dia(), store=self.notificaciones())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            intentos = int(entrega["intentos"])
            logger.warning(
                "Module %s failed on event %s for a case (attempt %d): %s", modulo.clave, evento.id, intentos, error
            )
            if intentos >= self.config.max_intentos:
                self._terminar(entrega, trabajador, estado=MUERTO, terminado=_iso(ahora()), error=error)
                return MUERTO
            siguiente = _iso(ahora() + self.config.espera(intentos))
            self._terminar(entrega, trabajador, estado=PENDIENTE, siguiente_intento=siguiente, error=error)
            return PENDIENTE
        fin = ahora()
        if self._terminar(
            entrega,
            trabajador,
            estado=HECHO,
            terminado=_iso(fin),
            error=None,
            avisos=resultado.avisos,
            duracion_ms=round((time.perf_counter() - inicio) * 1000, 3),
            latencia_ms=round((fin - _momento(str(entrega["evento_creado"]))).total_seconds() * 1000, 3),
        ):
            self.programar(case.case_id, modulo.clave, resultado.proxima_revision)
        return HECHO

    # ------------------------------------------------------------------ timers

    def programar(self, case_id: str, modulo: str, fecha: date | None) -> None:
        """Set (or clear) the next date ``modulo`` wants to be woken for ``case_id``: the latest answer wins."""
        with self._tx() as conn:
            if fecha is None:
                conn.execute("DELETE FROM eventos_temporizadores WHERE case_id = ? AND modulo = ?", (case_id, modulo))
                return
            conn.execute(
                """
                INSERT INTO eventos_temporizadores (case_id, modulo, despertar_el, programado) VALUES (?, ?, ?, ?)
                ON CONFLICT (case_id, modulo) DO UPDATE SET despertar_el = excluded.despertar_el,
                    programado = excluded.programado
                """,
                (case_id, modulo, fecha.isoformat(), _iso(self.ahora())),
            )

    def temporizadores(self) -> list[tuple[str, str, date]]:
        filas = self._leer("SELECT case_id, modulo, despertar_el FROM eventos_temporizadores ORDER BY despertar_el")
        return [(f["case_id"], f["modulo"], date.fromisoformat(f["despertar_el"])) for f in filas]

    def disparar_temporizadores(self, hoy: date | None = None) -> int:
        """Emit ``fecha.umbral`` for every timer due by ``hoy`` (once per timer and date) and clear it."""
        hoy = hoy or self.dia()
        emitidos = 0
        with self._tx() as conn:
            filas = conn.execute(
                "SELECT case_id, modulo, despertar_el FROM eventos_temporizadores WHERE despertar_el <= ?",
                (hoy.isoformat(),),
            ).fetchall()
            for fila in filas:
                clave = f"{FECHA_UMBRAL}:{fila['case_id']}:{fila['modulo']}:{fila['despertar_el']}"
                if (
                    emitir_en(
                        conn,
                        FECHA_UMBRAL,
                        case_id=fila["case_id"],
                        payload={"modulo": fila["modulo"], "fecha": fila["despertar_el"]},
                        clave=clave,
                        ahora=self.ahora(),
                    )
                    is not None
                ):
                    emitidos += 1
                conn.execute(
                    "DELETE FROM eventos_temporizadores WHERE case_id = ? AND modulo = ?",
                    (fila["case_id"], fila["modulo"]),
                )
        return emitidos

    def reconciliar(self, hoy: date | None = None) -> int:
        """Recompute every timer from the modules' cheap ``proxima_revision`` (daily safety net)."""
        from hacienda_gpt.eventos.manejadores import proxima_revision_de

        hoy = hoy or self.dia()
        with self._lock:
            casos = self.casos_abiertos(self._conn)
        programados = 0
        for case_id in casos:
            case = self.casos().get_case(case_id)
            if case is None:
                continue
            for modulo in self.modulos_de(case_id):
                try:
                    fecha = proxima_revision_de(modulo, case, hoy=hoy)
                except Exception:
                    logger.exception("Next review of module %s could not be computed", modulo.clave)
                    continue
                if fecha is not None:
                    self.programar(case_id, modulo.clave, fecha)
                    programados += 1
        return programados

    # ------------------------------------------------------------------ the loop

    def latido(self, trabajador: str, procesadas: int) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO eventos_trabajadores (nombre, latido, procesadas) VALUES (?, ?, ?)
                ON CONFLICT (nombre) DO UPDATE SET latido = excluded.latido,
                    procesadas = eventos_trabajadores.procesadas + excluded.procesadas
                """,
                (trabajador, _iso(self.ahora()), procesadas),
            )

    def ciclo(self, trabajador: str, *, max_entregas: int = 500) -> dict[str, int]:
        """One pass: due timers → consumers → deliveries until none is due. Returns what was done."""
        resumen = {"temporizadores": self.disparar_temporizadores(), "eventos": self.avanzar_consumidores()}
        estados: dict[str, int] = {}
        hechas = 0
        while hechas < max_entregas:
            lote = self.reclamar(trabajador)
            if not lote:
                break
            for entrega in lote:
                estado = self.procesar_entrega(entrega, trabajador)
                estados[estado] = estados.get(estado, 0) + 1
                hechas += 1
        self.latido(trabajador, hechas)
        return {**resumen, "entregas": hechas, **estados}

    def drenar(self, trabajador: str = "drenar", *, max_ciclos: int = 20) -> dict[str, int]:
        """Cycle until nothing is left that is due now (tests, the in-process dispatcher, ``--una-vez``)."""
        total: dict[str, int] = {}
        for _ in range(max_ciclos):
            resumen = self.ciclo(trabajador)
            for clave, valor in resumen.items():
                total[clave] = total.get(clave, 0) + valor
            if not (resumen["eventos"] or resumen["entregas"] or resumen["temporizadores"]):
                break
        return total

    # ------------------------------------------------------------------ operations

    def reintentar_muertas(self, ids: Sequence[int] | None = None) -> int:
        """Put dead module deliveries back in the queue (all, or the given ids).

        A consumer's dead letter (module ``consumidor:<name>``) is not re-queued: it stays as the record of
        an event that consumer skipped, until ``purgar(muertas=True)``.
        """
        filtro, params = ("", ()) if ids is None else (f" AND id IN ({','.join('?' * len(ids))})", tuple(ids))
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE eventos_entregas SET estado = ?, intentos = 0, siguiente_intento = ?, terminado = NULL "
                f"WHERE estado = ? AND modulo NOT LIKE ?{filtro}",
                (PENDIENTE, _iso(self.ahora()), MUERTO, f"{PREFIJO_CONSUMIDOR}%", *params),
            )
            return int(cursor.rowcount)

    def purgar(self, *, dias: int = 30, muertas: bool = False) -> dict[str, int]:
        """Forget finished deliveries and fully consumed events older than ``dias`` days."""
        limite = _iso(self.ahora() - timedelta(days=dias))
        estados = (HECHO, OMITIDO, MUERTO) if muertas else (HECHO, OMITIDO)
        with self._tx() as conn:
            entregas = conn.execute(
                f"DELETE FROM eventos_entregas WHERE estado IN ({','.join('?' * len(estados))}) AND terminado < ?",
                (*estados, limite),
            ).rowcount
            minimo = conn.execute("SELECT MIN(ultimo_id) FROM eventos_cursores").fetchone()[0] or 0
            eventos = conn.execute(
                """
                DELETE FROM eventos WHERE id <= ? AND created_at < ?
                  AND id NOT IN (SELECT evento_id FROM eventos_entregas)
                """,
                (minimo, limite),
            ).rowcount
        return {"entregas": int(entregas), "eventos": int(eventos)}

    def salud(self) -> dict[str, Any]:
        with self._lock:
            return salud_de(self._conn, ahora=self.ahora(), consumidores=[c.nombre for c in self.consumidores])

    def metricas(self) -> list[dict[str, Any]]:
        with self._lock:
            return metricas_de(self._conn)


# --------------------------------------------------------------------------- #
# Health and metrics (on any connection: the doctor opens the file read-only)
# --------------------------------------------------------------------------- #


def _percentil(valores: Sequence[float], p: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, round(p * (len(ordenados) - 1))))
    return round(ordenados[indice], 1)


def metricas_de(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per event type and module: deliveries by state, retries, and latency (event → done) / run time."""
    if not hay_tablas(conn):
        return []
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for fila in conn.execute(
        "SELECT tipo, modulo, estado, intentos, latencia_ms, duracion_ms FROM eventos_entregas"
    ).fetchall():
        g = grupos.setdefault(
            (fila[0], fila[1]),
            {"tipo": fila[0], "modulo": fila[1], "total": 0, "reintentos": 0, "_lat": [], "_dur": []},
        )
        g["total"] += 1
        g[fila[2]] = g.get(fila[2], 0) + 1
        g["reintentos"] += max(0, int(fila[3]) - 1)
        if fila[4] is not None:
            g["_lat"].append(float(fila[4]))
        if fila[5] is not None:
            g["_dur"].append(float(fila[5]))
    salida = []
    for g in sorted(grupos.values(), key=lambda x: (x["tipo"], x["modulo"])):
        lat, dur = g.pop("_lat"), g.pop("_dur")
        g["fallos"] = g["reintentos"] + g.get(MUERTO, 0)
        g["latencia_ms_p50"] = _percentil(lat, 0.5)
        g["latencia_ms_p95"] = _percentil(lat, 0.95)
        g["duracion_ms_p50"] = _percentil(dur, 0.5)
        g["duracion_ms_max"] = round(max(dur), 1) if dur else None
        salida.append(g)
    return salida


def salud_de(
    conn: sqlite3.Connection, *, ahora: datetime, consumidores: Sequence[str] = ("modulos",)
) -> dict[str, Any]:
    """Backlog, dead letters, timers and workers. Aggregates only: no case id leaves here."""
    if not hay_tablas(conn):
        return {"instalado": False}
    ultimo = conn.execute("SELECT COALESCE(MAX(id), 0) FROM eventos").fetchone()[0]
    cursores = {f["consumidor"]: f for f in conn.execute("SELECT * FROM eventos_cursores").fetchall()}
    sin_repartir: dict[str, int] = {}
    atraso_reparto = 0.0
    for nombre in consumidores:
        cursor = cursores.get(nombre)
        desde = int(cursor["ultimo_id"]) if cursor is not None else 0
        sin_repartir[nombre] = int(ultimo) - desde
        primero = conn.execute("SELECT MIN(created_at) FROM eventos WHERE id > ?", (desde,)).fetchone()[0]
        if primero:
            atraso_reparto = max(atraso_reparto, (ahora - _momento(primero)).total_seconds())
    por_estado = dict(conn.execute("SELECT estado, COUNT(*) FROM eventos_entregas GROUP BY estado").fetchall())
    mas_antigua = conn.execute(
        "SELECT MIN(evento_creado) FROM eventos_entregas WHERE estado IN (?, ?) AND siguiente_intento <= ?",
        (PENDIENTE, EN_CURSO, _iso(ahora)),
    ).fetchone()[0]
    atraso_entregas = (ahora - _momento(mas_antigua)).total_seconds() if mas_antigua else 0.0
    proximo = conn.execute("SELECT MIN(despertar_el) FROM eventos_temporizadores").fetchone()[0]
    return {
        "instalado": True,
        "eventos": int(ultimo),
        "sin_repartir": sin_repartir,
        "entregas": {estado: int(n) for estado, n in por_estado.items()},
        "pendientes": int(por_estado.get(PENDIENTE, 0)) + int(por_estado.get(EN_CURSO, 0)),
        "muertas": int(por_estado.get(MUERTO, 0)),
        "atraso_s": round(max(atraso_reparto, atraso_entregas), 1),
        "consumidores_con_error": sorted(n for n, c in cursores.items() if c["error"]),
        "temporizadores": int(conn.execute("SELECT COUNT(*) FROM eventos_temporizadores").fetchone()[0]),
        "proximo_temporizador": proximo,
        "trabajadores": [
            {"nombre": f["nombre"], "latido": f["latido"], "procesadas": int(f["procesadas"])}
            for f in conn.execute("SELECT * FROM eventos_trabajadores ORDER BY latido DESC").fetchall()
        ],
    }


def borrar_caso_en(conn: sqlite3.Connection, case_id: str) -> None:
    """Erase every trace of a case from the bus (right to erasure), on the caller's transaction."""
    if not hay_tablas(conn):
        return
    conn.execute("DELETE FROM eventos_entregas WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM eventos_temporizadores WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM eventos WHERE case_id = ?", (case_id,))


__all__ = [
    "EN_CURSO",
    "HECHO",
    "MUERTO",
    "OMITIDO",
    "PENDIENTE",
    "Bus",
    "Configuracion",
    "Consumidor",
    "RepartoAModulos",
    "borrar_caso_en",
    "emitir_en",
    "metricas_de",
    "salud_de",
]

"""Browser tasks in the cases' database, so a restart does not lose them and several processes agree.

The process that RUNS a task (the one that started its thread) owns it: it keeps the task in memory with
what must never be at rest — the latest screenshot, the summary of the values that would be sent, the
handed-over session — and mirrors everything else to ``navegador_tareas`` every second (state, steps,
messages, its heartbeat). Any other process reads the row: the API answers from whichever process the
request reaches, and «Detener» / «Cerrar» from another process become requests (``detener`` / ``cerrar``
columns) that the owner obeys on its next heartbeat.

* A task is claimed with ``BEGIN IMMEDIATE``: the «already started», «one task per user» and «N tasks at
  once in the whole service» checks and the 24-hour count (``navegador_inicios``) see every process.
* A prepared task (no browser yet) keeps its parameters in the row, so any process can start it; they are
  dropped when it starts, is cancelled or expires (90 minutes), and go with the case (trigger).
* A task whose owner stopped beating for :data:`LATIDO_CADUCA_S` (the service restarted) is closed as
  ``fallida`` with a message saying nothing was sent, and the end is written to the case's audit trail.

The schema is the ``navegador`` owner of the migrations (``plataforma/migraciones/catalogo/navegador.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import threading
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.sqlite import abrir_sqlite

if TYPE_CHECKING:
    from asistente.capacidades.navegador.tareas import Tarea

logger = logging.getLogger(__name__)

# The owner writes its tasks this often; a task whose owner has been silent this long is an orphan.
LATIDO_S = 1.0
LATIDO_CADUCA_S = 60.0
MENSAJE_HUERFANA = (
    "El servicio se reinició mientras la tarea estaba en marcha: el navegador se cerró y no se ha enviado nada. "
    "Prepara la tarea de nuevo si la necesitas."
)
ACTIVOS = ("en_curso", "esperando_login", "trabajando")


def identidad_de_proceso() -> str:
    """A name for this process that another process (or this one after a restart) never reuses."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def huella_usuario(clave_usuario: str) -> str:
    """The rate-limit key as stored (a user id or a case id never appears in clear in ``navegador_inicios``)."""
    return hashlib.sha256(clave_usuario.encode("utf-8")).hexdigest()


def _iso(momento: datetime | None) -> str | None:
    return momento.isoformat() if momento is not None else None


def _fecha(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def _parametros_a_texto(parametros: dict[str, Any]) -> str:
    return json.dumps(
        {k: (v.isoformat() if isinstance(v, date) else v) for k, v in parametros.items()}, ensure_ascii=False
    )


@dataclass(frozen=True)
class Reclamacion:
    """The outcome of :meth:`AlmacenTareas.reclamar`: ``None`` error means the task is now ours."""

    error: str | None = None
    codigo: int = 409
    parametros: dict[str, Any] | None = None


class _LimiteAlcanzadoError(Exception):
    """Internal: a check of :meth:`AlmacenTareas.reclamar` failed (rolls the claim back)."""

    def __init__(self, mensaje: str, codigo: int) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


class AlmacenTareas:
    """``navegador_tareas`` / ``navegador_inicios`` of one database file. Thread-safe."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def abierto(self) -> bool:
        return self._conn is not None

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = abrir_sqlite(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None, filas=True)
            # A prepared task's parameters are overwritten in the file when they are dropped.
            conn.execute("PRAGMA secure_delete=ON;")
            asegurar_esquema(conn, "navegador")
            self._conn = conn
        return self._conn

    @contextmanager
    def _transaccion(self) -> Generator[sqlite3.Connection]:
        with self._lock:
            conn = self._get()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def _leer(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._get().execute(sql, args).fetchall()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # --- rows <-> tasks ------------------------------------------------------

    @staticmethod
    def _payload(tarea: Tarea) -> str:
        with tarea.lock:
            datos = {
                "version": tarea.version,
                "titulo": tarea.titulo,
                "personales": sorted(tarea.personales),
                "modo": tarea.modo,
                "eventos": [
                    {"momento": e.momento.isoformat(), "tipo": e.tipo, "texto": e.texto, "paso": e.paso}
                    for e in tarea.eventos
                ],
                "pasos": [dict(p) for p in tarea.pasos],
                "mensaje": tarea.mensaje,
                "importacion": tarea.importacion,
                "importacion_error": tarea.importacion_error,
                "accion_id": tarea.accion_id,
                "bloqueadas": list(tarea.bloqueadas),
            }
        return json.dumps(datos, ensure_ascii=False)

    @staticmethod
    def a_tarea(fila: sqlite3.Row) -> Tarea:
        """A task as another process sees it: everything but the screenshot, the summary and the session."""
        from asistente.capacidades.navegador.guion import GuionError, guion_activo, validar_parametros
        from asistente.capacidades.navegador.tareas import EventoTarea, Tarea

        datos = json.loads(fila["payload_json"])
        parametros: dict[str, Any] = {}
        if fila["parametros_json"]:
            try:
                parametros = validar_parametros(guion_activo(fila["clave"]).reglas, json.loads(fila["parametros_json"]))
            except GuionError, ValueError:
                parametros = {}
        return Tarea(
            id=fila["id"],
            case_id=fila["case_id"],
            clave=fila["clave"],
            version=int(datos["version"]),
            titulo=datos["titulo"],
            parametros=parametros,
            personales=frozenset(datos.get("personales", [])),
            user_id=fila["user_id"],
            modo=datos.get("modo"),
            estado=fila["estado"],
            creada=datetime.fromisoformat(fila["creada"]),
            actualizada=datetime.fromisoformat(fila["actualizada"]),
            terminada=_fecha(fila["terminada"]),
            eventos=[
                EventoTarea(datetime.fromisoformat(e["momento"]), e["tipo"], e["texto"], e.get("paso"))
                for e in datos.get("eventos", [])
            ],
            pasos=[dict(p) for p in datos.get("pasos", [])],
            mensaje=datos.get("mensaje"),
            importacion=datos.get("importacion"),
            importacion_error=bool(datos.get("importacion_error")),
            accion_id=datos.get("accion_id"),
            bloqueadas=list(datos.get("bloqueadas", [])),
        )

    # --- writes ------------------------------------------------------------

    def insertar_preparada(self, tarea: Tarea) -> None:
        """A new prepared task, with its validated parameters (so any process can start it)."""
        with self._transaccion() as conn:
            conn.execute(
                "INSERT INTO navegador_tareas (id, case_id, user_id, clave, estado, creada, actualizada, "
                "parametros_json, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tarea.id,
                    tarea.case_id,
                    tarea.user_id,
                    tarea.clave,
                    tarea.estado,
                    tarea.creada.isoformat(),
                    tarea.actualizada.isoformat(),
                    _parametros_a_texto(tarea.parametros),
                    self._payload(tarea),
                ),
            )

    def guardar(self, tarea: Tarea, propietario: str | None) -> None:
        """Mirror the owner's task (state, steps, messages) and beat its heart. Parameters are never written."""
        with tarea.lock:
            estado, actualizada, terminada = tarea.estado, tarea.actualizada, tarea.terminada
        ahora = datetime.now(UTC).isoformat()
        with self._transaccion() as conn:
            conn.execute(
                "UPDATE navegador_tareas SET estado = ?, user_id = ?, actualizada = ?, terminada = ?, "
                "propietario = COALESCE(?, propietario), latido = CASE WHEN ? IS NULL THEN latido ELSE ? END, "
                "parametros_json = CASE WHEN ? = 'preparada' THEN parametros_json ELSE NULL END, payload_json = ? "
                "WHERE id = ?",
                (
                    estado,
                    tarea.user_id,
                    actualizada.isoformat(),
                    _iso(terminada),
                    propietario,
                    propietario,
                    ahora,
                    estado,
                    self._payload(tarea),
                    tarea.id,
                ),
            )

    def reclamar(
        self,
        tarea_id: str,
        *,
        propietario: str,
        user_id: str | None,
        modo: str,
        clave_usuario: str,
        clave_de: Any,
        max_simultaneas: int,
        max_dia: int,
        ahora: float,
        ventana_s: float,
    ) -> Reclamacion:
        """Start ``tarea_id`` for this process if every service-wide limit allows it (atomic)."""
        limite_latido = (datetime.now(UTC) - timedelta(seconds=LATIDO_CADUCA_S)).isoformat()
        huella = huella_usuario(clave_usuario)
        try:
            with self._transaccion() as conn:
                fila = conn.execute(
                    "SELECT estado, case_id, parametros_json, payload_json FROM navegador_tareas WHERE id = ?",
                    (tarea_id,),
                ).fetchone()
                if fila is None:
                    raise _LimiteAlcanzadoError("Esa tarea ya no existe: prepárala de nuevo.", 404)
                if fila["estado"] != "preparada":
                    raise _LimiteAlcanzadoError("Esta tarea ya se empezó; prepara otra.", 409)
                activas = conn.execute(
                    "SELECT id, case_id, user_id FROM navegador_tareas WHERE estado IN (?, ?, ?) "
                    "AND id != ? AND latido IS NOT NULL AND latido >= ?",
                    (*ACTIVOS, tarea_id, limite_latido),
                ).fetchall()
                if any(clave_de(a["user_id"], a["case_id"]) == clave_usuario for a in activas):
                    raise _LimiteAlcanzadoError(
                        "Ya tienes una tarea del navegador en curso: termina o detén esa antes.", 429
                    )
                if len(activas) >= max_simultaneas:
                    raise _LimiteAlcanzadoError(
                        "El servicio ya está ejecutando el máximo de tareas a la vez; prueba en un rato.", 429
                    )
                usadas = conn.execute(
                    "SELECT COUNT(*) FROM navegador_inicios WHERE clave_usuario = ? AND momento > ?",
                    (huella, ahora - ventana_s),
                ).fetchone()[0]
                if usadas >= max_dia:
                    raise _LimiteAlcanzadoError(
                        f"Has alcanzado el máximo de {max_dia} tareas del navegador en 24 horas.", 429
                    )
                conn.execute(
                    "INSERT INTO navegador_inicios (clave_usuario, momento, tarea_id, case_id) VALUES (?, ?, ?, ?)",
                    (huella, ahora, tarea_id, fila["case_id"]),
                )
                datos = json.loads(fila["payload_json"])
                datos["modo"] = modo
                conn.execute(
                    "UPDATE navegador_tareas SET estado = 'en_curso', user_id = ?, propietario = ?, latido = ?, "
                    "actualizada = ?, parametros_json = NULL, payload_json = ? WHERE id = ?",
                    (
                        user_id,
                        propietario,
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                        json.dumps(datos, ensure_ascii=False),
                        tarea_id,
                    ),
                )
                crudos = json.loads(fila["parametros_json"]) if fila["parametros_json"] else {}
        except _LimiteAlcanzadoError as exc:
            return Reclamacion(error=str(exc), codigo=exc.codigo)
        return Reclamacion(parametros=crudos)

    def pedir(self, tarea_id: str, *, detener: bool = False, cerrar: bool = False) -> None:
        """Ask the owner of a running task to stop / close it (it obeys on its next heartbeat)."""
        with self._transaccion() as conn:
            conn.execute(
                "UPDATE navegador_tareas SET detener = MAX(detener, ?), cerrar = MAX(cerrar, ?) WHERE id = ?",
                (int(detener or cerrar), int(cerrar), tarea_id),
            )

    def solicitudes(self, tarea_id: str) -> tuple[bool, bool]:
        filas = self._leer("SELECT detener, cerrar FROM navegador_tareas WHERE id = ?", (tarea_id,))
        if not filas:
            return False, False
        return bool(filas[0]["detener"]), bool(filas[0]["cerrar"])

    def terminar_sin_propietario(self, tarea_id: str, *, estado: str, cerrar: bool, mensaje: str | None) -> None:
        """Stop / close a task nobody is running (prepared, finished, or an orphan). Drops its parameters."""
        ahora = datetime.now(UTC).isoformat()
        with self._transaccion() as conn:
            fila = conn.execute("SELECT payload_json FROM navegador_tareas WHERE id = ?", (tarea_id,)).fetchone()
            if fila is None:
                return
            datos = json.loads(fila["payload_json"])
            if mensaje is not None:
                datos["mensaje"] = mensaje
            conn.execute(
                "UPDATE navegador_tareas SET estado = ?, terminada = COALESCE(terminada, ?), actualizada = ?, "
                "parametros_json = NULL, cerrar = MAX(cerrar, ?), payload_json = ? WHERE id = ?",
                (estado, ahora, ahora, int(cerrar), json.dumps(datos, ensure_ascii=False), tarea_id),
            )

    # --- reads -------------------------------------------------------------

    def leer(self, tarea_id: str) -> sqlite3.Row | None:
        filas = self._leer("SELECT * FROM navegador_tareas WHERE id = ?", (tarea_id,))
        return filas[0] if filas else None

    def de_caso(self, case_id: str) -> list[sqlite3.Row]:
        return self._leer("SELECT * FROM navegador_tareas WHERE case_id = ? ORDER BY creada DESC", (case_id,))

    def usadas(self, clave_usuario: str, *, ahora: float, ventana_s: float) -> int:
        filas = self._leer(
            "SELECT COUNT(*) AS n FROM navegador_inicios WHERE clave_usuario = ? AND momento > ?",
            (huella_usuario(clave_usuario), ahora - ventana_s),
        )
        return int(filas[0]["n"])

    # --- housekeeping --------------------------------------------------------

    def huerfanas(self, propietario: str) -> list[sqlite3.Row]:
        """Running tasks of OTHER processes whose heartbeat stopped."""
        limite = (datetime.now(UTC) - timedelta(seconds=LATIDO_CADUCA_S)).isoformat()
        return self._leer(
            "SELECT * FROM navegador_tareas WHERE estado IN (?, ?, ?) AND (propietario IS NULL OR propietario != ?) "
            "AND (latido IS NULL OR latido < ?)",
            (*ACTIVOS, propietario, limite),
        )

    def purgar(self, *, terminadas_antes: datetime, preparadas_antes: datetime, inicios_antes: float) -> None:
        with self._transaccion() as conn:
            conn.execute(
                "DELETE FROM navegador_tareas WHERE terminada IS NOT NULL AND terminada < ?",
                (terminadas_antes.isoformat(),),
            )
            conn.execute(
                "DELETE FROM navegador_tareas WHERE estado = 'preparada' AND creada < ?",
                (preparadas_antes.isoformat(),),
            )
            conn.execute("DELETE FROM navegador_inicios WHERE momento < ?", (inicios_antes,))


def exportar(db_path: str, case_id: str) -> list[dict[str, Any]]:
    """The browser tasks of a case for ``GET /me/export`` (access / portability): state, steps and messages, and
    the parameters the user typed while a task is still ``preparada`` (the only time they are kept at rest).

    Read-only: nothing is created when the database or the table does not exist. Never a screenshot, a session
    or a credential, because none is ever stored.
    """
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        existe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'navegador_tareas'"
        ).fetchone()
        if existe is None:
            return []
        filas = conn.execute(
            "SELECT id, clave, estado, creada, actualizada, terminada, parametros_json, payload_json "
            "FROM navegador_tareas WHERE case_id = ? ORDER BY creada",
            (case_id,),
        ).fetchall()
    finally:
        conn.close()
    salida: list[dict[str, Any]] = []
    for fila in filas:
        try:
            parametros = json.loads(fila["parametros_json"]) if fila["parametros_json"] else None
            contenido = json.loads(fila["payload_json"]) if fila["payload_json"] else {}
        except ValueError:  # exported as stored rather than dropped
            parametros, contenido = fila["parametros_json"], fila["payload_json"]
        salida.append(
            {
                "id": fila["id"],
                "guion": fila["clave"],
                "estado": fila["estado"],
                "creada": fila["creada"],
                "actualizada": fila["actualizada"],
                "terminada": fila["terminada"],
                "parametros": parametros,
                "contenido": contenido,
            }
        )
    return salida


__all__ = [
    "LATIDO_CADUCA_S",
    "LATIDO_S",
    "MENSAJE_HUERFANA",
    "AlmacenTareas",
    "Reclamacion",
    "exportar",
    "huella_usuario",
    "identidad_de_proceso",
]

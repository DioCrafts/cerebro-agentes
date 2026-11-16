"""The process model: an ``api`` process (uvicorn) and a ``worker`` process, over the same cases' database.

* **api** — ``uvicorn hacienda_gpt.api.api:app``: answers requests, writes events. With
  ``EVENTOS_DESPACHO_EN_API=true`` (the default, as before) it also drains the event bus itself right after a
  change, for latency; with ``false`` only the worker dispatches. Browser tasks run in the API process that
  started them (the user's login window or handed-over session lives there) and are shared with every other
  process through the database (``navegador/persistencia.py``).
* **worker** — ``python -m hacienda_gpt.cli.plataforma worker``: the long-running **services** (the event bus
  worker; anything another package registers, e.g. a task engine) and the **scheduled jobs** that used to be
  cron lines (daily run, weekly digest, BOE watch, index updates, Gmail sync…). Every scheduled slot is
  claimed in ``plataforma_trabajos`` first, so two workers never run the same slot twice; a slot missed while
  no worker ran still runs if the worker is back within ``PLATAFORMA_RECUPERAR_HORAS``. Jobs run as child
  processes through ``ops run <nombre> --`` (the watchdog state ``ops doctor`` reads and the healthcheck ping
  stay exactly as with cron), so a crashing job never takes the worker down.

Registering more (other packages)::

    def registrar_en_plataforma(registro: Registro) -> None:      # in e.g. hacienda_gpt/tareas/__init__.py
        registro.registrar_servicio(Servicio("tareas", "Motor de tareas largas", arrancar=mi_bucle))
        registro.registrar_trabajo(Trabajo("tareas-purga", "…", Programacion(hora=time(3, 10)), funcion=purgar))

The worker imports :data:`EXTENSIONES` (and ``PLATAFORMA_EXTENSIONES``) and calls that function when present.
A service's ``arrancar(contexto)`` runs in its own thread until ``contexto.parar`` is set.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import importlib
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import threading
from typing import Any
from zoneinfo import ZoneInfo

from hacienda_gpt import settings
from hacienda_gpt.plataforma.migraciones import BASE_CASOS, asegurar_esquema, estado, migrar

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("Europe/Madrid")
# Packages the worker asks for services and jobs (``registrar_en_plataforma``); missing ones are skipped.
EXTENSIONES: tuple[str, ...] = ("hacienda_gpt.tareas",)
# A job's slot is considered abandoned (another worker may take it) after this long without finishing.
TIEMPO_MAXIMO_TRABAJO = timedelta(hours=6)


def despacho_en_api() -> bool:
    """Whether the API process dispatches events itself (both settings must allow it)."""
    return bool(getattr(settings, "EVENTOS_DESPACHO_EN_PROCESO", True)) and bool(
        getattr(settings, "EVENTOS_DESPACHO_EN_API", True)
    )


# --------------------------------------------------------------------------- #
# Schedules, jobs, services
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Programacion:
    """When a job runs, in Spanish time: at ``hora`` (on some weekdays / days of the month), or every N minutes."""

    hora: time | None = None
    # 0 = Monday … 6 = Sunday; empty = every day.
    dias_semana: frozenset[int] = frozenset()
    dias_mes: frozenset[int] = frozenset()
    cada_minutos: int | None = None

    def __post_init__(self) -> None:
        if (self.hora is None) == (self.cada_minutos is None):
            raise ValueError("Una programación lleva una hora o un intervalo en minutos (y solo uno).")
        if self.cada_minutos is not None and not 1 <= self.cada_minutos <= 24 * 60:
            raise ValueError("El intervalo va de 1 a 1440 minutos.")

    def _toca(self, dia: date) -> bool:
        return (not self.dias_semana or dia.weekday() in self.dias_semana) and (
            not self.dias_mes or dia.day in self.dias_mes
        )

    def ultimo_turno(self, ahora: datetime) -> datetime | None:
        """The latest slot at or before ``ahora`` (aware), in Spanish time; None if none in the last 62 days."""
        local = ahora.astimezone(ZONA)
        if self.cada_minutos is not None:
            medianoche = datetime.combine(local.date(), time(0), tzinfo=ZONA)
            minutos = int((local - medianoche).total_seconds() // 60)
            return medianoche + timedelta(minutes=minutos - minutos % self.cada_minutos)
        assert self.hora is not None
        for atras in range(0, 62):
            dia = local.date() - timedelta(days=atras)
            turno = datetime.combine(dia, self.hora, tzinfo=ZONA)
            if self._toca(dia) and turno <= local:
                return turno
        return None

    def siguiente_turno(self, ahora: datetime) -> datetime | None:
        """The first slot strictly after ``ahora``."""
        local = ahora.astimezone(ZONA)
        if self.cada_minutos is not None:
            ultimo = self.ultimo_turno(ahora)
            assert ultimo is not None
            return ultimo + timedelta(minutes=self.cada_minutos)
        assert self.hora is not None
        for adelante in range(0, 62):
            dia = local.date() + timedelta(days=adelante)
            turno = datetime.combine(dia, self.hora, tzinfo=ZONA)
            if self._toca(dia) and turno > local:
                return turno
        return None

    def describir(self) -> str:
        if self.cada_minutos is not None:
            return f"cada {self.cada_minutos} min"
        assert self.hora is not None
        texto = f"a las {self.hora:%H:%M}"
        if self.dias_semana:
            nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
            texto += " los " + ", ".join(nombres[d] for d in sorted(self.dias_semana))
        if self.dias_mes:
            texto += " los días " + ", ".join(str(d) for d in sorted(self.dias_mes))
        return texto + " (hora peninsular)"


@dataclass(frozen=True)
class Trabajo:
    """A scheduled job. ``orden``: ``python -m`` arguments, run as a child process (through ``ops run`` when
    ``vigilado``); or ``funcion``: called in a thread of the worker (returns an exit code)."""

    nombre: str
    descripcion: str
    programacion: Programacion
    orden: tuple[str, ...] = ()
    funcion: Callable[[], int] | None = None
    por_defecto: bool = True
    vigilado: bool = True

    def __post_init__(self) -> None:
        if bool(self.orden) == (self.funcion is not None):
            raise ValueError(f"{self.nombre}: un trabajo lleva una orden o una función (y solo una).")

    def argv(self) -> list[str]:
        """The command line of a child-process job (the same as its documented cron line)."""
        orden = [sys.executable, "-m", *self.orden]
        if not self.vigilado:
            return orden
        return [sys.executable, "-m", "hacienda_gpt.cli.ops", "run", self.nombre, "--", *orden]


@dataclass
class ContextoServicio:
    """What a running service gets: the database, the worker's name, and the event that stops it."""

    db_path: str
    nombre_proceso: str
    parar: threading.Event


@dataclass(frozen=True)
class Servicio:
    """A long-running loop of the worker: ``arrancar(contexto)`` returns when ``contexto.parar`` is set."""

    nombre: str
    descripcion: str
    arrancar: Callable[[ContextoServicio], None]
    por_defecto: bool = True


class Registro:
    """The worker's services and jobs."""

    def __init__(self) -> None:
        self.trabajos: dict[str, Trabajo] = {}
        self.servicios: dict[str, Servicio] = {}
        self.extensiones: dict[str, str] = {}

    def registrar_trabajo(self, trabajo: Trabajo, *, reemplazar: bool = False) -> None:
        if trabajo.nombre in self.trabajos and not reemplazar:
            raise ValueError(f"Ya hay un trabajo llamado {trabajo.nombre!r}")
        self.trabajos[trabajo.nombre] = trabajo

    def registrar_servicio(self, servicio: Servicio, *, reemplazar: bool = False) -> None:
        if servicio.nombre in self.servicios and not reemplazar:
            raise ValueError(f"Ya hay un servicio llamado {servicio.nombre!r}")
        self.servicios[servicio.nombre] = servicio

    def trabajos_activos(self) -> list[Trabajo]:
        """The jobs this worker runs, per ``PLATAFORMA_TRABAJOS`` / ``PLATAFORMA_TRABAJOS_DESACTIVADOS``."""
        elegidos = str(getattr(settings, "PLATAFORMA_TRABAJOS", "") or "").strip().lower()
        fuera = _lista(getattr(settings, "PLATAFORMA_TRABAJOS_DESACTIVADOS", ""))
        if elegidos in {"ninguno", "none", "-"}:
            return []
        if elegidos in {"", "defecto"}:
            nombres = [t.nombre for t in self.trabajos.values() if t.por_defecto]
        elif elegidos in {"todos", "all", "*"}:
            nombres = list(self.trabajos)
        else:
            nombres = [n for n in _lista(elegidos) if n in self.trabajos]
            for desconocido in sorted(set(_lista(elegidos)) - set(self.trabajos)):
                logger.warning("PLATAFORMA_TRABAJOS names an unknown job: %s", desconocido)
        return [self.trabajos[n] for n in nombres if n not in fuera]

    def servicios_activos(self) -> list[Servicio]:
        return [s for s in self.servicios.values() if s.por_defecto]


def _lista(texto: Any) -> list[str]:
    return [p.strip().lower() for p in str(texto or "").split(",") if p.strip()]


def _cargar_extensiones(registro_: Registro, modulos: Iterable[str]) -> None:
    for nombre in modulos:
        try:
            modulo = importlib.import_module(nombre)
        except ModuleNotFoundError as exc:
            if exc.name and (nombre == exc.name or nombre.startswith(exc.name + ".")):
                registro_.extensiones[nombre] = "no instalado"
                continue
            logger.exception("Platform extension %s failed to import", nombre)
            registro_.extensiones[nombre] = f"error: {type(exc).__name__}"
            continue
        except Exception as exc:  # an extension must never stop the worker from starting
            logger.exception("Platform extension %s failed to import", nombre)
            registro_.extensiones[nombre] = f"error: {type(exc).__name__}"
            continue
        registrar_fn = getattr(modulo, "registrar_en_plataforma", None)
        if not callable(registrar_fn):
            registro_.extensiones[nombre] = "sin registrar_en_plataforma"
            continue
        try:
            registrar_fn(registro_)
            registro_.extensiones[nombre] = "cargada"
        except Exception as exc:
            logger.exception("Platform extension %s failed to register", nombre)
            registro_.extensiones[nombre] = f"error: {type(exc).__name__}"


_registro: list[Registro] = []
_registro_lock = threading.Lock()


def registro() -> Registro:
    """The process-wide registry: the built-in services and jobs, plus the extensions'."""
    with _registro_lock:
        if not _registro:
            from hacienda_gpt.plataforma.trabajos import registrar_incorporados

            nuevo = Registro()
            registrar_incorporados(nuevo)
            _cargar_extensiones(nuevo, (*EXTENSIONES, *_lista_modulos(getattr(settings, "PLATAFORMA_EXTENSIONES", ""))))
            _registro.append(nuevo)
        return _registro[0]


def _lista_modulos(texto: Any) -> list[str]:
    return [p.strip() for p in str(texto or "").split(",") if p.strip()]


def reiniciar_registro(nuevo: Registro | None = None) -> None:
    """Forget the registry (tests), or install ``nuevo``."""
    with _registro_lock:
        _registro.clear()
        if nuevo is not None:
            _registro.append(nuevo)


# --------------------------------------------------------------------------- #
# Shared state in the database: heartbeats and claimed slots
# --------------------------------------------------------------------------- #


def _conectar(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    asegurar_esquema(conn, "plataforma")
    return conn


def _iso(momento: datetime) -> str:
    return momento.astimezone(UTC).isoformat()


class EstadoCompartido:
    """``plataforma_procesos`` and ``plataforma_trabajos`` of one database. Thread-safe."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = _conectar(db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def latir(self, nombre: str, rol: str, iniciado: datetime, detalle: dict[str, Any], ahora: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO plataforma_procesos (nombre, rol, equipo, pid, iniciado, latido, detalle_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(nombre) DO UPDATE SET latido = excluded.latido, "
                "detalle_json = excluded.detalle_json",
                (
                    nombre,
                    rol,
                    socket.gethostname(),
                    os.getpid(),
                    _iso(iniciado),
                    _iso(ahora),
                    json.dumps(detalle, ensure_ascii=False, default=str),
                ),
            )

    def despedir(self, nombre: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM plataforma_procesos WHERE nombre = ?", (nombre,))

    def reclamar(self, trabajo: str, turno: datetime, propietario: str, ahora: datetime) -> bool:
        """Take slot ``turno`` of ``trabajo`` for ``propietario``. False if it (or a later one) was taken, or the
        previous run is still going (and not abandoned)."""
        abandonado = _iso(ahora - TIEMPO_MAXIMO_TRABAJO)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("INSERT OR IGNORE INTO plataforma_trabajos (nombre) VALUES (?)", (trabajo,))
                cursor = self._conn.execute(
                    "UPDATE plataforma_trabajos SET turno = ?, propietario = ?, inicio = ?, fin = NULL, codigo = NULL, "
                    "error = NULL WHERE nombre = ? AND (turno IS NULL OR turno < ?) "
                    "AND (inicio IS NULL OR fin IS NOT NULL OR inicio < ?)",
                    (_iso(turno), propietario, _iso(ahora), trabajo, _iso(turno), abandonado),
                )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return cursor.rowcount == 1

    def terminar(self, trabajo: str, propietario: str, codigo: int, error: str | None, ahora: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE plataforma_trabajos SET fin = ?, codigo = ?, error = ? WHERE nombre = ? AND propietario = ?",
                (_iso(ahora), codigo, error, trabajo, propietario),
            )

    def trabajos(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(f) for f in self._conn.execute("SELECT * FROM plataforma_trabajos ORDER BY nombre")]

    def procesos(self) -> list[dict[str, Any]]:
        with self._lock:
            filas = self._conn.execute("SELECT * FROM plataforma_procesos ORDER BY rol, nombre").fetchall()
        return [{**dict(f), "detalle_json": json.loads(f["detalle_json"] or "{}")} for f in filas]


# --------------------------------------------------------------------------- #
# The scheduler
# --------------------------------------------------------------------------- #


Ejecutor = Callable[[Trabajo], int]


def ejecutar_trabajo(trabajo: Trabajo) -> int:
    """Run one job to the end and return its exit code (never raises)."""
    try:
        if trabajo.funcion is not None:
            return int(trabajo.funcion())
        return subprocess.run(trabajo.argv(), check=False).returncode
    except Exception:
        logger.exception("Scheduled job %s failed to run", trabajo.nombre)
        return 1


class Planificador:
    """Runs each active job once per slot, across every worker sharing the database."""

    def __init__(
        self,
        estado_: EstadoCompartido,
        trabajos: Iterable[Trabajo],
        *,
        propietario: str,
        ejecutar: Ejecutor = ejecutar_trabajo,
        recuperar: timedelta | None = None,
    ) -> None:
        self.estado = estado_
        self.trabajos = list(trabajos)
        self.propietario = propietario
        self.ejecutar = ejecutar
        horas = float(getattr(settings, "PLATAFORMA_RECUPERAR_HORAS", 6))
        self.recuperar = recuperar if recuperar is not None else timedelta(hours=horas)
        self._en_marcha: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def debidos(self, ahora: datetime) -> list[tuple[Trabajo, datetime]]:
        """Jobs whose latest slot is due now (within the recovery window) and not running here."""
        salida: list[tuple[Trabajo, datetime]] = []
        for trabajo in self.trabajos:
            turno = trabajo.programacion.ultimo_turno(ahora)
            if turno is None or turno < ahora - self.recuperar:
                continue
            with self._lock:
                hilo = self._en_marcha.get(trabajo.nombre)
                if hilo is not None and hilo.is_alive():
                    continue
            salida.append((trabajo, turno))
        return salida

    def comprobar(self, ahora: datetime, *, esperar: bool = False) -> list[str]:
        """Claim and start every due job (in threads; ``esperar`` joins them). Returns the names started."""
        lanzados: list[str] = []
        for trabajo, turno in self.debidos(ahora):
            if not self.estado.reclamar(trabajo.nombre, turno, self.propietario, ahora):
                continue
            hilo = threading.Thread(target=self._correr, args=(trabajo,), name=f"trabajo-{trabajo.nombre}", daemon=True)
            with self._lock:
                self._en_marcha[trabajo.nombre] = hilo
            logger.info("Scheduled job %s: slot %s", trabajo.nombre, turno.isoformat())
            hilo.start()
            lanzados.append(trabajo.nombre)
        if esperar:
            self.esperar()
        return lanzados

    def _correr(self, trabajo: Trabajo) -> None:
        codigo, error = 1, None
        try:
            codigo = self.ejecutar(trabajo)
        except Exception as exc:
            error = type(exc).__name__
            logger.exception("Scheduled job %s crashed", trabajo.nombre)
        finally:
            if codigo != 0 and error is None:
                error = f"código de salida {codigo}"
            try:
                self.estado.terminar(trabajo.nombre, self.propietario, codigo, error, datetime.now(UTC))
            except sqlite3.Error:
                logger.exception("Could not record the end of job %s", trabajo.nombre)
            logger.info("Scheduled job %s finished with code %s", trabajo.nombre, codigo)

    def en_marcha(self) -> list[str]:
        with self._lock:
            return sorted(n for n, h in self._en_marcha.items() if h.is_alive())

    def esperar(self, timeout: float | None = None) -> None:
        with self._lock:
            hilos = list(self._en_marcha.values())
        for hilo in hilos:
            hilo.join(timeout)


# --------------------------------------------------------------------------- #
# The worker
# --------------------------------------------------------------------------- #


def nombre_de_proceso(rol: str = "worker") -> str:
    return f"{rol}-{socket.gethostname()}-{os.getpid()}"


class Worker:
    """The ``worker`` process: migrations, services in threads, the scheduler and a heartbeat."""

    def __init__(
        self,
        db_path: str,
        *,
        registro_: Registro | None = None,
        nombre: str | None = None,
        servicios: bool = True,
        trabajos: bool = True,
        ejecutar: Ejecutor = ejecutar_trabajo,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = db_path
        self.registro = registro_ or registro()
        self.nombre = nombre or nombre_de_proceso()
        self.con_servicios = servicios
        self.con_trabajos = trabajos
        self.reloj = reloj or (lambda: datetime.now(UTC))
        self.iniciado = self.reloj()
        self._ejecutar = ejecutar
        self._hilos: dict[str, threading.Thread] = {}
        self.parar = threading.Event()
        self.estado: EstadoCompartido | None = None
        self.planificador: Planificador | None = None

    def preparar(self) -> dict[str, Any]:
        """Migrate the database and open the shared state. Returns the migration report."""
        informe = migrar(self.db_path, base=BASE_CASOS)
        self.estado = EstadoCompartido(self.db_path)
        trabajos = self.registro.trabajos_activos() if self.con_trabajos else []
        self.planificador = Planificador(self.estado, trabajos, propietario=self.nombre, ejecutar=self._ejecutar)
        return informe.model_dump()

    def _arrancar_servicios(self) -> None:
        if not self.con_servicios:
            return
        for servicio in self.registro.servicios_activos():
            contexto = ContextoServicio(db_path=self.db_path, nombre_proceso=self.nombre, parar=self.parar)
            hilo = threading.Thread(
                target=self._bucle_servicio, args=(servicio, contexto), name=f"servicio-{servicio.nombre}", daemon=True
            )
            self._hilos[servicio.nombre] = hilo
            hilo.start()
            logger.info("Service %s started", servicio.nombre)

    def _bucle_servicio(self, servicio: Servicio, contexto: ContextoServicio) -> None:
        """Run a service; if it crashes, start it again after a pause (until the worker stops)."""
        pausa = 5.0
        while not self.parar.is_set():
            try:
                servicio.arrancar(contexto)
                return
            except Exception:
                logger.exception("Service %s crashed; restarting in %.0f s", servicio.nombre, pausa)
                self.parar.wait(pausa)
                pausa = min(pausa * 2, 300.0)

    def detalle(self) -> dict[str, Any]:
        return {
            "servicios": {n: h.is_alive() for n, h in self._hilos.items()},
            "trabajos": [t.nombre for t in (self.planificador.trabajos if self.planificador else [])],
            "en_marcha": self.planificador.en_marcha() if self.planificador else [],
        }

    def latir(self) -> None:
        if self.estado is not None:
            self.estado.latir(self.nombre, "worker", self.iniciado, self.detalle(), self.reloj())

    def paso(self, *, esperar: bool = False) -> list[str]:
        """One scheduler check and one heartbeat."""
        lanzados = self.planificador.comprobar(self.reloj(), esperar=esperar) if self.planificador else []
        self.latir()
        return lanzados

    def ejecutar(self) -> None:
        """Run until :attr:`parar` is set (SIGTERM / SIGINT in the CLI)."""
        if self.estado is None:
            self.preparar()
        self._arrancar_servicios()
        intervalo = max(1.0, float(getattr(settings, "PLATAFORMA_LATIDO_SEGUNDOS", 30)))
        logger.info("Worker %s started on %s", self.nombre, self.db_path)
        while not self.parar.is_set():
            try:
                self.paso()
            except Exception:
                logger.exception("Worker step failed; retrying after the pause")
            self.parar.wait(intervalo)
        self.cerrar()

    def una_vez(self) -> dict[str, Any]:
        """One pass for cron: every service's single pass is not available, so only the event bus pass (when
        registered) and the due jobs run, and the worker waits for them."""
        if self.estado is None:
            self.preparar()
        resumen: dict[str, Any] = {}
        if self.con_servicios and "eventos" in self.registro.servicios:
            from hacienda_gpt.plataforma.trabajos import pasada_de_eventos

            resumen["eventos"] = pasada_de_eventos(self.db_path, self.nombre)
        resumen["trabajos"] = self.paso(esperar=True)
        self.cerrar()
        return resumen

    def cerrar(self, timeout: float = 30.0) -> None:
        self.parar.set()
        for hilo in self._hilos.values():
            hilo.join(timeout)
        if self.planificador is not None:
            self.planificador.esperar(timeout)
        if self.estado is not None:
            try:
                self.estado.despedir(self.nombre)
            finally:
                self.estado.close()
                self.estado = None
        logger.info("Worker %s stopped", self.nombre)


# --------------------------------------------------------------------------- #
# Health and description
# --------------------------------------------------------------------------- #


def salud(db_path: str, *, ahora: datetime | None = None) -> dict[str, Any]:
    """Migrations, workers (heartbeat age) and scheduled jobs of ``db_path``. Aggregates only, no user data."""
    ahora = ahora or datetime.now(UTC)
    pendientes = {e.propietario: e.pendientes for e in estado(db_path) if e.pendientes}
    intervalo = float(getattr(settings, "PLATAFORMA_LATIDO_SEGUNDOS", 30))
    workers: list[dict[str, Any]] = []
    trabajos: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error:
        conn = None
    if conn is not None:
        conn.row_factory = sqlite3.Row
        try:
            for fila in conn.execute("SELECT * FROM plataforma_procesos ORDER BY nombre"):
                edad = (ahora - datetime.fromisoformat(fila["latido"])).total_seconds()
                workers.append(
                    {
                        "nombre": fila["nombre"],
                        "rol": fila["rol"],
                        "segundos_desde_latido": round(edad, 1),
                        "vivo": edad <= 3 * intervalo,
                        "detalle": json.loads(fila["detalle_json"] or "{}"),
                    }
                )
            trabajos = [dict(f) for f in conn.execute("SELECT * FROM plataforma_trabajos ORDER BY nombre")]
        except sqlite3.Error:
            pass  # tables not created yet: no worker ever ran here
        finally:
            conn.close()
    return {
        "migraciones_pendientes": pendientes,
        "workers": workers,
        "worker_vivo": any(w["vivo"] for w in workers if w["rol"] == "worker"),
        "trabajos": trabajos,
        "despacho_en_api": despacho_en_api(),
    }


def describir(registro_: Registro | None = None, *, ahora: datetime | None = None) -> dict[str, Any]:
    """The process model as the operator reads it (``cli.plataforma procesos``)."""
    reg = registro_ or registro()
    ahora = ahora or datetime.now(UTC)
    activos = {t.nombre for t in reg.trabajos_activos()}
    return {
        "procesos": {
            "api": {
                "orden": "uvicorn hacienda_gpt.api.api:app --proxy-headers",
                "despacha_eventos": despacho_en_api(),
            },
            "worker": {"orden": "python -m hacienda_gpt.cli.plataforma worker"},
        },
        "servicios": [
            {"nombre": s.nombre, "descripcion": s.descripcion, "activo": s.por_defecto} for s in reg.servicios.values()
        ],
        "trabajos": [
            {
                "nombre": t.nombre,
                "descripcion": t.descripcion,
                "cuando": t.programacion.describir(),
                "activo": t.nombre in activos,
                "siguiente": (lambda s: s.isoformat() if s else None)(t.programacion.siguiente_turno(ahora)),
                "orden": " ".join(t.orden) if t.orden else None,
            }
            for t in reg.trabajos.values()
        ],
        "extensiones": dict(reg.extensiones),
    }


__all__ = [
    "EXTENSIONES",
    "ZONA",
    "ContextoServicio",
    "EstadoCompartido",
    "Planificador",
    "Programacion",
    "Registro",
    "Servicio",
    "Trabajo",
    "Worker",
    "describir",
    "despacho_en_api",
    "ejecutar_trabajo",
    "nombre_de_proceso",
    "registro",
    "reiniciar_registro",
    "salud",
]

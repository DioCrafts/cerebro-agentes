"""The migration engine: versioned migrations per owner package, recorded in ``schema_migrations``.

Model
-----
* A :class:`Propietario` is the package that owns some tables (``auth``, ``eventos``, ``correo``…). Its
  :class:`Migracion` list is numbered 1, 2, 3… and applied in that order, each exactly once per database.
* ``depende_de`` orders owners: an owner's migrations run after those of the owners it depends on (and
  :func:`asegurar_esquema` brings those up to date first).
* ``requiere_tablas`` defers a migration until another owner's table exists (a trigger ``AFTER DELETE ON
  case_states`` needs ``case_states``): until then it is *aplazada* — not applied, not recorded, retried the
  next time — and the owner's later migrations wait behind it.
* Every applied migration is a row of ``schema_migrations`` (owner, version, name, checksum, date). A
  migration whose text changed after it was applied is reported by :func:`estado` (``sumas_distintas``):
  an applied migration is never edited; a change is a new migration.

Guarantees
----------
* **Transactional.** The pending migrations of a call run inside one ``BEGIN IMMEDIATE`` transaction (SQLite
  DDL is transactional): all of them, or none. Called on a connection that already has a transaction open,
  they join it and commit or roll back with the caller's work.
* **Idempotent and concurrent-safe.** The applied set is read again inside the write lock, so two processes
  starting at once apply each migration once; the baseline DDL is ``IF NOT EXISTS``, so a database created
  before the migrations existed is adopted as it is.
* **Cheap when up to date.** :func:`asegurar_esquema` (called by every store when it opens a connection) does
  one read of ``schema_migrations`` and takes no lock unless something is pending.

``MIGRACIONES_AUTOMATICAS=false`` turns the store hook off (the stores then create their own tables exactly
as before the migrations existed); ``python -m asistente.plataforma.cli.plataforma migrar`` still works.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import sqlite3
import textwrap
import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

BASE_CASOS = "casos"
BASE_DOCTRINA = "doctrina"
BASES = (BASE_CASOS, BASE_DOCTRINA)

TABLA_REGISTRO = "schema_migrations"
DDL_REGISTRO = """CREATE TABLE IF NOT EXISTS schema_migrations (
    propietario TEXT NOT NULL,
    version INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    suma TEXT NOT NULL,
    aplicada_el TEXT NOT NULL,
    PRIMARY KEY (propietario, version)
)"""


# A migration's checksum names its function by module path, and the checksum is STORED in ``schema_migrations`` when
# the migration is applied: the module path the package had then (``hacienda_gpt.…``) is used for ever, so moving
# the catalogue never makes an applied migration look edited. New prefix -> the prefix recorded in the checksums.
PREFIJOS_HISTORICOS: tuple[tuple[str, str], ...] = (("asistente.plataforma.", "hacienda_gpt.plataforma."),)


def nombre_de_funcion(funcion: Callable[..., Any]) -> str:
    """``module.qualname`` of a migration function, spelled as when the catalogue was first published."""
    modulo = funcion.__module__
    for actual, historico in PREFIJOS_HISTORICOS:
        if modulo.startswith(actual):
            modulo = historico + modulo[len(actual) :]
            break
    return f"{modulo}.{funcion.__qualname__}"


class MigracionError(RuntimeError):
    """A migration catalogue that cannot be applied (unknown owner, bad numbering, dependency cycle)."""


@dataclass(frozen=True)
class Migracion:
    """One step of an owner's schema. ``sentencias`` run first, then ``funcion`` (for conditional changes)."""

    version: int
    nombre: str
    sentencias: tuple[str, ...] = ()
    funcion: Callable[[sqlite3.Connection], None] | None = None
    requiere_tablas: tuple[str, ...] = ()

    def suma(self) -> str:
        """Checksum of what the migration does (its SQL and the name of its function)."""
        h = hashlib.sha256()
        for sentencia in self.sentencias:
            h.update(sentencia.encode("utf-8"))
            h.update(b"\x00")
        if self.funcion is not None:
            h.update(nombre_de_funcion(self.funcion).encode())
        return h.hexdigest()[:16]

    def aplicar(self, conn: sqlite3.Connection) -> None:
        for sentencia in self.sentencias:
            conn.execute(sentencia)
        if self.funcion is not None:
            self.funcion(conn)


@dataclass(frozen=True)
class Propietario:
    """A package that owns tables of one database (``base``)."""

    nombre: str
    paquete: str
    tablas: tuple[str, ...]
    migraciones: tuple[Migracion, ...]
    base: str = BASE_CASOS
    depende_de: tuple[str, ...] = ()
    descripcion: str = ""
    # Extra objects that are not tables (indexes, triggers), for the inventory.
    otros_objetos: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        versiones = [m.version for m in self.migraciones]
        if versiones != list(range(1, len(versiones) + 1)):
            raise MigracionError(f"{self.nombre}: las versiones deben ser 1, 2, 3… sin huecos ({versiones})")
        if self.base not in BASES:
            raise MigracionError(f"{self.nombre}: base desconocida {self.base!r}")

    @property
    def ultima(self) -> int:
        return len(self.migraciones)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_registro: dict[str, Propietario] = {}
_lock = threading.RLock()
_catalogo: list[bool] = []


def _cargar_catalogo() -> None:
    with _lock:
        if _catalogo:
            return
        _catalogo.append(True)
        from asistente.plataforma.migraciones.catalogo import PROPIETARIOS

        for propietario_ in PROPIETARIOS:
            _registro.setdefault(propietario_.nombre, propietario_)


def registrar(propietario_: Propietario, *, reemplazar: bool = False) -> None:
    """Add an owner (another package's tables). Registering the same owner twice is an error unless equal."""
    _cargar_catalogo()
    with _lock:
        previo = _registro.get(propietario_.nombre)
        if previo is not None and previo != propietario_ and not reemplazar:
            raise MigracionError(f"El propietario {propietario_.nombre!r} ya está registrado con otras migraciones")
        _registro[propietario_.nombre] = propietario_


def olvidar(nombre: str) -> None:
    """Remove an owner registered at run time (tests)."""
    with _lock:
        _registro.pop(nombre, None)


def propietario(nombre: str) -> Propietario:
    _cargar_catalogo()
    with _lock:
        encontrado = _registro.get(nombre)
    if encontrado is None:
        raise MigracionError(f"No hay migraciones registradas para {nombre!r}")
    return encontrado


def _ordenar(nombres: Iterable[str]) -> list[Propietario]:
    """The owners named and everything they depend on, dependencies first (stable: catalogue order)."""
    _cargar_catalogo()
    with _lock:
        registro = dict(_registro)
    salida: list[Propietario] = []
    visitando: set[str] = set()
    hechos: set[str] = set()

    def visitar(nombre: str) -> None:
        if nombre in hechos:
            return
        if nombre in visitando:
            raise MigracionError(f"Dependencia circular entre migraciones en {nombre!r}")
        actual = registro.get(nombre)
        if actual is None:
            raise MigracionError(f"No hay migraciones registradas para {nombre!r}")
        visitando.add(nombre)
        for dependencia in actual.depende_de:
            otra = registro.get(dependencia)
            if otra is not None and otra.base != actual.base:
                raise MigracionError(f"{nombre} depende de {dependencia}, que está en otra base de datos")
            visitar(dependencia)
        visitando.discard(nombre)
        hechos.add(nombre)
        salida.append(actual)

    for nombre in nombres:
        visitar(nombre)
    return salida


def propietarios(base: str | None = None) -> list[Propietario]:
    """Every registered owner (of ``base``), in the order their migrations run."""
    _cargar_catalogo()
    with _lock:
        nombres = [p.nombre for p in _registro.values() if base is None or p.base == base]
    return _ordenar(nombres)


# --------------------------------------------------------------------------- #
# Reading the state of a database
# --------------------------------------------------------------------------- #


def _aplicadas(conn: sqlite3.Connection) -> dict[tuple[str, int], str] | None:
    """(owner, version) -> checksum of every applied migration; None when the registry table is missing."""
    try:
        filas = conn.execute(f"SELECT propietario, version, suma FROM {TABLA_REGISTRO}").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    return {(str(f[0]), int(f[1])): str(f[2]) for f in filas}


def _tablas(conn: sqlite3.Connection) -> set[str]:
    return {str(f[0]) for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _faltan(conn: sqlite3.Connection, migracion: Migracion, extra: set[str] | None = None) -> list[str]:
    if not migracion.requiere_tablas:
        return []
    existentes = _tablas(conn) | (extra or set())
    return [t for t in migracion.requiere_tablas if t not in existentes]


class Paso(BaseModel):
    """A migration in a plan or a report."""

    model_config = ConfigDict(extra="forbid")

    propietario: str
    version: int
    nombre: str
    sentencias: list[str] = Field(default_factory=list)
    funcion: str | None = None
    # Deferred: waiting for these tables (another owner's) to exist.
    espera_tablas: list[str] = Field(default_factory=list)


class Informe(BaseModel):
    """What :func:`migrar` did (or, with ``dry_run``, would do)."""

    model_config = ConfigDict(extra="forbid")

    base: str
    ruta: str
    dry_run: bool
    aplicadas: list[Paso] = Field(default_factory=list)
    aplazadas: list[Paso] = Field(default_factory=list)


class EstadoPropietario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    propietario: str
    paquete: str
    base: str
    tablas: list[str]
    version_actual: int
    version_ultima: int
    pendientes: list[int]
    # Applied migrations whose code changed afterwards (should never happen: write a new migration).
    sumas_distintas: list[int] = Field(default_factory=list)

    @property
    def al_dia(self) -> bool:
        return not self.pendientes


def _paso(propietario_: Propietario, migracion: Migracion, espera: list[str] | None = None) -> Paso:
    funcion = migracion.funcion
    return Paso(
        propietario=propietario_.nombre,
        version=migracion.version,
        nombre=migracion.nombre,
        sentencias=[textwrap.dedent(s).strip() for s in migracion.sentencias],
        funcion=nombre_de_funcion(funcion) if funcion is not None else None,
        espera_tablas=espera or [],
    )


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #


def automaticas() -> bool:
    """Whether the stores bring their schema up to date when they open a connection (default: yes)."""
    from asistente.plataforma import settings

    return bool(getattr(settings, "MIGRACIONES_AUTOMATICAS", True))


def _hay_trabajo(conn: sqlite3.Connection, orden: list[Propietario]) -> bool:
    aplicadas = _aplicadas(conn)
    if aplicadas is None:
        return True
    for actual in orden:
        for migracion in actual.migraciones:
            if (actual.nombre, migracion.version) in aplicadas:
                continue
            # The first pending one decides for this owner: applicable now, or deferred (and the rest waits).
            if not _faltan(conn, migracion):
                return True
            break
    return False


def _aplicar(conn: sqlite3.Connection, orden: list[Propietario]) -> tuple[list[Paso], list[Paso]]:
    """Apply what is pending for ``orden`` in ONE transaction. Returns (applied, deferred)."""
    propia = not conn.in_transaction
    if propia:
        conn.execute("BEGIN IMMEDIATE")
    aplicadas_ahora: list[Paso] = []
    aplazadas: list[Paso] = []
    try:
        conn.execute(DDL_REGISTRO)
        # Read again under the write lock: another process may have just applied them.
        aplicadas = _aplicadas(conn) or {}
        momento = datetime.now(UTC).isoformat()
        for actual in orden:
            for migracion in actual.migraciones:
                if (actual.nombre, migracion.version) in aplicadas:
                    continue
                faltan = _faltan(conn, migracion)
                if faltan:
                    aplazadas.append(_paso(actual, migracion, faltan))
                    break
                migracion.aplicar(conn)
                conn.execute(
                    f"INSERT INTO {TABLA_REGISTRO} (propietario, version, nombre, suma, aplicada_el) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (actual.nombre, migracion.version, migracion.nombre, migracion.suma(), momento),
                )
                aplicadas_ahora.append(_paso(actual, migracion))
    except BaseException:
        if propia and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    if propia:
        conn.execute("COMMIT")
    return aplicadas_ahora, aplazadas


def asegurar_esquema(conn: sqlite3.Connection, nombre: str) -> None:
    """The store hook: bring owner ``nombre`` (and what it depends on) up to date on ``conn``.

    One read when nothing is pending. Joins the caller's transaction if one is open. A no-op when
    ``MIGRACIONES_AUTOMATICAS`` is off.
    """
    if not automaticas():
        return
    orden = _ordenar([nombre])
    if not _hay_trabajo(conn, orden):
        return
    _aplicar(conn, orden)


def _abrir(ruta: str, *, solo_lectura: bool) -> sqlite3.Connection | None:
    if solo_lectura:
        if ruta != ":memory:" and not Path(ruta).exists():
            return None
        if ruta == ":memory:":
            return sqlite3.connect(ruta)
        return sqlite3.connect(f"file:{Path(ruta).resolve()}?mode=ro", uri=True, timeout=30.0)
    if ruta != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta, timeout=30.0)
    if ruta != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL;")
    return conn


@contextmanager
def _conexion(destino: str | sqlite3.Connection, *, solo_lectura: bool) -> Iterator[sqlite3.Connection | None]:
    if isinstance(destino, sqlite3.Connection):
        yield destino
        return
    conn = _abrir(str(destino), solo_lectura=solo_lectura)
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()


def _nombre_ruta(destino: str | sqlite3.Connection) -> str:
    if isinstance(destino, sqlite3.Connection):
        fila = destino.execute("PRAGMA database_list").fetchone()
        return str(fila[2] or ":memory:") if fila is not None else ""
    return str(destino)


def _planificar(conn: sqlite3.Connection | None, orden: list[Propietario]) -> tuple[list[Paso], list[Paso]]:
    """What :func:`_aplicar` would do, without writing (tables created by earlier steps count as present)."""
    aplicadas = (_aplicadas(conn) if conn is not None else None) or {}
    creadas: set[str] = set()
    pasos: list[Paso] = []
    aplazadas: list[Paso] = []
    for actual in orden:
        for migracion in actual.migraciones:
            if (actual.nombre, migracion.version) in aplicadas:
                continue
            existentes = (_tablas(conn) if conn is not None else set()) | creadas
            faltan = [t for t in migracion.requiere_tablas if t not in existentes]
            if faltan:
                aplazadas.append(_paso(actual, migracion, faltan))
                break
            pasos.append(_paso(actual, migracion))
        creadas.update(actual.tablas)
    return pasos, aplazadas


def migrar(
    destino: str | sqlite3.Connection,
    *,
    base: str = BASE_CASOS,
    nombres: Iterable[str] | None = None,
    dry_run: bool = False,
) -> Informe:
    """Apply every pending migration of ``base`` (or of the owners ``nombres``) to ``destino``.

    ``dry_run`` reads only (it never creates the file) and reports what would run, SQL included.
    """
    orden = _ordenar(nombres) if nombres is not None else propietarios(base)
    ruta = _nombre_ruta(destino)
    with _conexion(destino, solo_lectura=dry_run) as conn:
        if dry_run:
            pasos, aplazadas = _planificar(conn, orden)
            return Informe(base=base, ruta=ruta, dry_run=True, aplicadas=pasos, aplazadas=aplazadas)
        assert conn is not None
        aplicadas, aplazadas = _aplicar(conn, orden)
        return Informe(base=base, ruta=ruta, dry_run=False, aplicadas=aplicadas, aplazadas=aplazadas)


def estado(destino: str | sqlite3.Connection, *, base: str = BASE_CASOS) -> list[EstadoPropietario]:
    """Per owner of ``base``: current and latest version, pending versions, changed checksums. Reads only."""
    salida: list[EstadoPropietario] = []
    with _conexion(destino, solo_lectura=True) as conn:
        aplicadas = (_aplicadas(conn) if conn is not None else None) or {}
        for actual in propietarios(base):
            hechas = [m for m in actual.migraciones if (actual.nombre, m.version) in aplicadas]
            salida.append(
                EstadoPropietario(
                    propietario=actual.nombre,
                    paquete=actual.paquete,
                    base=actual.base,
                    tablas=list(actual.tablas),
                    version_actual=max((m.version for m in hechas), default=0),
                    version_ultima=actual.ultima,
                    pendientes=[m.version for m in actual.migraciones if (actual.nombre, m.version) not in aplicadas],
                    sumas_distintas=[m.version for m in hechas if aplicadas[(actual.nombre, m.version)] != m.suma()],
                )
            )
    return salida


def volcado(destino: str | sqlite3.Connection, *, excluir: Iterable[str] = (TABLA_REGISTRO,)) -> list[tuple[str, ...]]:
    """The schema as SQLite stores it — (type, name, table, sql) sorted — without the migration registry."""
    fuera = set(excluir)
    with _conexion(destino, solo_lectura=True) as conn:
        if conn is None:
            return []
        filas = conn.execute("SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master").fetchall()
    return sorted(tuple(str(c) for c in f) for f in filas if f[1] not in fuera and f[2] not in fuera)


__all__ = [
    "BASES",
    "BASE_CASOS",
    "BASE_DOCTRINA",
    "DDL_REGISTRO",
    "TABLA_REGISTRO",
    "EstadoPropietario",
    "Informe",
    "Migracion",
    "MigracionError",
    "Paso",
    "Propietario",
    "asegurar_esquema",
    "automaticas",
    "estado",
    "migrar",
    "olvidar",
    "propietario",
    "propietarios",
    "registrar",
    "volcado",
]

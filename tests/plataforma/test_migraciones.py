"""Schema migrations: the baseline reproduces what the stores created before the migrations existed, an old
database upgrades cleanly, the stores' hook applies only their own owner, and every run is transactional
and idempotent."""

from __future__ import annotations

from collections.abc import Iterator
import sqlite3
import threading

from click.testing import CliRunner
import pytest

from asistente.capacidades.correo import almacen as correo
from asistente.conocimiento.doctrine.grafo import DoctrineGraph
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.evaluacion.captura import FeedbackStore
from asistente.nucleo.eventos.bus import Bus
from asistente.nucleo.memoria.almacen import DecisionStore
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.proactive.entrega import EntregaStore
from asistente.nucleo.proactive.store import NotificationStore
from asistente.nucleo.tareas.almacen import TareaStore
from asistente.plataforma import settings
from asistente.plataforma.auth.store import AuthStore
from asistente.plataforma.cli.plataforma import cli
from asistente.plataforma.migraciones import (
    BASE_DOCTRINA,
    TABLA_REGISTRO,
    Migracion,
    MigracionError,
    Propietario,
    asegurar_esquema,
    estado,
    migrar,
    olvidar,
    propietarios,
    registrar,
    volcado,
)
from asistente.plataforma.ops.consumo import ConsumoStore
from asistente.plataforma.push.almacen import PushStore
from tests.proactive import make_case

# Owners that did not exist before the migrations (new tables, not part of the «old code» comparison).
NUEVOS = {"navegador", "plataforma"}


def _sin_nuevos(filas: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    nuevas = {t for p in propietarios() if p.nombre in NUEVOS for t in p.tablas}
    return [f for f in filas if f[2] not in nuevas and f[1] not in {"navegador_borrar_con_caso"}]


def _con_todos_los_stores(path: str) -> None:
    """Open every store of the cases' database (each creates its tables on first use)."""
    SQLiteCaseStateStore(path).close()
    AuthStore(path)._get_conn().close()
    NotificationStore(path).close()
    EntregaStore(path).close()
    AccionStore(path)._get().close()
    DecisionStore(path)._get().close()
    FeedbackStore(path)._get().close()
    Bus(path).close()
    with correo.conectar(path):
        pass
    ConsumoStore(path).close()
    PushStore(path).close()
    TareaStore(path)._get().close()


@pytest.fixture
def codigo_antiguo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The stores exactly as before the migrations: the hook is a no-op, each runs its own DDL."""
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    yield
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", True)


def _registro(path: str) -> list[tuple[str, int]]:
    conn = sqlite3.connect(path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (TABLA_REGISTRO,)).fetchone():
            return []  # not even the registry table: a rolled-back first run leaves nothing
        return sorted(conn.execute(f"SELECT propietario, version FROM {TABLA_REGISTRO}").fetchall())
    finally:
        conn.close()


def test_the_baseline_reproduces_the_schema_the_old_code_created(tmp_path, codigo_antiguo) -> None:
    viejo, nuevo = str(tmp_path / "viejo.sqlite3"), str(tmp_path / "nuevo.sqlite3")
    _con_todos_los_stores(viejo)
    informe = migrar(nuevo)
    assert not informe.aplazadas
    # Byte for byte: the CREATE text SQLite keeps (indentation included), every index, trigger and sequence.
    assert volcado(viejo) == _sin_nuevos(volcado(nuevo))
    assert TABLA_REGISTRO not in {f[1] for f in volcado(viejo, excluir=())}

    grafo_viejo, grafo_nuevo = str(tmp_path / "g1.sqlite3"), str(tmp_path / "g2.sqlite3")
    DoctrineGraph(grafo_viejo).close()
    migrar(grafo_nuevo, base=BASE_DOCTRINA)
    assert volcado(grafo_viejo) == volcado(grafo_nuevo) != []


def test_every_table_in_the_database_has_an_owner(tmp_path) -> None:
    path = str(tmp_path / "s.sqlite3")
    migrar(path)
    tablas = {f[1] for f in volcado(path) if f[0] == "table" and f[1] != "sqlite_sequence"}
    con_dueno = {t for p in propietarios() for t in p.tablas}
    assert tablas == con_dueno - {"nodos", "alias", "aristas"}


def test_an_old_database_upgrades_cleanly_and_keeps_its_data(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "antigua.sqlite3")
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    _con_todos_los_stores(path)
    SQLiteCaseStateStore(path).save_case(make_case("c1"))
    usuario = AuthStore(path).create_user("ana@example.com", "una-clave-larga-1")
    antes = volcado(path)
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", True)

    informe = migrar(path)
    assert {p.propietario for p in informe.aplicadas} >= {"decision", "auth", "eventos", "correo", "navegador"}
    # The old tables are untouched (the baseline adopts them); only the new owners' objects are added.
    assert _sin_nuevos(volcado(path)) == antes
    assert SQLiteCaseStateStore(path).get_case("c1") is not None
    assert AuthStore(path).get_user(usuario.id) is not None
    # Idempotent: a second run applies nothing, and every owner is at its latest version.
    assert migrar(path).aplicadas == []
    assert all(not e.pendientes and not e.sumas_distintas for e in estado(path))


def test_a_database_from_before_the_added_columns_gets_them_as_the_stores_did(tmp_path, monkeypatch) -> None:
    """``case_states`` before optimistic concurrency and ``llm_usage`` before the routing columns."""
    viejo_codigo, migrada = str(tmp_path / "a.sqlite3"), str(tmp_path / "b.sqlite3")
    for path in (viejo_codigo, migrada):
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE case_states (case_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL, "
            "schema_version TEXT NOT NULL, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id TEXT, "
            "case_id TEXT, purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER, "
            "output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL)"
        )
        conn.close()
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    SQLiteCaseStateStore(viejo_codigo).close()
    ConsumoStore(viejo_codigo).close()
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", True)
    migrar(migrada, nombres=["decision", "consumo"])
    assert volcado(viejo_codigo) == volcado(migrada)
    columnas = {f[1] for f in sqlite3.connect(migrada).execute("PRAGMA table_info(llm_usage)")}
    assert {"tier", "escalated", "batch", "cache_write_1h_tokens"} <= columnas


def test_each_store_migrates_only_its_own_owner(tmp_path) -> None:
    path = str(tmp_path / "s.sqlite3")
    AuthStore(path)._get_conn()
    assert _registro(path) == [("auth", 1)]
    # The decisions' erasure trigger waits for the cases table, as the store always did.
    DecisionStore(path)._get()
    assert _registro(path) == [("auth", 1), ("memoria", 1)]
    SQLiteCaseStateStore(path).close()
    nueva = DecisionStore(path)
    nueva._get()
    assert ("memoria", 2) in _registro(path)
    nombres = {f[1] for f in volcado(path)}
    assert "decisiones_borrar_con_caso" in nombres and "eventos" not in nombres
    # The deliveries depend on the notifications.
    EntregaStore(path).close()
    assert {("proactive", 1), ("proactive_entrega", 1)} <= set(_registro(path))


def test_the_hook_is_one_read_when_up_to_date(tmp_path) -> None:
    path = str(tmp_path / "s.sqlite3")
    migrar(path)
    conn = sqlite3.connect(path)
    sentencias: list[str] = []
    conn.set_trace_callback(sentencias.append)
    asegurar_esquema(conn, "auth")
    conn.close()
    assert len(sentencias) == 1 and sentencias[0].startswith("SELECT")


@pytest.fixture
def prueba() -> Iterator[str]:
    yield "prueba_plataforma"
    olvidar("prueba_plataforma")


def _falla(conn: sqlite3.Connection) -> None:
    raise RuntimeError("fallo a propósito")


def test_a_failing_migration_rolls_back_the_whole_run(tmp_path, prueba: str) -> None:
    registrar(
        Propietario(
            nombre=prueba,
            paquete="tests",
            tablas=("t_prueba",),
            migraciones=(
                Migracion(1, "tabla", ("CREATE TABLE IF NOT EXISTS t_prueba (x INTEGER)",)),
                Migracion(2, "rota", funcion=_falla),
            ),
        )
    )
    path = str(tmp_path / "s.sqlite3")
    with pytest.raises(RuntimeError):
        migrar(path, nombres=[prueba])
    assert "t_prueba" not in {f[1] for f in volcado(path)}
    assert _registro(path) == []


def test_inside_the_callers_transaction_it_commits_or_rolls_back_with_it(tmp_path, prueba: str) -> None:
    registrar(
        Propietario(
            nombre=prueba,
            paquete="tests",
            tablas=("t_prueba",),
            migraciones=(Migracion(1, "tabla", ("CREATE TABLE IF NOT EXISTS t_prueba (x INTEGER)",)),),
        )
    )
    path = str(tmp_path / "s.sqlite3")
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE otra (y INTEGER)")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO otra VALUES (1)")
    asegurar_esquema(conn, prueba)
    assert conn.in_transaction
    conn.execute("ROLLBACK")
    assert "t_prueba" not in {f[1] for f in volcado(path)}
    conn.execute("BEGIN")
    asegurar_esquema(conn, prueba)
    conn.execute("COMMIT")
    conn.close()
    assert (prueba, 1) in _registro(path)


def test_concurrent_processes_apply_each_migration_once(tmp_path) -> None:
    path = str(tmp_path / "s.sqlite3")
    errores: list[BaseException] = []

    def migrar_en_hilo() -> None:
        try:
            migrar(path)
        except BaseException as exc:  # pragma: no cover - reported below
            errores.append(exc)

    hilos = [threading.Thread(target=migrar_en_hilo) for _ in range(6)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert errores == []
    filas = _registro(path)
    assert len(filas) == len(set(filas)) == sum(p.ultima for p in propietarios("casos"))


def test_dry_run_reads_only_and_shows_the_sql(tmp_path) -> None:
    path = tmp_path / "no_existe.sqlite3"
    informe = migrar(str(path), dry_run=True)
    assert not path.exists()
    assert informe.dry_run and informe.aplicadas[0].propietario == "decision"
    assert any("CREATE TABLE IF NOT EXISTS case_states" in s for s in informe.aplicadas[0].sentencias)
    # The triggers on case_states count as applicable: the cases table is created earlier in the same run.
    assert not informe.aplazadas
    assert all(e.pendientes for e in estado(str(path)))


def test_a_migration_edited_after_it_was_applied_is_reported(tmp_path, prueba: str) -> None:
    def propietario_(sql: str) -> Propietario:
        return Propietario(nombre=prueba, paquete="tests", tablas=("t",), migraciones=(Migracion(1, "t", (sql,)),))

    registrar(propietario_("CREATE TABLE IF NOT EXISTS t (x INTEGER)"))
    path = str(tmp_path / "s.sqlite3")
    migrar(path, nombres=[prueba])
    registrar(propietario_("CREATE TABLE IF NOT EXISTS t (x INTEGER, y TEXT)"), reemplazar=True)
    fila = next(e for e in estado(path) if e.propietario == prueba)
    assert fila.sumas_distintas == [1] and fila.pendientes == []


def test_the_catalogue_is_validated() -> None:
    with pytest.raises(MigracionError):
        Propietario(nombre="x", paquete="x", tablas=(), migraciones=(Migracion(2, "salta"),))
    registrar(Propietario(nombre="ciclo_a", paquete="t", tablas=(), migraciones=(), depende_de=("ciclo_b",)))
    registrar(Propietario(nombre="ciclo_b", paquete="t", tablas=(), migraciones=(), depende_de=("ciclo_a",)))
    try:
        with pytest.raises(MigracionError):
            migrar(":memory:", nombres=["ciclo_a"])
    finally:
        olvidar("ciclo_a")
        olvidar("ciclo_b")


def test_with_automatic_migrations_off_the_hook_does_nothing(tmp_path, codigo_antiguo) -> None:
    path = str(tmp_path / "s.sqlite3")
    AuthStore(path)._get_conn()
    assert TABLA_REGISTRO not in {f[1] for f in volcado(path, excluir=())}


def test_the_cli_migrates_and_reports(tmp_path) -> None:
    path = str(tmp_path / "s.sqlite3")
    runner = CliRunner()
    resultado = runner.invoke(cli, ["migrar", "--db-path", path, "--dry-run"])
    assert resultado.exit_code == 0, resultado.output
    assert "Se aplicarían" in resultado.output and "CREATE TABLE IF NOT EXISTS users" in resultado.output
    resultado = runner.invoke(cli, ["migrar", "--db-path", path])
    assert resultado.exit_code == 0 and "Aplicadas" in resultado.output
    resultado = runner.invoke(cli, ["migrar", "--db-path", path, "--estado"])
    assert resultado.exit_code == 0 and "pendientes" not in resultado.output and "al día" in resultado.output
    resultado = runner.invoke(cli, ["migrar", "--db-path", path, "--estado", "--json"])
    assert resultado.exit_code == 0 and '"version_actual": 1' in resultado.output


def test_the_deployment_inventory_lists_every_owner_of_the_catalogue() -> None:
    """docs/despliegue.md §2.1 is the operator's inventory: same owners, packages, tables, objects and versions."""
    from pathlib import Path

    from asistente.plataforma.migraciones.catalogo import PROPIETARIOS

    texto = (Path(__file__).resolve().parents[2] / "docs" / "despliegue.md").read_text(encoding="utf-8")
    seccion = texto.split("### 2.1 Inventario de tablas", 1)[1].split("###", 1)[0]

    def nombres(celda: str) -> set[str]:
        return {n for n in celda.replace("`", "").replace(" ", "").split(",") if n}

    filas = {}
    for linea in seccion.splitlines():
        if linea.startswith("| `"):
            celdas = [c.strip() for c in linea.strip("|").split("|")]
            filas[celdas[0].strip("`")] = celdas
    assert set(filas) == {p.nombre for p in PROPIETARIOS}
    for propietario in PROPIETARIOS:
        celdas = filas[propietario.nombre]
        assert celdas[1].strip("`") == propietario.paquete, propietario.nombre
        assert celdas[2] == propietario.base, propietario.nombre
        assert nombres(celdas[3]) == set(propietario.tablas), propietario.nombre
        assert nombres(celdas[4]) == set(propietario.otros_objetos), propietario.nombre
        assert int(celdas[5]) == len(propietario.migraciones), propietario.nombre

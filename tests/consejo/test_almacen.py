"""The sessions' store: the same schema with and without the migrations, per-case isolation, export, erasure with the
case and the account (triggers and ``DELETE /me``), retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import sqlite3
from typing import Any

import pytest

from asistente.nucleo.consejo.almacen import (
    AlmacenConsejo,
    ExtraSesion,
    borrar_consejo_de_usuario,
    exportar_consejo,
    huella_pregunta,
    nuevo_id,
    tabla_existe,
)
from asistente.nucleo.consejo.contrato import SesionConsejo
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma import settings
from asistente.plataforma.auth.store import AuthStore
from .fakes import caso


def _sesion(case_id: str = "c1", user_id: str | None = "u1", *, creada: datetime | None = None) -> SesionConsejo:
    momento = creada or datetime.now(UTC)
    return SesionConsejo(
        id=nuevo_id(),
        case_id=case_id,
        user_id=user_id,
        estado="pendiente_confirmacion",
        pregunta_usuario="¿Amortizo o invierto?",
        creada=momento,
        actualizada=momento,
        estimacion_usd=Decimal("0.40"),
        tope_usd=Decimal("0.80"),
    )


def _crear(store: AlmacenConsejo, **kw: Any) -> SesionConsejo:
    sesion = _sesion(**kw)
    store.crear(sesion, ExtraSesion(motivo="prueba"), huella=huella_pregunta(sesion.case_id, sesion.pregunta_usuario))
    return sesion


def _esquema(path: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(path)
    try:
        return sorted(
            (f[0], " ".join((f[1] or "").split()))
            for f in conn.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE '%consejo%'")
        )
    finally:
        conn.close()


def test_same_schema_with_and_without_migrations(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    con = str(tmp_path / "con.sqlite3")
    sin = str(tmp_path / "sin.sqlite3")
    for path in (con, sin):
        SQLiteCaseStateStore(path).save_case(caso())
        auth = AuthStore(path)
        auth.owner_of("c1")  # creates the accounts' tables
        auth.close()
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", True)
    AlmacenConsejo(con).listar("c1")
    monkeypatch.setattr(settings, "MIGRACIONES_AUTOMATICAS", False)
    AlmacenConsejo(sin).listar("c1")
    assert _esquema(con) == _esquema(sin)
    nombres = {n for n, _ in _esquema(con) if not n.startswith("sqlite_")}
    assert nombres == {
        "consejo_sesiones",
        "idx_consejo_sesiones_caso",
        "idx_consejo_sesiones_estado",
        "consejo_borrar_con_caso",
        "consejo_borrar_con_usuario",
    }


def test_isolation_per_case(db: str) -> None:
    store = AlmacenConsejo(db)
    mia = _crear(store, case_id="c1")
    _crear(store, case_id="c2", user_id="u2")
    assert store.obtener(mia.id, case_id="c2") is None
    assert [r.sesion.id for r in store.listar("c1")] == [mia.id]
    assert not store.borrar(mia.id, case_id="c2")
    assert not store.transicion(mia.id, case_id="c2", desde=("pendiente_confirmacion",), a="en_cola")
    assert store.obtener(mia.id, case_id="c1") is not None


def test_export_and_erasure_with_the_case(db: str) -> None:
    store = AlmacenConsejo(db)
    sesion = _crear(store)
    exportado = exportar_consejo(db, "c1")
    assert exportado[0]["id"] == sesion.id and exportado[0]["motivo"] == "prueba"
    assert exportado[0]["pregunta_usuario"] == "¿Amortizo o invierto?"
    assert SQLiteCaseStateStore(db).delete_case("c1")
    assert store.listar("c1") == [] and exportar_consejo(db, "c1") == []


def test_erasure_with_the_account(db: str) -> None:
    auth = AuthStore(db)
    user = auth.create_user("ana@example.com", "una-clave-larga-1")
    store = AlmacenConsejo(db)
    _crear(store, case_id="c1", user_id=user.id)
    _crear(store, case_id="c2", user_id="otro")
    assert borrar_consejo_de_usuario(db, user_id=user.id, case_ids=["c2"]) == 2
    assert store.listar("c1") == [] and store.listar("c2") == []
    # The trigger on users does it too.
    _crear(store, case_id="c1", user_id=user.id)
    auth.delete_user(user.id)
    assert store.listar("c1") == []


def test_export_and_erasure_never_create_the_table(tmp_path) -> None:
    path = str(tmp_path / "vacia.sqlite3")
    assert exportar_consejo(path, "c1") == [] and borrar_consejo_de_usuario(path, user_id="u", case_ids=["c1"]) == 0
    assert not tabla_existe(path)


def test_retention_purges_only_finished_sessions(db: str) -> None:
    store = AlmacenConsejo(db)
    vieja = _crear(store)
    abierta = _crear(store, case_id="c2", user_id="u2")
    store.transicion(vieja.id, case_id="c1", desde=("pendiente_confirmacion",), a="rechazada")
    futuro = datetime.now(UTC) + timedelta(days=400)
    assert store.purgar(antes_de=futuro) == 1
    assert store.obtener(vieja.id, case_id="c1") is None
    assert store.obtener(abierta.id, case_id="c2") is not None

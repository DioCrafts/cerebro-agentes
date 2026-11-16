"""``boe-impact`` goes through the bus: ``procesar_eventos`` emits one ``boe.cambio_normativo`` per analysed
change, after notifying inline exactly as before (so nothing changes when the bus is not in use)."""

from __future__ import annotations

import sqlite3

import pytest

from hacienda_gpt import settings
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.eventos.tipos import CAMBIO_NORMATIVO
from hacienda_gpt.proactive.impacto import procesar_eventos
from hacienda_gpt.proactive.store import NotificationStore
from tests.proactive.test_impacto import CASES, LIRPF_HTML, FakeProvider, _evento, _respuesta
from .ayudas import FISCAL, crear_bus

CLAVE = "cambio_normativo:BOE-A-2006-20764:20221222T090000Z"


def _eventos_en(db: str) -> list[tuple[str, str | None, str | None]]:
    conn = sqlite3.connect(db)
    try:
        if conn.execute("SELECT name FROM sqlite_master WHERE name = 'eventos'").fetchone() is None:
            return []
        return [tuple(f) for f in conn.execute("SELECT tipo, case_id, clave FROM eventos ORDER BY id")]
    finally:
        conn.close()


def _procesar(db: str) -> dict:
    store = NotificationStore(db)
    html = {"BOE-A-2006-20764": LIRPF_HTML}
    return procesar_eventos(
        FakeProvider(_respuesta()), eventos=[_evento()], html_por_boe_id=html, cases=CASES, store=store
    )


def test_each_analysed_change_is_one_global_event_and_inline_notices_are_unchanged(tmp_path) -> None:
    db = str(tmp_path / "e.sqlite3")
    casos = SQLiteCaseStateStore(db)
    for case in CASES:
        casos.save_case(case)
    summary = _procesar(db)
    assert summary["analizados"] == 1 and summary["notificaciones"] == len(CASES)
    notas = NotificationStore(db)
    antes = {case.case_id: [n.key for n in notas.list(case.case_id)] for case in CASES}
    assert all(claves == [CLAVE] for claves in antes.values())
    assert _eventos_en(db) == [(CAMBIO_NORMATIVO, None, f"{CAMBIO_NORMATIVO}:BOE-A-2006-20764:20221222T090000Z")]

    # The bus delivers it to the tax module of every open case: same notice key, so nothing is repeated.
    drenado = crear_bus(db, modulos=[FISCAL]).drenar()
    assert drenado["eventos"] == 1 and drenado["entregas"] == drenado["hecho"] == len(CASES)
    assert {case.case_id: [n.key for n in notas.list(case.case_id)] for case in CASES} == antes
    # A re-run (the CLI state file normally prevents it) neither re-notifies nor emits again.
    assert _procesar(db)["notificaciones"] == 0
    assert len(_eventos_en(db)) == 1


def test_without_the_bus_the_inline_notices_are_exactly_the_same(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EVENTOS_ACTIVOS", False)
    db = str(tmp_path / "e.sqlite3")
    summary = _procesar(db)
    assert summary["notificaciones"] == len(CASES)
    assert all([n.key for n in NotificationStore(db).list(case.case_id)] == [CLAVE] for case in CASES)
    assert _eventos_en(db) == []

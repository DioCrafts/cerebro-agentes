"""Producers: a profile change writes its event in the case's own transaction, with names only."""

from __future__ import annotations

import json
import sqlite3

import pytest

from hacienda_gpt import settings
from hacienda_gpt.decision.state_store import CaseVersionConflictError
from hacienda_gpt.eventos import emitir, emitir_cambio_modulo, emitir_indices, emitir_novedades, productores
from hacienda_gpt.eventos.bus import Bus
from hacienda_gpt.eventos.tipos import (
    CARGOS_IMPORTADOS,
    INDICE_PUBLICADO,
    MODULO_ACTIVADO,
    NORMA_BOE,
    PERFIL_CAMBIADO,
    EventoInvalidoError,
    validar,
)
from hacienda_gpt.proactive.novedades import Novedad
from tests.proactive import make_case
from .ayudas import stores


def _eventos(db: str) -> list[tuple[str, str | None, dict]]:
    with sqlite3.connect(db) as conn:
        filas = conn.execute("SELECT tipo, case_id, payload_json FROM eventos ORDER BY id").fetchall()
    return [(t, c, json.loads(p)) for t, c, p in filas]


def test_a_fact_change_emits_names_only_in_the_same_transaction(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    productores.instalar()
    case = cases.save_case(make_case("c1", agua_personas=3, comunidad_autonoma="Madrid"))
    cambiado = make_case("c1", agua_personas=4, comunidad_autonoma="Madrid").model_copy(
        update={"version": case.version}
    )
    cases.save_case(cambiado)

    eventos = _eventos(db)
    assert [(t, c) for t, c, _ in eventos] == [(PERFIL_CAMBIADO, "c1"), (PERFIL_CAMBIADO, "c1")]
    assert eventos[0][2] == {"hechos": ["agua_personas", "comunidad_autonoma"], "borrados": []}
    assert eventos[1][2] == {"hechos": ["agua_personas"], "borrados": []}
    # Values never travel with the event.
    with sqlite3.connect(db) as conn:
        volcado = str(conn.execute("SELECT * FROM eventos").fetchall())
    assert "Madrid" not in volcado and "4.0" not in volcado


def test_no_change_no_event_and_a_rolled_back_save_leaves_no_event(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    productores.instalar()
    case = cases.save_case(make_case("c1", agua_personas=3))
    cases.save_case(case)  # saved again with the same facts
    assert len(_eventos(db)) == 1  # the creation only

    viejo = make_case("c1", agua_personas=9)  # version 1, but the stored one is 2 now
    with pytest.raises(CaseVersionConflictError):
        cases.save_case(viejo.model_copy(update={"version": 0}))
    assert len(_eventos(db)) == 1


def test_removed_facts_and_detected_charges(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    productores.instalar()
    case = cases.save_case(make_case("c1", agua_personas=3, seguros_poliza="X"))
    case = cases.save_case(
        make_case("c1", agua_personas=3, suscripciones_servicios="[]").model_copy(update={"version": case.version})
    )
    tipos = [(t, p) for t, _c, p in _eventos(db)][1:]
    assert tipos == [
        (CARGOS_IMPORTADOS, {"hechos": ["suscripciones_servicios"]}),
        (PERFIL_CAMBIADO, {"hechos": [], "borrados": ["seguros_poliza"]}),
    ]


def test_the_fiscal_year_counts_as_a_fact(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    productores.instalar()
    case = cases.save_case(make_case("c1", tax_period="2025", agua_personas=3))
    cases.save_case(case.model_copy(update={"tax_period": "2026"}))
    assert _eventos(db)[-1][2] == {"hechos": ["periodo_fiscal"], "borrados": []}


def test_switched_off_writes_nothing_and_without_hooks_nothing_is_written(tmp_path, monkeypatch) -> None:
    db, cases, _ = stores(tmp_path)
    cases.save_case(make_case("c1", agua_personas=3))  # not installed
    productores.instalar()
    monkeypatch.setattr(settings, "EVENTOS_ACTIVOS", False)
    cases.save_case(make_case("c2", agua_personas=3))
    with sqlite3.connect(db) as conn:
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "eventos" not in tablas


def test_a_broken_bus_never_blocks_the_save(tmp_path, monkeypatch) -> None:
    _db, cases, _ = stores(tmp_path)
    productores.instalar()

    def roto(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(productores, "emitir_en", roto)
    assert cases.save_case(make_case("c1", agua_personas=3)).case_id == "c1"
    assert cases.get_case("c1") is not None


def test_erasing_a_case_erases_its_events_deliveries_and_timers(tmp_path) -> None:
    db, cases, _ = stores(tmp_path)
    productores.instalar()
    cases.save_case(make_case("c-borrar", agua_personas=3))
    cases.save_case(make_case("c-queda", agua_personas=3))
    bus = Bus(db, modulos_activos=lambda _c: [])
    bus.programar("c-borrar", "agua", bus.dia())
    bus.programar("c-queda", "agua", bus.dia())
    assert cases.delete_case("c-borrar")
    with sqlite3.connect(db) as conn:
        volcado = "\n".join(
            str(conn.execute(f"SELECT * FROM {t}").fetchall())
            for t in ("eventos", "eventos_entregas", "eventos_temporizadores")
        )
    assert "c-borrar" not in volcado and "c-queda" in volcado


def _novedad(boe_id: str = "BOE-A-2026-99999", relevancia: str = "alta") -> Novedad:
    return Novedad(
        boe_id=boe_id,
        fecha="2026-11-14",
        titulo="Ley 9/2026, de medidas tributarias",
        rango="Ley",
        ambito="estatal",
        tributos=["IRPF"],
        relevancia=relevancia,
        analizada=True,
        url=f"https://www.boe.es/diario_boe/txt.php?id={boe_id}",
    )


def test_global_producers_are_idempotent_and_never_raise(tmp_path) -> None:
    db = str(tmp_path / "estado.sqlite3")
    assert emitir_novedades(db, [_novedad(), _novedad("BOE-A-2026-1", relevancia="media")]) == 1
    assert emitir_novedades(db, [_novedad()]) == 0  # a re-run of boe-novedades
    assert emitir_indices(["BOE-A-2026-2", "BOE-A-2026-1"], db) is not None
    assert emitir_indices(["BOE-A-2026-1", "BOE-A-2026-2"], db) is None
    assert [t for t, _c, _p in _eventos(db)] == [NORMA_BOE, INDICE_PUBLICADO]
    assert _eventos(db)[1][2] == {"boe_ids": ["BOE-A-2026-1", "BOE-A-2026-2"]}
    # A bad event or an unwritable database is logged, never raised into the producer.
    assert emitir(db, "no.existe") is None
    assert emitir(str(tmp_path / "no" / "\0" / "x.db"), NORMA_BOE, payload={}) is None


def test_case_events_need_the_erasure_hook_in_the_process(tmp_path) -> None:
    db = str(tmp_path / "estado.sqlite3")
    assert emitir_cambio_modulo(db, ["c1", "c2"], "agua", True) == 0  # not installed: nothing about a case
    productores.instalar()
    assert emitir_cambio_modulo(db, ["c1", "c2"], "agua", True) == 2
    assert {(t, c, p["modulo"]) for t, c, p in _eventos(db)} == {
        (MODULO_ACTIVADO, "c1", "agua"),
        (MODULO_ACTIVADO, "c2", "agua"),
    }


@pytest.mark.parametrize(
    ("tipo", "case_id", "payload"),
    [
        ("inventado", "c1", {}),
        (NORMA_BOE, "c1", {}),
        (PERFIL_CAMBIADO, None, {}),
        (MODULO_ACTIVADO, "c1", {}),
        (PERFIL_CAMBIADO, "c1", {"hechos": ["x" * 20_000]}),
    ],
)
def test_events_are_validated(tipo, case_id, payload) -> None:
    with pytest.raises(EventoInvalidoError):
        validar(tipo, case_id, payload)

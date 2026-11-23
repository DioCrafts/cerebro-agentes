"""An action confirmed by the user always ends decided: never stuck in «ejecutando»."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from asistente.nucleo.decision.state_store import CaseVersionConflictError
from asistente.nucleo.modulos.acciones import (
    MENSAJE_INTERRUMPIDA,
    MENSAJE_SIN_GUARDAR,
    AccionPendiente,
    AccionStore,
    completar_accion,
)


@pytest.fixture
def store(tmp_path) -> AccionStore:
    s = AccionStore(str(tmp_path / "acciones.sqlite3"))
    yield s
    s.close()


def _en_marcha(store: AccionStore) -> str:
    accion = store.add(AccionPendiente(case_id="c1", modulo="m", herramienta="h", resumen="hacer algo"))
    assert store.transicion(accion.id, desde="pendiente", hacia="ejecutando") is not None
    return accion.id


def test_a_crash_of_the_tool_ends_failed(store: AccionStore) -> None:
    accion_id = _en_marcha(store)

    def revienta() -> tuple[str, bool]:
        raise RuntimeError("detalle interno")

    final = completar_accion(store, accion_id, ejecutar=revienta)
    assert final.estado == "fallida" and "detalle interno" not in (final.resultado or "")


def test_a_version_conflict_is_retried_and_then_saved(store: AccionStore) -> None:
    accion_id = _en_marcha(store)
    intentos: list[int] = []

    def guardar() -> None:
        intentos.append(1)
        if len(intentos) < 2:
            raise CaseVersionConflictError("c1", 1, 2)

    final = completar_accion(store, accion_id, ejecutar=lambda: ("hecho", False), guardar=guardar)
    assert final.estado == "ejecutada" and len(intentos) == 2


def test_a_save_that_keeps_failing_ends_failed_not_stuck(store: AccionStore) -> None:
    accion_id = _en_marcha(store)

    def guardar() -> None:
        raise CaseVersionConflictError("c1", 1, 2)

    final = completar_accion(store, accion_id, ejecutar=lambda: ("hecho", False), guardar=guardar)
    assert final.estado == "fallida" and final.resultado == MENSAJE_SIN_GUARDAR


def test_old_running_actions_are_recovered_as_failed(store: AccionStore) -> None:
    vieja = _en_marcha(store)
    reciente = _en_marcha(store)
    accion = store.get(vieja)
    assert accion is not None
    antigua = accion.model_copy(update={"iniciada_at": datetime.now(UTC) - timedelta(hours=1)})
    with store._get() as conn:
        conn.execute("UPDATE acciones_pendientes SET payload_json = ? WHERE id = ?", (antigua.model_dump_json(), vieja))
    assert store.recuperar_atascadas(mas_de=timedelta(minutes=15)) == 1
    assert store.get(vieja).estado == "fallida" and store.get(vieja).resultado == MENSAJE_INTERRUMPIDA
    assert store.get(reciente).estado == "ejecutando"

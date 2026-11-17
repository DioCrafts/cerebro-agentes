"""Shared fixtures of the decision-memory tests: a fixed «today» and a private cases database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import pytest

from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.memoria import almacen, modelo, reloj, servicio
from asistente.nucleo.memoria.almacen import olvidar_almacenes
from asistente.nucleo.modulos import registro
from asistente.nucleo.proactive.store import NotificationStore
from asistente.plataforma import settings

# The accounts API fixtures (``env``: AUTH_MODE=required over one private database file).
from tests.auth.conftest import CONSENTS, PASSWORD, Env, env, fast_scrypt  # noqa: F401

HOY = date(2026, 11, 16)  # a Monday


class Reloj:
    """«Today» for the decisions, movable by the test."""

    def __init__(self) -> None:
        self.dia = HOY

    def __call__(self) -> date:
        return self.dia


@pytest.fixture(autouse=True)
def registro_limpio() -> Iterator[None]:
    """Every test starts and ends with the real (discovered) module registry; fakes are installed per test."""
    registro.reiniciar()
    yield
    registro.reiniciar()


@pytest.fixture(autouse=True)
def reloj_fijo(monkeypatch: pytest.MonkeyPatch) -> Iterator[Reloj]:
    fijo = Reloj()
    monkeypatch.setattr(reloj, "hoy", fijo)

    # The moment a decision is recorded follows the same day (midday UTC), so the date a test reads in a
    # message («el 16/11/2026 nos dijiste…») does not depend on the day the suite runs.
    def ahora_fijo() -> datetime:
        return datetime.combine(fijo.dia, time(12), tzinfo=UTC)

    for modulo in (modelo, almacen, servicio):
        monkeypatch.setattr(modulo, "ahora", ahora_fijo)
    yield fijo
    olvidar_almacenes()


@pytest.fixture
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A private cases database (with the cases table, as in production) that the agent also uses."""
    path = str(tmp_path / "estado.sqlite3")
    SQLiteCaseStateStore(path)
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", path)
    return path


@pytest.fixture
def casos(db: str) -> SQLiteCaseStateStore:
    return SQLiteCaseStateStore(db)


@pytest.fixture
def bandeja(db: str) -> NotificationStore:
    return NotificationStore(db)


def guardar(casos: SQLiteCaseStateStore, case: CaseState) -> CaseState:
    actual = casos.get_case(case.case_id)
    if actual is not None:
        case = case.model_copy(update={"version": actual.version})
    return casos.save_case(case)

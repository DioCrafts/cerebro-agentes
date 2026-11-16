"""Shared fixtures of the decision-memory tests: a fixed «today» and a private cases database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from hacienda_gpt import settings
from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.memoria import reloj
from hacienda_gpt.memoria.almacen import olvidar_almacenes
from hacienda_gpt.modulos import registro
from hacienda_gpt.proactive.store import NotificationStore

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

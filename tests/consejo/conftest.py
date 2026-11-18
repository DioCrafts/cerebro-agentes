"""Shared fixtures of the council's integration tests: a private cases database, no accounts, fixed «today»."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from asistente.nucleo.consejo import servicio as servicio_mod
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.almacen import olvidar_almacenes
from asistente.plataforma import settings
from .fakes import HOY, caso


@pytest.fixture(autouse=True)
def entorno_consejo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No accounts, the council on, nothing started in a background thread unless a test asks, no push.

    Only for this folder's own tests: the engine's (``motor/``) and the evaluation's (``evaluacion/``) set up their
    own environment."""
    if {"motor", "evaluacion"} & set(request.node.path.parts):
        yield
        return
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "CONSEJO_ACTIVO", True)
    monkeypatch.setattr(settings, "CONSEJO_EN_SEGUNDO_PLANO", False)
    monkeypatch.setattr(settings, "CONSEJO_FAMILIAS", "")
    monkeypatch.setattr(reloj, "hoy", lambda: HOY)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    yield
    olvidar_almacenes()
    servicio_mod._servicios.clear()


@pytest.fixture
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    path = str(tmp_path / "estado.sqlite3")
    casos = SQLiteCaseStateStore(path)
    casos.save_case(caso("c1", "u1", edad=44, comunidad_autonoma="Madrid", rendimiento_trabajo_bruto=38000))
    casos.save_case(caso("c2", "u2"))
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", path)
    return path

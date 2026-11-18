"""Fixtures of the council's evaluation tests: no test reads or writes the repository's active variant."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from asistente.plataforma import settings


@pytest.fixture(autouse=True)
def variante_activa_aislada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    ruta = tmp_path / "variante_activa.json"
    monkeypatch.setattr(settings, "EVALUACION_VARIANTE_ACTIVA_PATH", str(ruta))
    monkeypatch.setattr(settings, "CONSEJO_SEMILLA", "", raising=False)
    monkeypatch.setattr(settings, "CONSEJO_FAMILIAS", "", raising=False)
    # The engine's default four seats, whatever the environment says (the tests count their calls).
    monkeypatch.setattr(
        settings, "CONSEJO_MIEMBROS", "prudente,fiscal,liquidez_familia,abogado_del_diablo", raising=False
    )
    monkeypatch.setattr(settings, "EVALUACION_CONSEJO", False, raising=False)
    yield ruta

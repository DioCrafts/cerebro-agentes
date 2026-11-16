from collections.abc import Iterator
from pathlib import Path

import pytest

from asistente.plataforma import settings


@pytest.fixture(autouse=True)
def variante_activa_aislada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """No test reads or writes the repository's active variant: each gets its own (absent) file."""
    ruta = tmp_path / "variante_activa.json"
    monkeypatch.setattr(settings, "EVALUACION_VARIANTE_ACTIVA_PATH", str(ruta))
    yield ruta

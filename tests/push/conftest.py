"""Push tests: the inbox write hook pushes inline, so what reaches the fake push service is deterministic."""

from __future__ import annotations

import pytest

from asistente.plataforma.push import envio


@pytest.fixture(autouse=True)
def _push_en_linea(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envio, "EN_SEGUNDO_PLANO", False)

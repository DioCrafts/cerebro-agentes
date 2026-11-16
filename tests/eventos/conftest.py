from __future__ import annotations

from collections.abc import Iterator

import pytest

from hacienda_gpt.eventos import despacho, productores
from hacienda_gpt.modulos import registro


@pytest.fixture(autouse=True)
def limpio() -> Iterator[None]:
    """Every test starts and ends with the real registry, no bus hooks and no dispatcher thread."""
    registro.reiniciar()
    productores.desinstalar()
    yield
    despacho.detener()
    productores.desinstalar()
    registro.reiniciar()

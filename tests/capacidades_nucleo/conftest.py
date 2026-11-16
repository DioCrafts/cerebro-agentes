from collections.abc import Iterator

import pytest

from asistente.capacidades import registro as registro_capacidades
from asistente.nucleo.modulos import registro as registro_modulos


@pytest.fixture(autouse=True)
def registros_limpios() -> Iterator[None]:
    """Every test starts and ends with the real (discovered) registries; fakes are installed per test."""
    registro_capacidades.reiniciar()
    registro_modulos.reiniciar()
    yield
    registro_capacidades.reiniciar()
    registro_modulos.reiniciar()

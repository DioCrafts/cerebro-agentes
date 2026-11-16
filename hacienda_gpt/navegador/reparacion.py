"""Moved to :mod:`hacienda_gpt.capacidades.navegador.reparacion`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import reparacion as _modulo
from hacienda_gpt.capacidades.navegador.reparacion import (
    ESQUEMA as ESQUEMA,
)
from hacienda_gpt.capacidades.navegador.reparacion import (
    SYSTEM as SYSTEM,
)
from hacienda_gpt.capacidades.navegador.reparacion import (
    Reparador as Reparador,
)
from hacienda_gpt.capacidades.navegador.reparacion import (
    ReparadorLLM as ReparadorLLM,
)
from hacienda_gpt.capacidades.navegador.reparacion import (
    describir_paso as describir_paso,
)
from hacienda_gpt.capacidades.navegador.reparacion import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.navegador.reparacion import (
    puerta as puerta,
)

sys.modules[__name__] = _modulo

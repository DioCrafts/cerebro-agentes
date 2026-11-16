"""Moved to :mod:`hacienda_gpt.capacidades.navegador.modulo`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import modulo as _modulo
from hacienda_gpt.capacidades.navegador.modulo import (  # noqa: F401 (the names, for type checkers)
    DIAS_POR_DEFECTO as DIAS_POR_DEFECTO,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    HECHO_CUPS_LUZ as HECHO_CUPS_LUZ,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    MODULO as MODULO,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    _error as _error,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    _fecha as _fecha,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    descargar_consumo_datadis as descargar_consumo_datadis,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    herramientas as herramientas,
)
from hacienda_gpt.capacidades.navegador.modulo import (
    presentar_tramite as presentar_tramite,
)

sys.modules[__name__] = _modulo

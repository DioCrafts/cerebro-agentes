"""Moved to :mod:`hacienda_gpt.capacidades.correo.herramientas`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import herramientas as _modulo
from hacienda_gpt.capacidades.correo.herramientas import (
    _SIN_CONEXION as _SIN_CONEXION,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    HERRAMIENTAS as HERRAMIENTAS,
)
from hacienda_gpt.capacidades.correo.herramientas import (  # noqa: F401 (the names, for type checkers)
    MAX_RESULTADOS as MAX_RESULTADOS,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    _error as _error,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    _estado as _estado,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    _fecha as _fecha,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    _herramienta as _herramienta,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    _sin_datos as _sin_datos,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    correo_buscar as correo_buscar,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    correo_propuestas as correo_propuestas,
)
from hacienda_gpt.capacidades.correo.herramientas import (
    correo_resumen as correo_resumen,
)

sys.modules[__name__] = _modulo

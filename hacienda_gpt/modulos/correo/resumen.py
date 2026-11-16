"""Moved to :mod:`hacienda_gpt.capacidades.correo.resumen`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import resumen as _modulo
from hacienda_gpt.capacidades.correo.resumen import (
    SECCIONES as SECCIONES,
)
from hacienda_gpt.capacidades.correo.resumen import (  # noqa: F401 (the names, for type checkers)
    Periodo as Periodo,
)
from hacienda_gpt.capacidades.correo.resumen import (
    Resumen as Resumen,
)
from hacienda_gpt.capacidades.correo.resumen import (
    a_dict as a_dict,
)
from hacienda_gpt.capacidades.correo.resumen import (
    construir as construir,
)
from hacienda_gpt.capacidades.correo.resumen import (
    linea as linea,
)
from hacienda_gpt.capacidades.correo.resumen import (
    linea_propuesta as linea_propuesta,
)
from hacienda_gpt.capacidades.correo.resumen import (
    resumen_dict as resumen_dict,
)
from hacienda_gpt.capacidades.correo.resumen import (
    texto as texto,
)

sys.modules[__name__] = _modulo

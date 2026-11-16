"""Moved to :mod:`hacienda_gpt.capacidades.correo.gmail`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import gmail as _modulo
from hacienda_gpt.capacidades.correo.gmail import (
    _ID_RE as _ID_RE,
)
from hacienda_gpt.capacidades.correo.gmail import (
    CABECERAS_METADATOS as CABECERAS_METADATOS,
)
from hacienda_gpt.capacidades.correo.gmail import (
    MAX_PAGINAS as MAX_PAGINAS,
)
from hacienda_gpt.capacidades.correo.gmail import (
    ClienteGmail as ClienteGmail,
)
from hacienda_gpt.capacidades.correo.gmail import (
    HistorialCaducadoError as HistorialCaducadoError,
)
from hacienda_gpt.capacidades.correo.gmail import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)

sys.modules[__name__] = _modulo

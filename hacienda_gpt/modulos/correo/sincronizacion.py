"""Moved to :mod:`hacienda_gpt.capacidades.correo.sincronizacion`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import sincronizacion as _modulo
from hacienda_gpt.capacidades.correo.sincronizacion import (
    CATEGORIAS_CON_ASUNTO as CATEGORIAS_CON_ASUNTO,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    EVENTO as EVENTO,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    OMITIR_ETIQUETAS as OMITIR_ETIQUETAS,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    SOLO_METADATOS as SOLO_METADATOS,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    ResultadoSincronizacion as ResultadoSincronizacion,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    _emitir as _emitir,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    _sincronizar as _sincronizar,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    conservacion_dias as conservacion_dias,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    elemento_de as elemento_de,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    procesar as procesar,
)
from hacienda_gpt.capacidades.correo.sincronizacion import (
    sincronizar as sincronizar,
)

sys.modules[__name__] = _modulo

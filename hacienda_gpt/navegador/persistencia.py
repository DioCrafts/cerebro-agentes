"""Moved to :mod:`hacienda_gpt.capacidades.navegador.persistencia`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import persistencia as _modulo
from hacienda_gpt.capacidades.navegador.persistencia import (
    ACTIVOS as ACTIVOS,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    LATIDO_CADUCA_S as LATIDO_CADUCA_S,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    LATIDO_S as LATIDO_S,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    MENSAJE_HUERFANA as MENSAJE_HUERFANA,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    AlmacenTareas as AlmacenTareas,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    Reclamacion as Reclamacion,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    _fecha as _fecha,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    _iso as _iso,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    _LimiteAlcanzadoError as _LimiteAlcanzadoError,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    _parametros_a_texto as _parametros_a_texto,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    huella_usuario as huella_usuario,
)
from hacienda_gpt.capacidades.navegador.persistencia import (
    identidad_de_proceso as identidad_de_proceso,
)
from hacienda_gpt.capacidades.navegador.persistencia import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)

sys.modules[__name__] = _modulo

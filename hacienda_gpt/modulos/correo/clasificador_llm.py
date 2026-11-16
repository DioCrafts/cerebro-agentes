"""Moved to :mod:`hacienda_gpt.capacidades.correo.clasificador_llm`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import clasificador_llm as _modulo
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    CAMPOS as CAMPOS,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    ESQUEMA as ESQUEMA,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    MAX_TEXTO as MAX_TEXTO,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    NIVEL as NIVEL,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    PERMITIDAS as PERMITIDAS,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    PROPOSITO as PROPOSITO,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    SISTEMA as SISTEMA,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    _fabrica as _fabrica,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    _proveedor as _proveedor,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    _valor as _valor,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    activo as activo,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    configurar as configurar,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.correo.clasificador_llm import (
    refinar as refinar,
)

sys.modules[__name__] = _modulo

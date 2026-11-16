"""Moved to :mod:`hacienda_gpt.capacidades.correo.config`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import config as _modulo
from hacienda_gpt.capacidades.correo.config import (
    AUTH_URL as AUTH_URL,
)
from hacienda_gpt.capacidades.correo.config import (
    GMAIL_API_URL as GMAIL_API_URL,
)
from hacienda_gpt.capacidades.correo.config import (
    MAX_MENSAJES_POR_SINCRONIZACION as MAX_MENSAJES_POR_SINCRONIZACION,
)
from hacienda_gpt.capacidades.correo.config import (
    REVOKE_URL as REVOKE_URL,
)
from hacienda_gpt.capacidades.correo.config import (  # noqa: F401 (the names, for type checkers)
    SCOPE_LECTURA as SCOPE_LECTURA,
)
from hacienda_gpt.capacidades.correo.config import (
    SCOPES_FASE_1 as SCOPES_FASE_1,
)
from hacienda_gpt.capacidades.correo.config import (
    TOKEN_URL as TOKEN_URL,
)
from hacienda_gpt.capacidades.correo.config import (
    VENTANA_DIAS_MAX as VENTANA_DIAS_MAX,
)
from hacienda_gpt.capacidades.correo.config import (
    VENTANA_DIAS_POR_DEFECTO as VENTANA_DIAS_POR_DEFECTO,
)
from hacienda_gpt.capacidades.correo.config import (
    Configuracion as Configuracion,
)
from hacienda_gpt.capacidades.correo.config import (
    _entero as _entero,
)
from hacienda_gpt.capacidades.correo.config import (
    configuracion as configuracion,
)

sys.modules[__name__] = _modulo

"""Moved to :mod:`hacienda_gpt.capacidades.correo.http`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import http as _modulo
from hacienda_gpt.capacidades.correo.http import (
    MAX_RESPUESTA as MAX_RESPUESTA,
)
from hacienda_gpt.capacidades.correo.http import (  # noqa: F401 (the names, for type checkers)
    TIMEOUT_SEGUNDOS as TIMEOUT_SEGUNDOS,
)
from hacienda_gpt.capacidades.correo.http import (
    CorreoError as CorreoError,
)
from hacienda_gpt.capacidades.correo.http import (
    RespuestaHttp as RespuestaHttp,
)
from hacienda_gpt.capacidades.correo.http import (
    Transporte as Transporte,
)
from hacienda_gpt.capacidades.correo.http import (
    _transporte as _transporte,
)
from hacienda_gpt.capacidades.correo.http import (
    configurar_transporte as configurar_transporte,
)
from hacienda_gpt.capacidades.correo.http import (
    transporte as transporte,
)
from hacienda_gpt.capacidades.correo.http import (
    transporte_requests as transporte_requests,
)

sys.modules[__name__] = _modulo

"""Moved to :mod:`hacienda_gpt.capacidades.correo.oauth`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import oauth as _modulo
from hacienda_gpt.capacidades.correo.oauth import (
    ConexionCaducadaError as ConexionCaducadaError,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _b64 as _b64,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _cache as _cache,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _cache_lock as _cache_lock,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _exigir as _exigir,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _guardar_en_cache as _guardar_en_cache,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _revocar as _revocar,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _revocar_conexion as _revocar_conexion,
)
from hacienda_gpt.capacidades.correo.oauth import (
    _token as _token,
)
from hacienda_gpt.capacidades.correo.oauth import (
    completar as completar,
)
from hacienda_gpt.capacidades.correo.oauth import (
    desconectar as desconectar,
)
from hacienda_gpt.capacidades.correo.oauth import (
    iniciar as iniciar,
)
from hacienda_gpt.capacidades.correo.oauth import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.correo.oauth import (
    olvidar_cache as olvidar_cache,
)
from hacienda_gpt.capacidades.correo.oauth import (
    revocar_de_usuario as revocar_de_usuario,
)
from hacienda_gpt.capacidades.correo.oauth import (
    revocar_token_cifrado as revocar_token_cifrado,
)
from hacienda_gpt.capacidades.correo.oauth import (
    token_de_acceso as token_de_acceso,
)

sys.modules[__name__] = _modulo

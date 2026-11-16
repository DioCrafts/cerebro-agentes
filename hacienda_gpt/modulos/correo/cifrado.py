"""Moved to :mod:`hacienda_gpt.capacidades.correo.cifrado`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import cifrado as _modulo
from hacienda_gpt.capacidades.correo.cifrado import (  # noqa: F401 (the names, for type checkers)
    CifradoNoDisponibleError as CifradoNoDisponibleError,
)
from hacienda_gpt.capacidades.correo.cifrado import (
    _fernet as _fernet,
)
from hacienda_gpt.capacidades.correo.cifrado import (
    cifrar as cifrar,
)
from hacienda_gpt.capacidades.correo.cifrado import (
    descifrar as descifrar,
)
from hacienda_gpt.capacidades.correo.cifrado import (
    disponible as disponible,
)
from hacienda_gpt.capacidades.correo.cifrado import (
    nueva_clave as nueva_clave,
)
from hacienda_gpt.capacidades.correo.cifrado import (
    recifrar as recifrar,
)

sys.modules[__name__] = _modulo

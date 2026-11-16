"""Moved to :mod:`hacienda_gpt.capacidades.correo.avisos`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import avisos as _modulo
from hacienda_gpt.capacidades.correo.avisos import (  # noqa: F401 (the names, for type checkers)
    DIAS_ADMINISTRACION as DIAS_ADMINISTRACION,
)
from hacienda_gpt.capacidades.correo.avisos import (
    DIAS_PHISHING as DIAS_PHISHING,
)
from hacienda_gpt.capacidades.correo.avisos import (
    HORIZONTE_ACCION as HORIZONTE_ACCION,
)
from hacienda_gpt.capacidades.correo.avisos import (
    _asunto as _asunto,
)
from hacienda_gpt.capacidades.correo.avisos import (
    _dominio as _dominio,
)
from hacienda_gpt.capacidades.correo.avisos import (
    aviso_accion as aviso_accion,
)
from hacienda_gpt.capacidades.correo.avisos import (
    aviso_administracion as aviso_administracion,
)
from hacienda_gpt.capacidades.correo.avisos import (
    aviso_phishing as aviso_phishing,
)
from hacienda_gpt.capacidades.correo.avisos import (
    generar_avisos as generar_avisos,
)

sys.modules[__name__] = _modulo

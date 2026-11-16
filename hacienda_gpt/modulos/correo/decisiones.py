"""Moved to :mod:`hacienda_gpt.capacidades.correo.decisiones`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import decisiones as _modulo
from hacienda_gpt.capacidades.correo.decisiones import (
    TipoSilencio as TipoSilencio,
)
from hacienda_gpt.capacidades.correo.decisiones import (
    ambito_de as ambito_de,
)
from hacienda_gpt.capacidades.correo.decisiones import (
    ambito_silencio as ambito_silencio,
)
from hacienda_gpt.capacidades.correo.decisiones import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.correo.decisiones import (
    silenciar as silenciar,
)
from hacienda_gpt.capacidades.correo.decisiones import (
    suprimido as suprimido,
)
from hacienda_gpt.capacidades.correo.decisiones import (
    vigentes as vigentes,
)

sys.modules[__name__] = _modulo

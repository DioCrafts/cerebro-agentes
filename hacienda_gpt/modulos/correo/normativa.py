"""Moved to :mod:`hacienda_gpt.capacidades.correo.normativa`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import normativa as _modulo
from hacienda_gpt.capacidades.correo.normativa import (
    _NUMEROS as _NUMEROS,
)
from hacienda_gpt.capacidades.correo.normativa import (  # noqa: F401 (the names, for type checkers)
    DATOS as DATOS,
)
from hacienda_gpt.capacidades.correo.normativa import (
    FICHEROS_LEGALES as FICHEROS_LEGALES,
)
from hacienda_gpt.capacidades.correo.normativa import (
    ORTOGRAFIA as ORTOGRAFIA,
)
from hacienda_gpt.capacidades.correo.normativa import (
    Evidencia as Evidencia,
)
from hacienda_gpt.capacidades.correo.normativa import (
    FuentePrincipal as FuentePrincipal,
)
from hacienda_gpt.capacidades.correo.normativa import (
    FuenteRegla as FuenteRegla,
)
from hacienda_gpt.capacidades.correo.normativa import (
    Normativa as Normativa,
)
from hacienda_gpt.capacidades.correo.normativa import (
    Regla as Regla,
)
from hacienda_gpt.capacidades.correo.normativa import (
    _leer as _leer,
)
from hacienda_gpt.capacidades.correo.normativa import (
    _Modelo as _Modelo,
)
from hacienda_gpt.capacidades.correo.normativa import (
    cargar as cargar,
)
from hacienda_gpt.capacidades.correo.normativa import (
    cifra_a_decimal as cifra_a_decimal,
)
from hacienda_gpt.capacidades.correo.normativa import (
    comprobar as comprobar,
)
from hacienda_gpt.capacidades.correo.normativa import (
    grupos_legales as grupos_legales,
)
from hacienda_gpt.capacidades.correo.normativa import (
    main as main,
)

sys.modules[__name__] = _modulo

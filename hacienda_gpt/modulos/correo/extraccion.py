"""Moved to :mod:`hacienda_gpt.capacidades.correo.extraccion`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import extraccion as _modulo
from hacienda_gpt.capacidades.correo.extraccion import (
    _ANIO as _ANIO,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _CODIGO as _CODIGO,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _FECHA_RE as _FECHA_RE,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _IMPORTE_RE as _IMPORTE_RE,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _MES_RE as _MES_RE,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _MESES as _MESES,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _NUMERO as _NUMERO,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _NUMERO_LARGO as _NUMERO_LARGO,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _PLAZO_RE as _PLAZO_RE,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _POTENCIA_RE as _POTENCIA_RE,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _TOTAL_RE as _TOTAL_RE,
)
from hacienda_gpt.capacidades.correo.extraccion import (  # noqa: F401 (the names, for type checkers)
    MAX_FRAGMENTO as MAX_FRAGMENTO,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    Extraido as Extraido,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    _anio_probable as _anio_probable,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    aparece as aparece,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    enmascarar as enmascarar,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    fecha_tras as fecha_tras,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    fechas as fechas,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    fragmento as fragmento,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    importe_decimal as importe_decimal,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    importe_total as importe_total,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    importes as importes,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    periodo as periodo,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    plazo as plazo,
)
from hacienda_gpt.capacidades.correo.extraccion import (
    potencia_kw as potencia_kw,
)

sys.modules[__name__] = _modulo

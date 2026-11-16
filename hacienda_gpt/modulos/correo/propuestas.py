"""Moved to :mod:`hacienda_gpt.capacidades.correo.propuestas`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import propuestas as _modulo
from hacienda_gpt.capacidades.correo.propuestas import (  # noqa: F401 (the names, for type checkers)
    ENTRADA_COMPRA as ENTRADA_COMPRA,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    ENTRADA_POTENCIA as ENTRADA_POTENCIA,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    HECHO_POTENCIA as HECHO_POTENCIA,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    HERRAMIENTA_COMPRAS as HERRAMIENTA_COMPRAS,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    NOMBRES_DESTINO as NOMBRES_DESTINO,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    Aplicacion as Aplicacion,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    Registrador as Registrador,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    _evidencias as _evidencias,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    _origen as _origen,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    aplicar as aplicar,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    destino_de as destino_de,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    eur as eur,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    fecha_es as fecha_es,
)
from hacienda_gpt.capacidades.correo.propuestas import (
    generar as generar,
)

sys.modules[__name__] = _modulo

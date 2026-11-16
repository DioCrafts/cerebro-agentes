"""Moved to :mod:`hacienda_gpt.capacidades.navegador.ejecutor`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import ejecutor as _modulo
from hacienda_gpt.capacidades.navegador.ejecutor import (
    ESPERA_SESION_S as ESPERA_SESION_S,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    FALLOS_DE_FORMATO as FALLOS_DE_FORMATO,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    MAX_FILAS_RESUMEN as MAX_FILAS_RESUMEN,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    MAX_SESION_BYTES as MAX_SESION_BYTES,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    Ejecutor as Ejecutor,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    EstadoFinal as EstadoFinal,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    EstadoPaso as EstadoPaso,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    Evento as Evento,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    Modo as Modo,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    Observador as Observador,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    PasoEjecutado as PasoEjecutado,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    ResultadoEjecucion as ResultadoEjecucion,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    _CanceladoError as _CanceladoError,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    _FalloPasoError as _FalloPasoError,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    _Nulo as _Nulo,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    ejecutable_chromium as ejecutable_chromium,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (
    filtrar_sesion as filtrar_sesion,
)
from hacienda_gpt.capacidades.navegador.ejecutor import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)

sys.modules[__name__] = _modulo

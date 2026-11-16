"""Moved to :mod:`hacienda_gpt.capacidades.navegador.vigilancia`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import vigilancia as _modulo
from hacienda_gpt.capacidades.navegador.vigilancia import (
    _CASE_ID as _CASE_ID,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    DIAS_DIAGNOSTICO as DIAS_DIAGNOSTICO,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    _canarios as _canarios,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    _dir_diagnostico as _dir_diagnostico,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    aplicar_reparacion as aplicar_reparacion,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    borrar_de_caso as borrar_de_caso,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    diagnosticos_de as diagnosticos_de,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    ensayar_guion as ensayar_guion,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    guardar_diagnostico as guardar_diagnostico,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    observar_ejecucion as observar_ejecucion,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    proponer_reparacion as proponer_reparacion,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    propuestas_de as propuestas_de,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    purgar as purgar,
)
from hacienda_gpt.capacidades.navegador.vigilancia import (
    registrar_fuentes as registrar_fuentes,
)

sys.modules[__name__] = _modulo

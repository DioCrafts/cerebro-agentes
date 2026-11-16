"""Moved to :mod:`hacienda_gpt.capacidades.navegador.tareas`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import tareas as _modulo
from hacienda_gpt.capacidades.navegador.tareas import (
    ACTIVOS as ACTIVOS,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    CLAVE_MODULO as CLAVE_MODULO,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    HERRAMIENTA_PRESENTAR as HERRAMIENTA_PRESENTAR,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    MAX_EVENTOS as MAX_EVENTOS,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    MINUTOS_TRAS_TERMINAR as MINUTOS_TRAS_TERMINAR,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    TERMINADOS as TERMINADOS,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    TEXTO_CONSENTIMIENTO as TEXTO_CONSENTIMIENTO,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    TEXTO_CONSENTIMIENTO_VERSION as TEXTO_CONSENTIMIENTO_VERSION,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    VENTANA_LIMITE_S as VENTANA_LIMITE_S,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    EstadoTarea as EstadoTarea,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    EventoTarea as EventoTarea,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    GestorTareas as GestorTareas,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    Servicios as Servicios,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    Tarea as Tarea,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    TareaError as TareaError,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    _gestor as _gestor,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    _gestor_lock as _gestor_lock,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    _modulo_de_herramienta_activo as _modulo_de_herramienta_activo,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    _Observador as _Observador,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    consentimiento as consentimiento,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    disponibles as disponibles,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    ejecutar_herramienta_de_modulo as ejecutar_herramienta_de_modulo,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    gestor as gestor,
)
from hacienda_gpt.capacidades.navegador.tareas import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    modos_permitidos as modos_permitidos,
)
from hacienda_gpt.capacidades.navegador.tareas import (
    sustitutos as sustitutos,
)

sys.modules[__name__] = _modulo

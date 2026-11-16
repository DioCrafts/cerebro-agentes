"""Moved to :mod:`hacienda_gpt.capacidades.navegador.guion`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.navegador import guion as _modulo
from hacienda_gpt.capacidades.navegador.guion import (
    _CAMBIA_ESTADO as _CAMBIA_ESTADO,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _CREDENCIAL as _CREDENCIAL,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _CUPS as _CUPS,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _ID as _ID,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _M as _M,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _MOTOR as _MOTOR,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _PLANTILLA as _PLANTILLA,
)
from hacienda_gpt.capacidades.navegador.guion import (
    GUIONES_INCORPORADOS as GUIONES_INCORPORADOS,
)
from hacienda_gpt.capacidades.navegador.guion import (
    MAX_PASOS as MAX_PASOS,
)
from hacienda_gpt.capacidades.navegador.guion import (
    MAX_SELECTOR as MAX_SELECTOR,
)
from hacienda_gpt.capacidades.navegador.guion import (
    MAX_VALOR as MAX_VALOR,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PREFIJO_FUENTE as PREFIJO_FUENTE,
)
from hacienda_gpt.capacidades.navegador.guion import (
    Descarga as Descarga,
)
from hacienda_gpt.capacidades.navegador.guion import (
    Guion as Guion,
)
from hacienda_gpt.capacidades.navegador.guion import (
    GuionError as GuionError,
)
from hacienda_gpt.capacidades.navegador.guion import (
    Login as Login,
)
from hacienda_gpt.capacidades.navegador.guion import (
    Parametro as Parametro,
)
from hacienda_gpt.capacidades.navegador.guion import (
    Paso as Paso,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoDescargar as PasoDescargar,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoDetener as PasoDetener,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoEsperar as PasoEsperar,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoIr as PasoIr,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoPulsar as PasoPulsar,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoRellenar as PasoRellenar,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PasoSeleccionar as PasoSeleccionar,
)
from hacienda_gpt.capacidades.navegador.guion import (
    PeticionPermitida as PeticionPermitida,
)
from hacienda_gpt.capacidades.navegador.guion import (
    RangoFechas as RangoFechas,
)
from hacienda_gpt.capacidades.navegador.guion import (
    Resumen as Resumen,
)
from hacienda_gpt.capacidades.navegador.guion import (
    SpecGuion as SpecGuion,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _ConSelector as _ConSelector,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _leer as _leer,
)
from hacienda_gpt.capacidades.navegador.guion import (
    _Paso as _Paso,
)
from hacienda_gpt.capacidades.navegador.guion import (
    cambia_estado as cambia_estado,
)
from hacienda_gpt.capacidades.navegador.guion import (
    claves_incorporadas as claves_incorporadas,
)
from hacienda_gpt.capacidades.navegador.guion import (
    comprobar_selector as comprobar_selector,
)
from hacienda_gpt.capacidades.navegador.guion import (
    enmascarar_valor as enmascarar_valor,
)
from hacienda_gpt.capacidades.navegador.guion import (
    fuente_de_guion as fuente_de_guion,
)
from hacienda_gpt.capacidades.navegador.guion import (
    guion_activo as guion_activo,
)
from hacienda_gpt.capacidades.navegador.guion import (
    guion_incorporado as guion_incorporado,
)
from hacienda_gpt.capacidades.navegador.guion import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.navegador.guion import (
    normalizar_texto as normalizar_texto,
)
from hacienda_gpt.capacidades.navegador.guion import (
    parece_credencial as parece_credencial,
)
from hacienda_gpt.capacidades.navegador.guion import (
    renderizar as renderizar,
)
from hacienda_gpt.capacidades.navegador.guion import (
    ruta_reparada as ruta_reparada,
)
from hacienda_gpt.capacidades.navegador.guion import (
    validar_parametros as validar_parametros,
)

sys.modules[__name__] = _modulo

"""Moved to :mod:`hacienda_gpt.capacidades.correo.dominios`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import dominios as _modulo
from hacienda_gpt.capacidades.correo.dominios import (
    _HOMOGLIFOS as _HOMOGLIFOS,
)
from hacienda_gpt.capacidades.correo.dominios import (
    _HOST_RE as _HOST_RE,
)
from hacienda_gpt.capacidades.correo.dominios import (
    _SUFIJOS_DOBLES as _SUFIJOS_DOBLES,
)
from hacienda_gpt.capacidades.correo.dominios import (  # noqa: F401 (the names, for type checkers)
    DATOS as DATOS,
)
from hacienda_gpt.capacidades.correo.dominios import (
    SUFIJO_SEDE_ESTATAL as SUFIJO_SEDE_ESTATAL,
)
from hacienda_gpt.capacidades.correo.dominios import (
    Marca as Marca,
)
from hacienda_gpt.capacidades.correo.dominios import (
    Remitentes as Remitentes,
)
from hacienda_gpt.capacidades.correo.dominios import (
    _crudo as _crudo,
)
from hacienda_gpt.capacidades.correo.dominios import (
    _distancia as _distancia,
)
from hacienda_gpt.capacidades.correo.dominios import (
    _extra_del_entorno as _extra_del_entorno,
)
from hacienda_gpt.capacidades.correo.dominios import (
    _visual as _visual,
)
from hacienda_gpt.capacidades.correo.dominios import (
    cargar as cargar,
)
from hacienda_gpt.capacidades.correo.dominios import (
    es_correo_gratuito as es_correo_gratuito,
)
from hacienda_gpt.capacidades.correo.dominios import (
    es_oficial as es_oficial,
)
from hacienda_gpt.capacidades.correo.dominios import (
    es_sede_estatal as es_sede_estatal,
)
from hacienda_gpt.capacidades.correo.dominios import (
    etiqueta as etiqueta,
)
from hacienda_gpt.capacidades.correo.dominios import (
    host_valido as host_valido,
)
from hacienda_gpt.capacidades.correo.dominios import (
    marca_nombrada as marca_nombrada,
)
from hacienda_gpt.capacidades.correo.dominios import (
    marca_oficial_de as marca_oficial_de,
)
from hacienda_gpt.capacidades.correo.dominios import (
    normalizar as normalizar,
)
from hacienda_gpt.capacidades.correo.dominios import (
    parecido_a as parecido_a,
)
from hacienda_gpt.capacidades.correo.dominios import (
    registrable as registrable,
)

sys.modules[__name__] = _modulo

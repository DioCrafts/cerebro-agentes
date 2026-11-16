"""Moved to :mod:`hacienda_gpt.capacidades.correo.mime`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import mime as _modulo
from hacienda_gpt.capacidades.correo.mime import (
    _BLOQUES as _BLOQUES,
)
from hacienda_gpt.capacidades.correo.mime import (
    _INVISIBLES as _INVISIBLES,
)
from hacienda_gpt.capacidades.correo.mime import (
    _OCULTO_RE as _OCULTO_RE,
)
from hacienda_gpt.capacidades.correo.mime import (
    _OMITIR as _OMITIR,
)
from hacienda_gpt.capacidades.correo.mime import (
    _URL_RE as _URL_RE,
)
from hacienda_gpt.capacidades.correo.mime import (
    _VACIOS as _VACIOS,
)
from hacienda_gpt.capacidades.correo.mime import (
    MAX_BYTES_MENSAJE as MAX_BYTES_MENSAJE,
)
from hacienda_gpt.capacidades.correo.mime import (
    MAX_BYTES_PARTE as MAX_BYTES_PARTE,
)
from hacienda_gpt.capacidades.correo.mime import (
    MAX_PARTES as MAX_PARTES,
)
from hacienda_gpt.capacidades.correo.mime import (
    MAX_TEXTO as MAX_TEXTO,
)
from hacienda_gpt.capacidades.correo.mime import (  # noqa: F401 (the names, for type checkers)
    ZONA as ZONA,
)
from hacienda_gpt.capacidades.correo.mime import (
    Adjunto as Adjunto,
)
from hacienda_gpt.capacidades.correo.mime import (
    Enlace as Enlace,
)
from hacienda_gpt.capacidades.correo.mime import (
    Mensaje as Mensaje,
)
from hacienda_gpt.capacidades.correo.mime import (
    _b64url as _b64url,
)
from hacienda_gpt.capacidades.correo.mime import (
    _cabeceras as _cabeceras,
)
from hacienda_gpt.capacidades.correo.mime import (
    _charset as _charset,
)
from hacienda_gpt.capacidades.correo.mime import (
    _decodificar_cabecera as _decodificar_cabecera,
)
from hacienda_gpt.capacidades.correo.mime import (
    _direccion as _direccion,
)
from hacienda_gpt.capacidades.correo.mime import (
    _dominio as _dominio,
)
from hacienda_gpt.capacidades.correo.mime import (
    _enlace as _enlace,
)
from hacienda_gpt.capacidades.correo.mime import (
    _Html as _Html,
)
from hacienda_gpt.capacidades.correo.mime import (
    _limpiar as _limpiar,
)
from hacienda_gpt.capacidades.correo.mime import (
    _recorrer as _recorrer,
)
from hacienda_gpt.capacidades.correo.mime import (
    _texto_de_bytes as _texto_de_bytes,
)
from hacienda_gpt.capacidades.correo.mime import (
    html_a_texto as html_a_texto,
)
from hacienda_gpt.capacidades.correo.mime import (
    leer_mensaje as leer_mensaje,
)

sys.modules[__name__] = _modulo

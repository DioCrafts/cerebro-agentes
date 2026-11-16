"""Moved to :mod:`hacienda_gpt.capacidades.correo.almacen`: this name is an alias of that module (the same object)."""

import sys

from hacienda_gpt.capacidades.correo import almacen as _modulo
from hacienda_gpt.capacidades.correo.almacen import (
    _ESQUEMA as _ESQUEMA,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _M as _M,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _TABLAS as _TABLAS,
)
from hacienda_gpt.capacidades.correo.almacen import (
    MAX_ASUNTO as MAX_ASUNTO,
)
from hacienda_gpt.capacidades.correo.almacen import (
    VIDA_ESTADO_OAUTH as VIDA_ESTADO_OAUTH,
)
from hacienda_gpt.capacidades.correo.almacen import (
    CampoGuardado as CampoGuardado,
)
from hacienda_gpt.capacidades.correo.almacen import (
    Conexion as Conexion,
)
from hacienda_gpt.capacidades.correo.almacen import (
    Elemento as Elemento,
)
from hacienda_gpt.capacidades.correo.almacen import (
    EstadoPropuesta as EstadoPropuesta,
)
from hacienda_gpt.capacidades.correo.almacen import (
    OrigenPropuesta as OrigenPropuesta,
)
from hacienda_gpt.capacidades.correo.almacen import (
    Propuesta as Propuesta,
)
from hacienda_gpt.capacidades.correo.almacen import (
    TipoPropuesta as TipoPropuesta,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _existen as _existen,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _hash as _hash,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _lock as _lock,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _revocar_despues as _revocar_despues,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _revocar_pendientes as _revocar_pendientes,
)
from hacienda_gpt.capacidades.correo.almacen import (
    _wal as _wal,
)
from hacienda_gpt.capacidades.correo.almacen import (
    actualizar_conexion as actualizar_conexion,
)
from hacienda_gpt.capacidades.correo.almacen import (
    al_borrar_caso as al_borrar_caso,
)
from hacienda_gpt.capacidades.correo.almacen import (
    anadir_propuesta as anadir_propuesta,
)
from hacienda_gpt.capacidades.correo.almacen import (
    borrar_conexion as borrar_conexion,
)
from hacienda_gpt.capacidades.correo.almacen import (
    borrar_datos as borrar_datos,
)
from hacienda_gpt.capacidades.correo.almacen import (
    conectar as conectar,
)
from hacienda_gpt.capacidades.correo.almacen import (
    conexion_de as conexion_de,
)
from hacienda_gpt.capacidades.correo.almacen import (
    consumir_estado_oauth as consumir_estado_oauth,
)
from hacienda_gpt.capacidades.correo.almacen import (
    contactos as contactos,
)
from hacienda_gpt.capacidades.correo.almacen import (
    elemento as elemento,
)
from hacienda_gpt.capacidades.correo.almacen import (
    elementos as elementos,
)
from hacienda_gpt.capacidades.correo.almacen import (
    exportar as exportar,
)
from hacienda_gpt.capacidades.correo.almacen import (
    fijar_contacto as fijar_contacto,
)
from hacienda_gpt.capacidades.correo.almacen import (
    guardar_conexion as guardar_conexion,
)
from hacienda_gpt.capacidades.correo.almacen import (
    guardar_elementos as guardar_elementos,
)
from hacienda_gpt.capacidades.correo.almacen import (
    guardar_estado_oauth as guardar_estado_oauth,
)
from hacienda_gpt.capacidades.correo.almacen import (
    ids_guardados as ids_guardados,
)
from hacienda_gpt.capacidades.correo.almacen import (
    instalar as instalar,
)
from hacienda_gpt.capacidades.correo.almacen import (  # noqa: F401 (the names, for type checkers)
    logger as logger,
)
from hacienda_gpt.capacidades.correo.almacen import (
    propuesta as propuesta,
)
from hacienda_gpt.capacidades.correo.almacen import (
    propuestas as propuestas,
)
from hacienda_gpt.capacidades.correo.almacen import (
    purgar_anteriores as purgar_anteriores,
)
from hacienda_gpt.capacidades.correo.almacen import (
    ruta_db as ruta_db,
)
from hacienda_gpt.capacidades.correo.almacen import (
    soltar_contacto as soltar_contacto,
)
from hacienda_gpt.capacidades.correo.almacen import (
    transicion as transicion,
)

sys.modules[__name__] = _modulo

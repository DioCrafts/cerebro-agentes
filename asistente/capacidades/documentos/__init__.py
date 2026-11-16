"""Capability ``documentos``: read an uploaded document, and the paperwork of a procedure.

Operations (``capacidades/contrato.py``):

* ``extraer_texto`` (lectura, no model): the text of an upload, or that it must be read as an image;
* ``leer_campos`` (lectura, model purpose ``documentos``): the model reads the document with the CALLING
  DOMAIN's instructions and schema — once from the extracted text, or twice, independently, from a scan or a
  photo (see ``lectura.py``). It returns the raw readings and the text: checking every value against the literal
  passage it was read from stays the domain's job (``modulos/fiscal/documentos.py``,
  ``modulos/caducidades/ticket.py``), and nothing is saved;
* ``lista_de_comprobacion``, ``borrador`` (prepara: a draft the user sends), ``pedir_documento``: the paperwork
  of a long-running task (``tramites.py``).

No consent of its own (``consentimiento="ninguno"``): it only processes what the user hands over in that same
request; sending an upload to the model provider needs the user's consent, which the API checks before.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from hacienda_gpt.capacidades.contrato import CapacidadBase, ContextoPeticion, OperacionCapacidad, ResultadoCapacidad
from hacienda_gpt.capacidades.documentos import lectura, tramites
from hacienda_gpt.capacidades.documentos.lectura import (
    MAX_SCAN_PAGES,
    DocumentoEscaneadoError,
    DocumentoNoLegibleError,
    decode_upload,
    extract_text,
    normalizar_pasaje,
    tipo_de_imagen,
)

PROPOSITO = "documentos"

_FICHERO = {
    "nombre_fichero": {"type": "string", "minLength": 1, "maxLength": 255},
    "contenido_base64": {"type": "string", "minLength": 1},
}

ENTRADA_EXTRAER: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": _FICHERO,
    "required": ["nombre_fichero", "contenido_base64"],
}
SALIDA_EXTRAER: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lectura": {"type": "string", "enum": ["texto", "imagen"]},
        "texto": {"type": "string"},
        "paginas": {"type": "integer", "minimum": 0},
        "tipo_medio": {"type": ["string", "null"]},
    },
    "required": ["lectura", "texto", "paginas"],
}

ENTRADA_LEER: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_FICHERO,
        "instrucciones": {"type": "string", "minLength": 1},
        "esquema": {"type": "object"},
        "nombre_esquema": {"type": "string", "minLength": 1, "maxLength": 64},
        "instrucciones_imagen": {"type": "string", "minLength": 1},
        "esquema_imagen": {"type": "object"},
        "nombre_esquema_imagen": {"type": "string", "minLength": 1, "maxLength": 64},
        "que_localizar": {"type": "string", "minLength": 1, "maxLength": 200},
        "max_caracteres": {"type": "integer", "minimum": 1},
        "max_paginas_escaneo": {"type": "integer", "minimum": 1, "maximum": MAX_SCAN_PAGES},
        "aviso_escaneo_largo": {"type": "string", "maxLength": 300},
    },
    "required": [
        "nombre_fichero",
        "contenido_base64",
        "instrucciones",
        "esquema",
        "nombre_esquema",
        "instrucciones_imagen",
        "esquema_imagen",
        "nombre_esquema_imagen",
        "que_localizar",
    ],
}
SALIDA_LEER: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lectura": {"type": "string", "enum": ["texto", "imagen"]},
        "paginas": {"type": "integer", "minimum": 0},
        "texto": {"type": "string"},
        "lecturas": {"type": "array", "items": {"type": "object"}, "maxItems": 2},
    },
    "required": ["lectura", "paginas", "texto", "lecturas"],
}


def contenido(data: bytes) -> str:
    """Bytes of an upload as the operations take them (base64)."""
    return base64.b64encode(data).decode("ascii")


def _extraer_texto(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    data = decode_upload(str(args["contenido_base64"]))
    nombre = str(args["nombre_fichero"])
    imagen = tipo_de_imagen(data)
    if imagen is not None:
        return ResultadoCapacidad(
            datos={"lectura": "imagen", "texto": "", "paginas": 1, "tipo_medio": imagen}, resumen="imagen"
        )
    try:
        texto, paginas = extract_text(data, nombre)
    except DocumentoEscaneadoError as exc:
        return ResultadoCapacidad(
            datos={"lectura": "imagen", "texto": "", "paginas": exc.paginas, "tipo_medio": "application/pdf"},
            resumen="pdf escaneado",
        )
    return ResultadoCapacidad(
        datos={"lectura": "texto", "texto": texto, "paginas": paginas, "tipo_medio": None},
        resumen=f"{paginas} páginas de texto",
    )


def _leer_campos(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    if ctx.proveedor is None:
        raise DocumentoNoLegibleError("No hay un lector de documentos configurado en este servidor.")
    data = decode_upload(str(args["contenido_base64"]))
    leido = lectura.leer(
        ctx.proveedor,
        filename=str(args["nombre_fichero"]),
        data=data,
        instrucciones=str(args["instrucciones"]),
        esquema=dict(args["esquema"]),
        nombre_esquema=str(args["nombre_esquema"]),
        instrucciones_imagen=str(args["instrucciones_imagen"]),
        esquema_imagen=dict(args["esquema_imagen"]),
        nombre_esquema_imagen=str(args["nombre_esquema_imagen"]),
        que_localizar=str(args["que_localizar"]),
        max_caracteres=int(args["max_caracteres"]) if "max_caracteres" in args else None,
        max_paginas_escaneo=int(args.get("max_paginas_escaneo", MAX_SCAN_PAGES)),
        aviso_escaneo_largo=str(args["aviso_escaneo_largo"]) if "aviso_escaneo_largo" in args else None,
    )
    # A reading that is not an object reads as an empty one (the domain finds nothing in it).
    leido["lecturas"] = [raw if isinstance(raw, dict) else {} for raw in leido["lecturas"]]
    return ResultadoCapacidad(datos=leido, resumen=f"{len(leido['lecturas'])} lectura(s) de {leido['lectura']}")


OPERACIONES = (
    OperacionCapacidad(
        clave="extraer_texto",
        descripcion="Extrae el texto de un documento subido (o dice que hay que leerlo como imagen).",
        entrada=ENTRADA_EXTRAER,
        salida=SALIDA_EXTRAER,
        ejecutar=_extraer_texto,
    ),
    OperacionCapacidad(
        clave="leer_campos",
        descripcion=(
            "Lee un documento subido con las instrucciones del ámbito que lo pide (una lectura del texto, o dos "
            "independientes de un escaneo); el ámbito comprueba cada dato contra su pasaje literal."
        ),
        entrada=ENTRADA_LEER,
        salida=SALIDA_LEER,
        ejecutar=_leer_campos,
        proposito=PROPOSITO,
    ),
    OperacionCapacidad(
        clave="lista_de_comprobacion",
        descripcion="Lista de lo que hay que tener a mano para un trámite.",
        entrada=tramites.ENTRADA_LISTA,
        salida=tramites.SALIDA_VACIA,
        ejecutar=tramites.lista_de_comprobacion,
        datos_de_terceros=False,
    ),
    OperacionCapacidad(
        clave="borrador",
        descripcion="Prepara un borrador de escrito para que el usuario lo revise y lo envíe él (nunca se envía).",
        entrada=tramites.ENTRADA_BORRADOR,
        salida=tramites.SALIDA_VACIA,
        ejecutar=tramites.borrador,
        efecto="prepara",
        datos_de_terceros=False,
    ),
    OperacionCapacidad(
        clave="pedir_documento",
        descripcion="Pide al usuario que tenga listo un documento.",
        entrada=tramites.ENTRADA_PEDIR,
        salida=tramites.SALIDA_VACIA,
        ejecutar=tramites.pedir_documento,
        datos_de_terceros=False,
    ),
)

CAPACIDAD = CapacidadBase(
    clave="documentos",
    nombre="Documentos",
    descripcion=(
        "Lee los documentos que subes (PDF, fotos, escaneos) para el ámbito que los necesita, que comprueba cada "
        "dato contra el texto del documento antes de proponértelo; y prepara listas de papeles y borradores."
    ),
    consentimiento="ninguno",
    operaciones=OPERACIONES,
)

__all__ = [
    "CAPACIDAD",
    "PROPOSITO",
    "DocumentoEscaneadoError",
    "DocumentoNoLegibleError",
    "contenido",
    "decode_upload",
    "extract_text",
    "normalizar_pasaje",
    "tipo_de_imagen",
]

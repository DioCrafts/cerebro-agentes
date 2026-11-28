"""Each declared MCP server as a CAPABILITY (``capacidades/contrato.py``): ``mcp_<clave>``.

The capability is built from two files the operator controls, never from what the server says at run time:

* the declaration (``config.py``): which servers exist, which of their tools only read;
* the approvals (``aprobaciones.py``): the tool definitions a person accepted, by digest.

So every rule of :func:`asistente.capacidades.peticion.pedir` applies to a tool of a third-party server exactly as
to the project's own capabilities:

* consent — one switch per server, the user's (``PUT /me/capacidades/mcp_<clave>``);
* effect — ``lectura`` only for the tools the operator listed; every other one is ``actua`` and runs only with the
  user's :class:`Confirmacion` of exactly those arguments;
* schemas — arguments are checked against the approved (cleaned) input schema before anything leaves the app;
* untrusted data — every result is ``datos_de_terceros``: wrapped as data, and a turn that read it can no longer
  write facts without the user, nor call one of these tools again without them (``contiene_no_confiable`` and
  ``HerramientaModulo.sale_del_equipo``). What goes out in the arguments is the reason for the second half: a
  read runs unasked, so without that gate a model steered by third-party text could send the case's data to the
  server. The digest covers the tool's definition, never what the server then does with those arguments.

Nothing here connects at import or discovery time. The first call opens the session; the live definition must
still carry the approved digest, or the call is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from typing import Any

from asistente.capacidades.conectores import ajustes, conexiones
from asistente.capacidades.conectores import aprobaciones as aprobaciones_mod
from asistente.capacidades.conectores.aprobaciones import Aprobaciones
from asistente.capacidades.conectores.config import (
    ConfiguracionMCP,
    SecretoAusenteError,
    ServidorMCP,
    clave_capacidad,
    leer,
)
from asistente.capacidades.conectores.definicion import clave_de
from asistente.capacidades.contrato import CapacidadBase, ContextoPeticion, OperacionCapacidad, ResultadoCapacidad

logger = logging.getLogger(__name__)

# Who may ask a connector: the core (API, long-running tasks) and the connectors' own chat front.
AMBITO = "conectores"
AMBITOS = frozenset({"nucleo", AMBITO})

SALIDA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "texto": {"type": "string"},
        "estructurado": {},
        "es_error": {"type": "boolean"},
        "recortado": {"type": "boolean"},
        "bloques_omitidos": {"type": "integer"},
    },
    "required": ["texto", "es_error"],
}


def _fallo(mensaje: str, resumen: str) -> ResultadoCapacidad:
    return ResultadoCapacidad(
        datos={"texto": mensaje, "es_error": True},
        resumen=resumen,
        texto_para_modelo=mensaje,
        es_error=True,
    )


def _ejecutor(clave: str, servidor: ServidorMCP, herramienta: str, huella: str) -> Any:
    """The ``ejecutar`` of one operation: call the server's tool and reduce its answer to capped text."""

    def ejecutar(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
        del ctx
        try:
            remoto = conexiones.gestor().llamar(clave, servidor, herramienta, dict(args), huella=huella)
        except conexiones.DefinicionCambiadaError as exc:
            logger.error("MCP server %s: tool definition changed after approval; call refused", clave)
            return _fallo(
                f"{exc} No se ha llamado. El operador tiene que revisarla y aprobarla de nuevo.",
                "definición cambiada",
            )
        except conexiones.TiempoAgotadoError as exc:
            return _fallo(f"{exc} No se sabe si la operación llegó a hacerse.", "sin respuesta a tiempo")
        except SecretoAusenteError:
            logger.error("MCP server %s: a secret of its declaration is missing from the environment", clave)
            return _fallo("El servicio externo no está bien configurado en este servidor.", "sin configurar")
        except conexiones.ConexionError as exc:
            return _fallo(f"No se ha podido usar «{servidor.nombre}»: {exc}", "sin conexión")

        maximo = ajustes.max_resultado()
        texto = remoto.texto
        if remoto.estructurado is not None and not texto.strip():
            texto = json.dumps(remoto.estructurado, ensure_ascii=False, default=str, indent=1)
        recortado = len(texto) > maximo
        if recortado:
            texto = texto[:maximo]
        datos: dict[str, Any] = {"texto": texto, "es_error": remoto.es_error, "recortado": recortado}
        if remoto.omitidos:
            datos["bloques_omitidos"] = remoto.omitidos
        avisos: list[str] = []
        if recortado:
            avisos.append(f"La respuesta de «{servidor.nombre}» era más larga y se ha recortado.")
        if remoto.omitidos:
            avisos.append(f"«{servidor.nombre}» devolvió {remoto.omitidos} elemento(s) que no son texto; no se leen.")
        return ResultadoCapacidad(
            datos=datos,
            resumen=f"{servidor.nombre}: {herramienta}" + (" (error)" if remoto.es_error else ""),
            citas=(f"mcp://{clave}/{herramienta}",),
            avisos=tuple(avisos),
            texto_para_modelo=texto or "(sin contenido)",
            es_error=remoto.es_error,
        )

    return ejecutar


def operaciones_de(
    clave: str, servidor: ServidorMCP, aprobadas: Mapping[str, aprobaciones_mod.HerramientaAprobada]
) -> list[tuple[str, OperacionCapacidad]]:
    """``(server tool name, operation)`` for every approved tool the declaration offers, in a stable order."""
    operaciones: list[tuple[str, OperacionCapacidad]] = []
    usadas: set[str] = set()
    for nombre in sorted(aprobadas):
        if not servidor.ofrece(nombre):
            continue
        aprobada = aprobadas[nombre]
        clave_op = clave_de(nombre, usadas)
        usadas.add(clave_op)
        lectura = nombre in servidor.lectura
        operaciones.append(
            (
                nombre,
                OperacionCapacidad(
                    clave=clave_op,
                    descripcion=aprobada.descripcion or f"Herramienta «{nombre}» de {servidor.nombre}.",
                    entrada=aprobada.entrada or {"type": "object", "properties": {}},
                    salida=SALIDA,
                    ejecutar=_ejecutor(clave, servidor, nombre, aprobada.huella),
                    efecto="lectura" if lectura else "actua",
                    proposito=None,  # a connector never gets this app's model
                    datos_de_terceros=True,
                    ambitos=AMBITOS,
                ),
            )
        )
    return operaciones


def capacidad_de(
    clave: str, servidor: ServidorMCP, aprobadas: Mapping[str, aprobaciones_mod.HerramientaAprobada]
) -> CapacidadBase | None:
    """The capability of one server; None while it has no approved tool (it is then not offered at all)."""
    operaciones = [op for _nombre, op in operaciones_de(clave, servidor, aprobadas)]
    if not operaciones:
        return None
    return CapacidadBase(
        clave=clave_capacidad(clave),
        nombre=servidor.nombre,
        descripcion=(
            f"{servidor.descripcion.rstrip('.')}. Servicio externo conectado por MCP: lo que devuelve son datos de "
            "un tercero y lo que cambie algo fuera de la aplicación espera tu confirmación."
        ),
        consentimiento="usuario",
        operaciones=tuple(operaciones),
        ambitos=AMBITOS,
    )


def capacidades(
    configuracion: ConfiguracionMCP | None = None, aprobaciones: Aprobaciones | None = None
) -> list[CapacidadBase]:
    """Every connector capability: declared, switched on and with at least one approved tool.

    Reads the two files of the operator; never connects. A broken declaration yields no capability (logged).
    """
    if configuracion is None:
        lectura = leer()
        if lectura.error is not None:
            logger.error("MCP connectors are off: %s", lectura.error)
        configuracion = lectura.configuracion
    activos = configuracion.activos()
    if not activos:
        return []
    aprobaciones = aprobaciones if aprobaciones is not None else aprobaciones_mod.leer()
    encontradas: list[CapacidadBase] = []
    for clave, servidor in activos.items():
        capacidad = capacidad_de(clave, servidor, aprobaciones.de(clave))
        if capacidad is None:
            logger.warning(
                "MCP server %s has no approved tool: run `python -m asistente.plataforma.cli.mcp aprobar %s`",
                clave,
                clave,
            )
            continue
        encontradas.append(capacidad)
    return encontradas


__all__ = ["AMBITO", "AMBITOS", "SALIDA", "capacidad_de", "capacidades", "operaciones_de"]

"""The browser agent as a CAPABILITY any domain can use (``capacidades/contrato.py``).

* ``preparar_descarga_consumo`` (prepara) — «descarga mi consumo»: the electricity domain asks for a Datadis
  download for ITS supply (the CUPS it passes). The task is only PREPARED: the browser opens when the user presses
  «Empezar» in the «Navegador» panel and logs in themselves; the CSV then goes to the destination the script
  declares (``luz_importar_consumo``, run as if the user typed it in that domain). Needs the user's consent to the
  browser (the ``navegador`` switch) and the operator's ``NAVEGADOR_ACTIVO``.
* ``consumo_datadis`` (lectura, no consent needed: it uses no browser) — for a long-running task's step: tells
  the user how to get the hourly consumption into the electricity domain, with the browser when it is on for them,
  otherwise by downloading the CSV themselves.

Submitting, signing and paying are not implemented anywhere (``navegador_presentar_tramite`` only answers so).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hacienda_gpt import settings
from hacienda_gpt.capacidades.contrato import CapacidadBase, ContextoPeticion, OperacionCapacidad, ResultadoCapacidad
from hacienda_gpt.modulos.contrato import ContextoModulo

CLAVE = "navegador"

_FECHA = {"type": "string", "maxLength": 10}
ENTRADA_DESCARGA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"desde": _FECHA, "hasta": _FECHA, "cups": {"type": "string", "maxLength": 30}},
}
SALIDA_DESCARGA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"preparada": {"type": "boolean"}, "mensaje": {"type": "string"}},
    "required": ["preparada", "mensaje"],
}
ENTRADA_VACIA: dict[str, Any] = {"type": "object", "additionalProperties": False, "properties": {}}


def _preparar_descarga(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    from hacienda_gpt.capacidades.navegador.modulo import descargar_consumo_datadis

    argumentos = {k: v for k, v in args.items() if v not in (None, "")}
    # The CUPS comes from the asking domain; the browser never reads another domain's facts on its own.
    resultado = descargar_consumo_datadis(argumentos, ContextoModulo(perfil={}, hoy=ctx.hoy, case_id=ctx.case_id))
    return ResultadoCapacidad(
        datos={"preparada": not resultado.es_error, "mensaje": resultado.contenido},
        resumen=resultado.resumen,
        es_error=resultado.es_error,
        texto_para_modelo=resultado.contenido,
    )


def _consumo_datadis(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    del args
    if ctx.activos is not None:
        activo = CLAVE in ctx.activos
    else:
        from hacienda_gpt.capacidades.consentimiento import consentida

        activo = consentida(CAPACIDAD, ctx)
    con_navegador = bool(getattr(settings, "NAVEGADOR_ACTIVO", False)) and activo
    if con_navegador:
        cuerpo = (
            "Abre «Navegador», elige «Consumo de Datadis» y pulsa «Empezar»: iniciarás sesión tú en Datadis y el "
            "agente descargará el CSV y lo pasará al módulo de luz. No se cambia nada en Datadis."
        )
    else:
        cuerpo = (
            "Descarga de Datadis el CSV de tu consumo horario (los últimos meses) y súbelo en «Módulos» → Luz "
            "(importar consumo). El agente con navegador no está disponible o no lo has activado."
        )
    return ResultadoCapacidad(
        resumen=cuerpo,
        citas=("modulo://navegador" if con_navegador else "modulo://luz/luz_importar_consumo",),
        aviso=("Trae tu consumo de luz", cuerpo),
    )


CAPACIDAD = CapacidadBase(
    clave=CLAVE,
    nombre="Agente con navegador",
    descripcion=(
        "Actúa por ti en webs oficiales con un navegador, con tu sesión y bajo tu control: descarga tu consumo "
        "horario de Datadis para el ámbito de la luz. Nunca firma, presenta ni paga."
    ),
    consentimiento="usuario",
    operaciones=(
        OperacionCapacidad(
            clave="preparar_descarga_consumo",
            descripcion=(
                "Prepara la descarga del consumo horario de Datadis (el usuario la inicia e inicia sesión él mismo)."
            ),
            entrada=ENTRADA_DESCARGA,
            salida=SALIDA_DESCARGA,
            ejecutar=_preparar_descarga,
            efecto="prepara",
            datos_de_terceros=False,
            ambitos=frozenset({"luz", "nucleo"}),
        ),
        OperacionCapacidad(
            clave="consumo_datadis",
            descripcion="Explica cómo traer el consumo horario de Datadis al módulo de luz (el usuario la inicia).",
            entrada=ENTRADA_VACIA,
            salida={"type": "object"},
            ejecutar=_consumo_datadis,
            datos_de_terceros=False,
            requiere_consentimiento=False,
        ),
    ),
)

__all__ = ["CAPACIDAD"]

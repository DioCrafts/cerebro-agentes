"""The ``navegador`` household module: the browser agent seen from the chat.

It declares no facts (it stores nothing of its own in the case) and two tools:

* ``navegador_descargar_consumo_datadis`` (``prepara``): validates the period and the CUPS and PREPARES the
  Datadis download. It never opens a browser by itself: the user starts it from the «Navegador» panel when
  they are ready to log in, and when the CSV is downloaded it goes to ``luz_importar_consumo`` through the
  module tool path (``tareas.ejecutar_herramienta_de_modulo``).
* ``navegador_presentar_tramite`` (``actua``): the pending action a procedure leaves when the agent stops
  before «Firmar y enviar». Submitting, signing and paying are NOT implemented: confirming it only answers
  that the user must submit it themselves.

Switching the module on is the user's consent (recorded with its date, exported and erased with the
account); ``NAVEGADOR_ACTIVO`` is the operator's kill switch over all of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from hacienda_gpt import settings
from hacienda_gpt.agent.providers.base import ToolSpec
from hacienda_gpt.capacidades.navegador.guion import enmascarar_valor
from hacienda_gpt.capacidades.navegador.tareas import CLAVE_MODULO, HERRAMIENTA_PRESENTAR, TareaError, gestor
from hacienda_gpt.modulos.contrato import (
    ContextoModulo,
    FuenteModulo,
    HerramientaModulo,
    ModuloBase,
    ResultadoHerramienta,
)

DIAS_POR_DEFECTO = 60
HECHO_CUPS_LUZ = "luz_cups"


def _error(texto: str) -> ResultadoHerramienta:
    return ResultadoHerramienta(contenido=texto, resumen=texto[:120], es_error=True)


def _fecha(valor: Any, defecto: date) -> date:
    if valor in (None, ""):
        return defecto
    return date.fromisoformat(str(valor))


def descargar_consumo_datadis(args: Mapping[str, Any], ctx: ContextoModulo) -> ResultadoHerramienta:
    if not settings.NAVEGADOR_ACTIVO:
        return _error("El agente con navegador está desactivado en este servicio. Descarga el CSV de Datadis a mano.")
    if ctx.case_id is None:
        return _error("Hace falta un expediente para preparar la descarga.")
    try:
        hasta = _fecha(args.get("hasta"), ctx.hoy - timedelta(days=1))
        desde = _fecha(args.get("desde"), hasta - timedelta(days=DIAS_POR_DEFECTO - 1))
    except ValueError:
        return _error("Las fechas van en formato AAAA-MM-DD.")
    cups = args.get("cups") or ctx.perfil.get(HECHO_CUPS_LUZ) or None
    try:
        tarea = gestor().preparar(
            case_id=ctx.case_id,
            clave="datadis_consumo",
            parametros={"cups": cups, "desde": desde.isoformat(), "hasta": hasta.isoformat()},
            hoy=ctx.hoy,
        )
    except TareaError as exc:
        return _error(str(exc))
    suministro = f"el suministro {enmascarar_valor(cups)}" if cups else "tu suministro (si tienes varios, elígelo)"
    contenido = (
        f"He preparado la descarga de tu consumo horario de Datadis para {suministro}, del {desde:%d-%m-%Y} "
        f"al {hasta:%d-%m-%Y} (tarea {tarea.id}).\n"
        "Para empezar, abre «Navegador» y pulsa «Empezar»: se abrirá Datadis y tendrás que iniciar sesión tú. "
        "HaciendaGPT no ve, no guarda y no envía a ningún modelo tu contraseña. Después descargaré el CSV y lo "
        "importaré en el módulo de luz. En Datadis no se cambia nada."
    )
    return ResultadoHerramienta(contenido=contenido, resumen=f"Descarga de Datadis preparada ({tarea.id})")


def presentar_tramite(args: Mapping[str, Any], ctx: ContextoModulo) -> ResultadoHerramienta:
    """Never submits: in this version submitting, signing and paying are not implemented at all."""
    return _error(
        "No implementado: HaciendaGPT no firma, no presenta y no paga trámites. El formulario quedó preparado y "
        "revisado; preséntalo tú en la sede (en la ventana del navegador o entrando de nuevo)."
    )


def herramientas() -> list[HerramientaModulo]:
    return [
        HerramientaModulo(
            spec=ToolSpec(
                name="navegador_descargar_consumo_datadis",
                description=(
                    "Prepara la descarga del CSV horario de consumo eléctrico desde Datadis con el navegador: el "
                    "usuario inicia sesión él mismo y después el agente descarga el fichero y lo importa en el "
                    "módulo de luz. No abre el navegador hasta que el usuario pulsa «Empezar»."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "desde": {"type": "string", "description": "Fecha inicial AAAA-MM-DD (60 días por defecto)."},
                        "hasta": {"type": "string", "description": "Fecha final AAAA-MM-DD (ayer por defecto)."},
                        "cups": {"type": "string", "description": "CUPS del suministro (el del expediente si hay)."},
                    },
                },
            ),
            ejecutar=descargar_consumo_datadis,
            efecto="prepara",
        ),
        HerramientaModulo(
            spec=ToolSpec(
                name=HERRAMIENTA_PRESENTAR,
                description=(
                    "Presentar un trámite que el agente con navegador dejó preparado. No implementado: "
                    "HaciendaGPT nunca firma, presenta ni paga; el usuario lo presenta él mismo."
                ),
                input_schema={"type": "object", "properties": {"tarea_id": {"type": "string"}}},
            ),
            ejecutar=presentar_tramite,
            efecto="actua",
        ),
    ]


MODULO = ModuloBase(
    clave=CLAVE_MODULO,
    nombre="Agente con navegador",
    descripcion=(
        "Actúa por ti en webs oficiales con un navegador, con tu sesión y bajo tu control: descarga tu consumo "
        "horario de Datadis para el módulo de luz y prepara trámites en una sede electrónica sin presentarlos."
    ),
    instrucciones=(
        "Eres el especialista del agente con navegador. Solo PREPARAS tareas: el navegador no se abre hasta que el "
        "usuario pulsa «Empezar» en el panel «Navegador» e inicia sesión él mismo. Nunca pidas ni aceptes "
        "contraseñas, PIN, códigos ni certificados: si el usuario te los escribe, dile que no los comparta y que "
        "inicie sesión en la ventana del navegador. Para el consumo eléctrico usa "
        "navegador_descargar_consumo_datadis. No presentes, firmes ni pagues nada: no está implementado y el "
        "usuario lo hace él mismo."
    ),
    herramientas=tuple(herramientas()),
    fuentes=(
        FuenteModulo(
            identificador="datadis",
            descripcion="Datadis: datos de consumo de las distribuidoras eléctricas (acceso con la sesión del usuario).",
            url="https://datadis.es",
        ),
    ),
)

__all__ = ["MODULO", "descargar_consumo_datadis", "herramientas", "presentar_tramite"]

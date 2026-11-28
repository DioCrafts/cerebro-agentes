"""The ``navegador`` household module: the browser agent seen from the chat.

It declares no facts (it stores nothing of its own in the case) and three tools:

* ``navegador_descargar_consumo_datadis`` (``prepara``): validates the period and the CUPS and PREPARES the
  Datadis download. It never opens a browser by itself: the user starts it from the «Navegador» panel when
  they are ready to log in, and when the CSV is downloaded it goes to ``luz_importar_consumo`` through the
  module tool path (``tareas.ejecutar_herramienta_de_modulo``).
* ``navegador_encargar_mision`` (``prepara``): PREPARES a mission (``agente.py``) on a declared site: an
  objective and the data the user gave for it. Nothing runs: the user reviews the objective and the data in the
  «Navegador» panel, accepts the missions' consent text and presses «Empezar». That review matters, because
  here the objective is written by a model: what the agent may type into a page is limited to what the
  objective and the data say, so the user must have read them.
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

from asistente.capacidades.navegador.guion import enmascarar_valor
from asistente.capacidades.navegador.tareas import (
    CLAVE_MODULO,
    HERRAMIENTA_PRESENTAR,
    TareaError,
    gestor,
    misiones_activas,
    sitios_disponibles,
)
from asistente.nucleo.agent.providers.base import ToolSpec
from asistente.nucleo.modulos.contrato import (
    ContextoModulo,
    FuenteModulo,
    HerramientaModulo,
    ModuloBase,
    ResultadoHerramienta,
)
from asistente.plataforma import settings

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


def encargar_mision(args: Mapping[str, Any], ctx: ContextoModulo) -> ResultadoHerramienta:
    """Prepare a mission. It never opens a browser nor calls a model: the user starts it after reviewing it."""
    if not misiones_activas():
        return _error("Las misiones del agente con navegador están desactivadas en este servicio.")
    if ctx.case_id is None:
        return _error("Hace falta un expediente para preparar una misión.")
    disponibles = sitios_disponibles()
    sitio = str(args.get("sitio") or "").strip()
    if sitio not in disponibles:
        lista = "; ".join(f"{clave} ({spec.reglas.titulo})" for clave, spec in sorted(disponibles.items()))
        return _error(f"Ese sitio no está entre los declarados. Disponibles: {lista or 'ninguno'}.")
    datos = args.get("datos") or {}
    if not isinstance(datos, Mapping):
        return _error("«datos» debe ser un objeto con nombre y valor.")
    try:
        tarea = gestor().preparar_mision(
            case_id=ctx.case_id, sitio=sitio, objetivo=str(args.get("objetivo") or ""), datos=dict(datos)
        )
    except TareaError as exc:
        return _error(str(exc))
    reglas = disponibles[sitio].reglas
    acceso = "tendrás que iniciar sesión tú" if reglas.login is not None else "no hace falta iniciar sesión"
    contenido = (
        f"He preparado una misión en «{reglas.titulo}» (tarea {tarea.id}). No ha empezado: abre «Navegador», "
        f"revisa el objetivo y los datos que he escrito y pulsa «Empezar» ({acceso}). El agente solo consulta: "
        "no envía, no firma y no paga; en los campos solo escribe los datos de la misión."
    )
    return ResultadoHerramienta(contenido=contenido, resumen=f"Misión preparada ({tarea.id})")


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
                name="navegador_encargar_mision",
                description=(
                    "Prepara una misión del agente con navegador en un sitio declarado: un objetivo en lenguaje "
                    "natural (qué consultar o qué descargar) y los datos que el USUARIO ha dado para cumplirlo. "
                    "No abre el navegador: el usuario revisa el objetivo y los datos y pulsa «Empezar». Escribe "
                    "el objetivo con las palabras del usuario y pon en «datos» solo valores que él haya escrito "
                    "en esta conversación; nunca contraseñas, PIN ni datos de pago."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sitio": {"type": "string", "description": "Clave del sitio (p. ej. boe)."},
                        "objetivo": {"type": "string", "description": "Qué tiene que conseguir el agente."},
                        "datos": {
                            "type": "object",
                            "description": "Datos del usuario para la misión: nombre → valor (texto).",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["sitio", "objetivo"],
                },
            ),
            ejecutar=encargar_mision,
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
        "navegador_descargar_consumo_datadis. Para consultar o descargar algo en otro sitio declarado usa "
        "navegador_encargar_mision, con el objetivo en palabras del usuario y solo los datos que él haya dado. "
        "No presentes, firmes ni pagues nada: no está implementado y el usuario lo hace él mismo."
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

__all__ = ["MODULO", "descargar_consumo_datadis", "encargar_mision", "herramientas", "presentar_tramite"]

"""Research as a step of a long-running task (``hacienda_gpt/tareas``, step type ``capacidad``).

:class:`CapacidadInvestigacionTareas` is the tasks layer's ``investigacion`` capability with one more
operation, ``investigar``, which runs this package's research through the capability contract
(:func:`~hacienda_gpt.capacidades.contrato.pedir`, domain ``nucleo``, origin ``tarea``). The existing
``webs_oficiales`` operation is inherited unchanged. The step's summary is the report's answer (already
reduced to verified claims), its confidence and what could not be verified; the evidences are the cited
sources. A question carrying a value of the user's case is refused (the same privacy gate as the agent tool).

Wiring (in ``hacienda_gpt/tareas/capacidades.py``, ``capacidades_por_defecto``)::

    "investigacion": CapacidadInvestigacionTareas(buscador),
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from typing import Any

from hacienda_gpt.capacidades.contrato import ContextoPeticion, PeticionDenegada, pedir
from hacienda_gpt.capacidades.investigacion.capacidad import CLAVE
from hacienda_gpt.capacidades.investigacion.herramienta import dato_privado_en, valores_privados
from hacienda_gpt.capacidades.investigacion.modelo import PROFUNDIDADES, TIPOS_FUENTE
from hacienda_gpt.tareas.capacidades import (
    Buscador,
    CapacidadInvestigacion,
    ContextoCapacidad,
    Operacion,
    ResultadoCapacidad,
)

logger = logging.getLogger(__name__)

OPERACION_INVESTIGAR = Operacion(
    "investigar",
    "Investiga una pregunta general con fuentes (corpus legal, BOE y, con consentimiento, web) y resume lo "
    "verificado con citas literales.",
    {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "pregunta": {"type": "string", "minLength": 5, "maxLength": 400},
            "fuentes": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": list(TIPOS_FUENTE)}},
            "profundidad": {"type": "string", "enum": list(PROFUNDIDADES)},
            "solo_oficiales": {"type": "boolean"},
        },
        "required": ["pregunta"],
    },
)


def _proveedor_por_defecto() -> Any | None:
    try:
        from hacienda_gpt.agent.providers import create_provider
        from hacienda_gpt.ops.consumo import medir

        return medir(create_provider(), "investigacion:sintetizar")
    except Exception as exc:  # no model configured: the research lists sources without an answer
        logger.warning("research step without a model (%s)", type(exc).__name__)
        return None


class CapacidadInvestigacionTareas(CapacidadInvestigacion):
    """The tasks' research capability: ``webs_oficiales`` (inherited) and ``investigar`` (this package)."""

    def __init__(self, buscador: Buscador | None = None, *, proveedor: Callable[[], Any] | None = None) -> None:
        super().__init__(buscador)
        self.operaciones = {**self.operaciones, OPERACION_INVESTIGAR.nombre: OPERACION_INVESTIGAR}
        self._proveedor = proveedor or _proveedor_por_defecto

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        if operacion != OPERACION_INVESTIGAR.nombre:
            return super().ejecutar(operacion, parametros, ctx)
        pregunta = str(parametros.get("pregunta", ""))
        # The case's facts as stored and as the modules read them (the profile).
        datos_caso = {f.name: f.value for f in ctx.case.facts} | dict(ctx.perfil)
        if dato_privado_en(pregunta, valores_privados(datos_caso), ()) is not None:
            return ResultadoCapacidad(
                resumen="La pregunta incluye datos de tu expediente: no se envía a investigar. Reformúlala en "
                "términos generales.",
                es_error=True,
            )
        contexto = ContextoPeticion(
            ambito="nucleo",
            hoy=ctx.hoy,
            case_id=ctx.case.case_id,
            proveedor=self._proveedor(),
            perfil=dict(ctx.perfil),
            activos=frozenset(m.clave for m in ctx.modulos_activos),
            clave_idempotencia=ctx.clave,
            origen="tarea",
        )
        try:
            resultado = pedir(CLAVE, "investigar", dict(parametros), contexto)
        except PeticionDenegada as exc:
            return ResultadoCapacidad(resumen=str(exc), es_error=exc.motivo in ("entrada", "salida"))
        datos = resultado.datos
        lineas = [str(datos.get("respuesta", "")), "", f"Confianza: {datos.get('confianza', 'baja')}."]
        pendientes = [str(t) for t in datos.get("no_verificado") or []][:5]
        if pendientes:
            lineas += ["No verificado:"] + [f"- {t}" for t in pendientes]
        return ResultadoCapacidad(resumen="\n".join(lineas)[:3500], citas=resultado.citas[:20])


__all__ = ["OPERACION_INVESTIGAR", "CapacidadInvestigacionTareas"]

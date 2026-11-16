"""A domain tool that asks a capability: ``pedir`` from a ``ContextoModulo``, answered as a tool result.

A refused request (consent, effect, domain not allowed…) becomes the tool's Spanish error, never an exception;
the capability's text reaches the model only in the form ``pedir`` gives it (``texto_para_modelo``, wrapped as
untrusted data when it carries third-party content).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asistente.capacidades.contrato import ContextoPeticion, PeticionDenegada, pedir
from asistente.nucleo.modulos.contrato import ContextoModulo, ResultadoHerramienta


def pedir_desde_herramienta(
    ambito: str, capacidad: str, operacion: str, args: Mapping[str, Any], ctx: ContextoModulo
) -> ResultadoHerramienta:
    if ctx.case_id is None:
        texto = "Hace falta un expediente para usar esta función."
        return ResultadoHerramienta(contenido=texto, resumen=texto, es_error=True)
    try:
        resultado = pedir(
            capacidad,
            operacion,
            args,
            ContextoPeticion(ambito=ambito, hoy=ctx.hoy, case_id=ctx.case_id, perfil=dict(ctx.perfil), origen="agente"),
        )
    except PeticionDenegada as exc:
        return ResultadoHerramienta(contenido=str(exc), resumen=f"{capacidad}.{operacion}: {exc.motivo}", es_error=True)
    texto = resultado.texto_para_modelo
    if resultado.avisos:
        texto += "\n" + "\n".join(resultado.avisos)
    return ResultadoHerramienta(
        contenido=texto,
        resumen=resultado.resumen or f"{capacidad}.{operacion}",
        es_error=resultado.es_error,
        citas=resultado.citas,
    )


__all__ = ["pedir_desde_herramienta"]

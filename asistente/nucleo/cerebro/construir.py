"""Wiring the household brain from settings (what ``agent.build_agent`` returns when ``CEREBRO_MODO=hogar``).

* the tax specialist is the advisor exactly as the legacy mode builds it (``agent.construir_asesor_fiscal``: the
  metered provider of the active variant, the FAISS searcher, the verifier and the doctrine graph);
* the brain's providers are that same metered provider re-purposed per call (``cerebro``, ``cerebro:intencion``,
  ``especialista:<clave>``, ``verificador``…), so routing by tier and the question budget apply to every call;
* the household specialists are built per turn from the user's active modules (:func:`especialistas_del_hogar`,
  the collaboration runtime's ``EspecialistaDeModulo``);
* the conversation memory is the memory package's service (:func:`servicio_de_memoria`), or no memory at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import inspect
import logging
from typing import TYPE_CHECKING, Any

from asistente.nucleo.cerebro.contrato import CLAVE_FISCAL, Especialista, MemoriaNula, ProveedorMemoria

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.cerebro.cerebro import Cerebro
    from asistente.nucleo.modulos.contrato import Modulo

logger = logging.getLogger(__name__)

# Where the conversation memory's factory lives (agent C's package).
SERVICIO_MEMORIA = "asistente.nucleo.recuerdos.servicio:servicio_memoria"


def servicio_de_memoria() -> ProveedorMemoria:
    """The conversation memory service (``asistente.nucleo.recuerdos.servicio.servicio_memoria()``), or
    :class:`MemoriaNula` when the package is not there or fails to start: the brain works without memory."""
    modulo, _, funcion = SERVICIO_MEMORIA.partition(":")
    try:
        import importlib

        fabrica = getattr(importlib.import_module(modulo), funcion)
        servicio = fabrica()
    except Exception:
        logger.warning("conversation memory unavailable: the brain runs without it", exc_info=True)
        return MemoriaNula()
    if not isinstance(servicio, ProveedorMemoria):
        logger.warning("the conversation memory service does not implement ProveedorMemoria: ignored")
        return MemoriaNula()
    return servicio


def especialistas_del_hogar(modulos: Sequence[Modulo]) -> Mapping[str, Especialista]:
    """One specialist per household module (the collaboration runtime's ``EspecialistaDeModulo``).

    A module whose specialist cannot be built is left out (logged): the others keep working.
    """
    from asistente.nucleo.cerebro.cerebro import de_la_colaboracion

    clase = de_la_colaboracion("EspecialistaDeModulo")
    if clase is None:
        logger.warning("no EspecialistaDeModulo in the collaboration runtime: household specialists unavailable")
        return {}
    especialistas: dict[str, Especialista] = {}
    for modulo in modulos:
        if modulo.clave == CLAVE_FISCAL:
            continue
        try:
            especialistas[modulo.clave] = _construir(clase, modulo)
        except Exception:
            logger.exception("specialist of module %s unavailable", modulo.clave)
    return especialistas


def _construir(clase: Any, modulo: Modulo) -> Especialista:
    parametros = inspect.signature(clase).parameters
    if "modulo" in parametros:
        return clase(modulo=modulo)  # type: ignore[no-any-return]
    return clase(modulo)  # type: ignore[no-any-return]


def construir_cerebro() -> Cerebro:
    """The production brain: the legacy advisor as its tax specialist, the shared metered provider, the memory."""
    from asistente.nucleo.agent.agent import construir_asesor_fiscal
    from asistente.nucleo.cerebro.cerebro import Cerebro

    asesor = construir_asesor_fiscal()
    return Cerebro(
        asesor=asesor,
        proveedor=asesor._provider,
        verificador=asesor._verifier,
        memoria=servicio_de_memoria(),
        fabrica=especialistas_del_hogar,
    )


__all__ = ["SERVICIO_MEMORIA", "construir_cerebro", "especialistas_del_hogar", "servicio_de_memoria"]

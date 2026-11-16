"""The tax module: IRPF, autónomos, Sociedades, Patrimonio, Sucesiones y Donaciones and the year-end plan.

Everything that knows about taxes lives here, behind the module contract (``modulos/contrato.py``):

* ``calculo/`` — the deterministic calculators (parameters are data in ``asistente/ambitos/fiscal/data``, arithmetic
  is code). The old ``asistente.ambitos.fiscal.calculo.<name>`` paths are aliases of these modules;
* ``especificaciones.py`` / ``herramientas.py`` / ``presentacion.py`` — the tools the advisor calls
  (``calcular_irpf``, ``optimizar_impuestos``…): their specs, their handlers and how their results read;
* ``avisos.py`` — the proactive jobs (deadline reminders and the year-end plan);
* ``modulo.py`` — :class:`ModuloFiscal`, the object the registry discovers as ``MODULO``.

The module sets ``herramientas_directas = True``: the central agent offers its tools directly (no specialist
hop), with the run's evidence ledger, exactly as when they were the advisor's own tools.

This ``__init__`` stays import-light on purpose: importing a calculator (``asistente.ambitos.fiscal.calculo.irpf``, which is
``asistente.ambitos.fiscal.calculo.irpf``) imports this package first, and the module object pulls in the
agent's evidence ledger and the case schemas. ``MODULO`` is therefore loaded on first access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asistente.ambitos.fiscal.modulo import MODULO, ModuloFiscal


def __getattr__(name: str) -> Any:
    if name in ("MODULO", "ModuloFiscal"):
        from asistente.ambitos.fiscal import modulo

        return getattr(modulo, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MODULO", "ModuloFiscal"]

"""Compatibility package, for ONE release: the project's code moved to :mod:`asistente`.

Every old module path (``hacienda_gpt.tax.irpf``, ``hacienda_gpt.modulos.luz``, ``hacienda_gpt.cli.proactive``…,
the aliases of earlier releases included) is an alias of its new module (``_alias.NUEVOS``), so external scripts,
cron lines (``python -m hacienda_gpt.cli.…``) and stored references keep working. Each use warns
(``DeprecationWarning``) with the new path to write instead. Nothing in :mod:`asistente` imports this package.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any
import warnings

from hacienda_gpt._alias import instalar, nuevo_nombre

instalar()

_MENSAJE = "El paquete «hacienda_gpt» está obsoleto y desaparecerá en la próxima versión: usa «asistente»."
if sys.argv[:1] == ["-m"]:
    # ``python -m hacienda_gpt.…`` (a cron line): runpy imports this package, where the default filters would hide
    # the warning; the operator must see it, so it is shown whatever the filters say.
    warnings.showwarning(DeprecationWarning(_MENSAJE), DeprecationWarning, __file__, 0)
else:
    warnings.warn(_MENSAJE, DeprecationWarning, stacklevel=2)


def __getattr__(nombre: str) -> Any:
    """``hacienda_gpt.<child>`` without importing it first (``import hacienda_gpt; hacienda_gpt.settings``)."""
    if nombre.startswith("__") or nuevo_nombre(f"{__name__}.{nombre}") is None:
        raise AttributeError(nombre)
    return importlib.import_module(f"{__name__}.{nombre}")

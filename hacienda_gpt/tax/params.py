"""Moved to :mod:`hacienda_gpt.modulos.fiscal.calculo.params` (the tax module's calculators).

Re-export shim so every existing import keeps working: this old path is an alias of the SAME module object
(``sys.modules``), so module state, ``isinstance`` checks and monkeypatching behave exactly as before.
"""

import sys
from typing import Any

from hacienda_gpt.modulos.fiscal.calculo import params as _modulo
from hacienda_gpt.modulos.fiscal.calculo.params import *  # noqa: F403

sys.modules[__name__] = _modulo


def __getattr__(name: str) -> Any:  # for type checkers: at run time the alias above answers every name
    return getattr(_modulo, name)

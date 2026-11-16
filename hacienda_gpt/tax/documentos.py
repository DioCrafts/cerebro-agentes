"""Moved: the tax domain's document reader is :mod:`hacienda_gpt.modulos.fiscal.documentos`, and the generic
reading (decoding, text extraction, scans read twice) is the ``documentos`` capability
(:mod:`hacienda_gpt.capacidades.documentos`). This name is an alias of the tax reader (the same module object)."""

import sys

from hacienda_gpt.modulos.fiscal import documentos as _modulo
from hacienda_gpt.modulos.fiscal.documentos import *  # noqa: F403 (the names, for type checkers)
from hacienda_gpt.modulos.fiscal.documentos import _normalize as _normalize  # noqa: F401

sys.modules[__name__] = _modulo

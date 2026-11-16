"""Moved to :mod:`hacienda_gpt.capacidades.correo` (the mail CAPABILITY, see ``capacidades/contrato.py``).

This package stays so that (1) the module registry still discovers the mail's chat front (``MODULO``: its
specialist, notices and switch) and (2) every old import path keeps working: each submodule here is an alias of
the moved one (the same module object, so patching either name patches both).
"""

from hacienda_gpt.capacidades.correo import HECHO_VENTANA, MODULO

__all__ = ["HECHO_VENTANA", "MODULO"]

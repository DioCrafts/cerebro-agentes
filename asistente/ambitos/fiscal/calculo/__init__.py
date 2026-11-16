"""Deterministic tax computation: parameters are data, arithmetic is code.

The language model never computes a tax figure. It gathers the user's numbers, calls
:func:`asistente.ambitos.fiscal.calculo.irpf.calcular_irpf` (through the tax module's tools), and explains
the result. The parameter files with their legal quotes are in ``asistente/ambitos/fiscal/data`` (maintained by
the knowledge layer); the old ``hacienda_gpt.tax.<name>`` paths are aliases of these modules in the compatibility
package ``hacienda_gpt``.
"""

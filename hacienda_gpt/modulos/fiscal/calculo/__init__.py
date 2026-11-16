"""Deterministic tax computation: parameters are data, arithmetic is code.

The language model never computes a tax figure. It gathers the user's numbers, calls
:func:`hacienda_gpt.modulos.fiscal.calculo.irpf.calcular_irpf` (through the tax module's tools), and explains
the result. The parameter files with their legal quotes stay in ``hacienda_gpt/tax/data`` (the knowledge
layer); ``hacienda_gpt.tax.<name>`` remains an alias of every module moved here.
"""

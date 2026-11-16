"""The domains (``ámbitos``): fiscal, luz, hipoteca, ayudas, caducidades, suscripciones…

Every sub-package exposing ``MODULO`` is discovered by the registry (:mod:`asistente.nucleo.modulos.registro`):
a domain owns its data, calculators, notices and tools, and declares its integrations with the core in its
``ganchos`` (contract v2). Domains never import each other's internals; they ask capabilities through
:func:`asistente.capacidades.contrato.pedir`. Nothing but domain packages belongs here.
"""

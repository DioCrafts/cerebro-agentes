"""Memory of the user's decisions: what they rejected, accepted, prefer or cannot do.

So the assistant never repeats a proposal the user rejected and personalises without asking twice.

* :mod:`~asistente.nucleo.memoria.modelo` — the ``Decision`` model and its pure rules (expiry, reopening);
* :mod:`~asistente.nucleo.memoria.almacen` — the table in the cases' database (export, erasure, redaction);
* :mod:`~asistente.nucleo.memoria.servicio` — recording from the chat, the buttons, the settings and actions;
* :mod:`~asistente.nucleo.memoria.bandeja` — the inbox's write path: hold back or let come back once;
* :mod:`~asistente.nucleo.memoria.optimizador` — decisions as constraints of the tax optimiser;
* :mod:`~asistente.nucleo.memoria.planificador` — decisions as restrictions of the household planner;
* :mod:`~asistente.nucleo.memoria.agente` — the agent's tools and context block.

This ``__init__`` imports nothing on purpose: the prompts and the inbox import submodules lazily.
"""

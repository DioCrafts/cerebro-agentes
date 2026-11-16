"""Memory of the user's decisions: what they rejected, accepted, prefer or cannot do.

So the assistant never repeats a proposal the user rejected and personalises without asking twice.

* :mod:`~hacienda_gpt.memoria.modelo` — the ``Decision`` model and its pure rules (expiry, reopening);
* :mod:`~hacienda_gpt.memoria.almacen` — the table in the cases' database (export, erasure, redaction);
* :mod:`~hacienda_gpt.memoria.servicio` — recording from the chat, the buttons, the settings and actions;
* :mod:`~hacienda_gpt.memoria.bandeja` — the inbox's write path: hold back or let come back once;
* :mod:`~hacienda_gpt.memoria.optimizador` — decisions as constraints of the tax optimiser;
* :mod:`~hacienda_gpt.memoria.agente` — the agent's tools and context block.

This ``__init__`` imports nothing on purpose: the prompts and the inbox import submodules lazily.
"""

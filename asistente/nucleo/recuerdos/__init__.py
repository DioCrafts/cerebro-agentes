"""Conversation memory: each conversation, a summary of it, and «lo que sé de ti» — every item with its origin.

Until this package the server kept no conversations (the browser sent the history), so a new session started
almost from zero. Now:

* :mod:`~asistente.nucleo.recuerdos.almacen` — the tables (same SQLite file as the cases), strictly per owner
  ``(case_id, user_id)``, erased with the case and with the account;
* :mod:`~asistente.nucleo.recuerdos.privacidad` — the deterministic privacy gates (credentials, identifiers,
  special categories, pasted text, injection bait);
* :mod:`~asistente.nucleo.recuerdos.resumen` and :mod:`~asistente.nucleo.recuerdos.prompts` — the summariser:
  the model proposes, the gate keeps only what the user said in their own words;
* :mod:`~asistente.nucleo.recuerdos.recuperacion` — hybrid retrieval (per-owner BM25, optional embeddings,
  priors, explicit references, MMR) with calibrated relevance;
* :mod:`~asistente.nucleo.recuerdos.servicio` — :func:`~asistente.nucleo.recuerdos.servicio.servicio_memoria`,
  the ``ProveedorMemoria`` the brain uses, and the user's and the jobs' operations.

See ``docs/cerebro/memoria.md``. This ``__init__`` imports nothing on purpose (the stores import lazily).
"""

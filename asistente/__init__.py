"""The household assistant (formerly ``hacienda_gpt``).

* :mod:`asistente.nucleo` — the central brain, long-running tasks, events, memory, actions, profile, evaluation;
* :mod:`asistente.capacidades` — means any domain can use: mail, browser, documents, research;
* :mod:`asistente.ambitos` — the domains: fiscal, luz, hipoteca, ayudas, caducidades, suscripciones;
* :mod:`asistente.conocimiento` — legal data with literal quotes, its verification and re-encoding, the BOE and
  format watch, the corpus (crawlers, processing, retrieval, doctrine);
* :mod:`asistente.plataforma` — API, accounts, storage with migrations, processes, operations, CLI, settings.

``hacienda_gpt`` remains, for one release, a compatibility package whose old module paths are aliases of these.
"""

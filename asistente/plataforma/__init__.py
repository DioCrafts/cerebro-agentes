"""Platform backend: storage with migrations, the process model (api / worker) and operations.

* :mod:`~hacienda_gpt.plataforma.migraciones` — versioned schema migrations per owner package, recorded in
  ``schema_migrations``; every store calls :func:`~hacienda_gpt.plataforma.migraciones.asegurar_esquema`
  when it opens its connection;
* :mod:`~hacienda_gpt.plataforma.procesos` — the process model: the ``worker`` (event bus, scheduled jobs and
  any service other packages register) next to the ``api`` (uvicorn);
* :mod:`~hacienda_gpt.plataforma.copias` — online SQLite backups, optionally encrypted.

This package is imported by the stores themselves, so its ``__init__`` imports nothing.
See docs/despliegue.md.
"""

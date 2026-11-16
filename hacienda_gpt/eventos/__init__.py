"""Event-driven proactivity: react when something happens, waking only the modules it concerns.

See README «Arquitectura orientada a eventos» and docs/operacion.md. Modules:

* :mod:`~hacienda_gpt.eventos.tipos` — event types and the payload rules (ids only, no personal data);
* :mod:`~hacienda_gpt.eventos.bus` — the SQLite outbox, consumers with cursors, deliveries with retries,
  backoff and dead letters, timers, metrics;
* :mod:`~hacienda_gpt.eventos.rutas` — which module each event wakes (subscriptions);
* :mod:`~hacienda_gpt.eventos.manejadores` — running a module and storing its notices like the daily run;
* :mod:`~hacienda_gpt.eventos.productores` — the hooks that emit events;
* :mod:`~hacienda_gpt.eventos.despacho` — in-process dispatch for the API;
* :mod:`~hacienda_gpt.eventos.salud` — what ``ops doctor`` reports.
"""

from __future__ import annotations

from hacienda_gpt.eventos.bus import Bus, Configuracion
from hacienda_gpt.eventos.productores import (
    emitir,
    emitir_cambio_modulo,
    emitir_cambio_normativo,
    emitir_indices,
    emitir_novedades,
    instalar,
)
from hacienda_gpt.eventos.tipos import Evento

__all__ = [
    "Bus",
    "Configuracion",
    "Evento",
    "emitir",
    "emitir_cambio_modulo",
    "emitir_cambio_normativo",
    "emitir_indices",
    "emitir_novedades",
    "instalar",
]

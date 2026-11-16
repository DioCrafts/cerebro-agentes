"""Event-driven proactivity: react when something happens, waking only the modules it concerns.

See README «Arquitectura orientada a eventos» and docs/operacion.md. Modules:

* :mod:`~asistente.nucleo.eventos.tipos` — event types and the payload rules (ids only, no personal data);
* :mod:`~asistente.nucleo.eventos.bus` — the SQLite outbox, consumers with cursors, deliveries with retries,
  backoff and dead letters, timers, metrics;
* :mod:`~asistente.nucleo.eventos.rutas` — which module each event wakes (subscriptions);
* :mod:`~asistente.nucleo.eventos.manejadores` — running a module and storing its notices like the daily run;
* :mod:`~asistente.nucleo.eventos.productores` — the hooks that emit events;
* :mod:`~asistente.nucleo.eventos.despacho` — in-process dispatch for the API;
* :mod:`~asistente.nucleo.eventos.salud` — what ``ops doctor`` reports.
"""

from __future__ import annotations

from asistente.nucleo.eventos.bus import Bus, Configuracion
from asistente.nucleo.eventos.productores import (
    emitir,
    emitir_cambio_modulo,
    emitir_cambio_normativo,
    emitir_indices,
    emitir_novedades,
    instalar,
)
from asistente.nucleo.eventos.tipos import Evento

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

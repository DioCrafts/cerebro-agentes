"""In-process dispatch: the API drains the bus right after a commit, so a change shows up in seconds.

Disabled until :func:`habilitar` (the API calls it at start-up when ``EVENTOS_DESPACHO_EN_PROCESO`` is on).
One daemon thread per database file waits for a signal from a producer (:func:`despertar`) or for the poll
interval, then runs the same cycle as ``cli.eventos worker``: timers, consumers, deliveries. A producer
signals just BEFORE its transaction commits, so the thread waits a moment first and, finding nothing, looks
once more; the periodic poll covers anything else (including events written by other processes). It is a
latency optimisation only: the separate worker and the daily run see exactly the same state, and leases
keep the thread and any worker from running a delivery twice.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from hacienda_gpt import settings

logger = logging.getLogger(__name__)

ESPERA_TRAS_SENAL_S = 0.05
REINTENTO_S = 0.25

_lock = threading.Lock()
_habilitado = False
_hilos: dict[str, _Despachador] = {}


class _Despachador:
    def __init__(self, db_path: str, intervalo: float) -> None:
        self.db_path = db_path
        self.intervalo = intervalo
        self.senal = threading.Event()
        self.parar = threading.Event()
        self.nombre = f"api-{os.getpid()}-{len(_hilos)}"
        self.hilo = threading.Thread(target=self._bucle, name=f"eventos-{self.nombre}", daemon=True)
        self.hilo.start()

    def _bucle(self) -> None:
        from hacienda_gpt.eventos.bus import Bus, consumidores_del_trabajador

        bus: Bus | None = None
        while not self.parar.is_set():
            senal = self.senal.wait(self.intervalo)
            self.senal.clear()
            if self.parar.is_set():
                break
            try:
                if bus is None:
                    bus = Bus(self.db_path, consumidores=consumidores_del_trabajador())
                if senal:
                    time.sleep(ESPERA_TRAS_SENAL_S)
                resumen = bus.drenar(self.nombre)
                if senal and not (resumen.get("eventos") or resumen.get("entregas")):
                    time.sleep(REINTENTO_S)
                    bus.drenar(self.nombre)
            except Exception:
                logger.exception("In-process event dispatch failed (the worker or the daily run will catch up)")
        if bus is not None:
            bus.close()


def _intervalo() -> float:
    try:
        return max(0.5, float(getattr(settings, "EVENTOS_INTERVALO_SEGUNDOS", 5)))
    except (TypeError, ValueError):
        return 5.0


def habilitar(db_path: str | None = None) -> bool:
    """Turn in-process dispatch on (and start polling ``db_path``). False when the setting keeps it off."""
    global _habilitado
    if not getattr(settings, "EVENTOS_DESPACHO_EN_PROCESO", True):
        return False
    if not getattr(settings, "EVENTOS_DESPACHO_EN_API", True):  # process split (hacienda_gpt/plataforma)
        return False
    with _lock:
        _habilitado = True
    if db_path:
        despertar(db_path)
    return True


def despertar(db_path: str) -> None:
    """A producer wrote an event in ``db_path``. No-op unless dispatch is enabled in this process."""
    if not _habilitado or not db_path:
        return
    with _lock:
        hilo = _hilos.get(db_path)
        if hilo is None or not hilo.hilo.is_alive():
            hilo = _hilos[db_path] = _Despachador(db_path, _intervalo())
    hilo.senal.set()


def detener(timeout: float = 5.0) -> None:
    """Stop every dispatcher thread and disable dispatch (tests, shutdown)."""
    global _habilitado
    with _lock:
        _habilitado = False
        hilos = list(_hilos.values())
        _hilos.clear()
    for hilo in hilos:
        hilo.parar.set()
        hilo.senal.set()
    for hilo in hilos:
        hilo.hilo.join(timeout)


def habilitado() -> bool:
    return _habilitado


__all__ = ["despertar", "detener", "habilitado", "habilitar"]

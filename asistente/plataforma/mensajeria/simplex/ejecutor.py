"""Runs the gateway's handler off the channel's event loop: in order per contact, in parallel across contacts.

Why: the handler (the gateway) may take long — it can wait for the brain — and must never stall the event loop that
also completes file downloads and feeds every other chat. Messages from ONE contact keep their order (a question
sent after a document must reach the gateway after it); different contacts do not wait for each other.

Bounded: at most ``max_hilos`` worker threads and ``max_pendientes`` queued tasks; :meth:`EjecutorPorClave.enviar`
blocks the caller (the event loop) when the bound is reached — back-pressure goes to the client's bounded event queue,
never to the socket reader. Each task runs in a copy of the submitter's ``contextvars`` context (contextvars do not
cross threads on their own). A task's exception is logged by class and location only (its message could quote the
user's text) and never kills a worker.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Hashable
import contextvars
from dataclasses import dataclass
import logging
import threading
import traceback
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _Tarea:
    contexto: contextvars.Context
    funcion: Callable[..., Any]
    argumentos: tuple[Any, ...]


def describir_excepcion(e: BaseException) -> str:
    """``RuntimeError en pasarela.py:123 (procesar)`` — class and where it was raised, never its message."""
    marcos = traceback.extract_tb(e.__traceback__) if e.__traceback__ else []
    if not marcos:
        return type(e).__name__
    ultimo = marcos[-1]
    archivo = ultimo.filename.rsplit("/", 1)[-1]
    return f"{type(e).__name__} en {archivo}:{ultimo.lineno} ({ultimo.name})"


class EjecutorPorClave:
    def __init__(self, max_hilos: int = 4, max_pendientes: int = 200, nombre: str = "simplex-manejador") -> None:
        self._max_hilos = max(1, max_hilos)
        self._max_pendientes = max(1, max_pendientes)
        self._nombre = nombre
        self._cond = threading.Condition()
        self._colas: dict[Hashable, deque[_Tarea]] = {}
        self._listas: deque[Hashable] = deque()  # keys with queued work and no task running
        self._activas: set[Hashable] = set()
        self._pendientes = 0
        self._hilos: list[threading.Thread] = []
        self._ocupados = 0
        self._cerrado = False

    @property
    def pendientes(self) -> int:
        with self._cond:
            return self._pendientes

    def es_hilo_propio(self) -> bool:
        return threading.current_thread() in self._hilos

    def enviar(
        self, clave: Hashable, funcion: Callable[..., Any], *argumentos: Any, timeout: float | None = None
    ) -> bool:
        """Queue ``funcion(*argumentos)`` behind the other tasks of ``clave``. False when closed (or timed out)."""
        tarea = _Tarea(contextvars.copy_context(), funcion, argumentos)
        with self._cond:
            if not self._cond.wait_for(lambda: self._cerrado or self._pendientes < self._max_pendientes, timeout):
                return False
            if self._cerrado:
                return False
            cola = self._colas.get(clave)
            if cola is None:
                cola = self._colas[clave] = deque()
            cola.append(tarea)
            self._pendientes += 1
            if clave not in self._activas and len(cola) == 1:
                self._listas.append(clave)
            libres = len(self._hilos) - self._ocupados
            if len(self._listas) > libres and len(self._hilos) < self._max_hilos:
                hilo = threading.Thread(
                    target=self._trabajar, name=f"{self._nombre}-{len(self._hilos) + 1}", daemon=True
                )
                self._hilos.append(hilo)
                hilo.start()
            self._cond.notify_all()
        return True

    def _trabajar(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._cerrado or bool(self._listas))
                if not self._listas:  # closed and nothing left
                    return
                clave = self._listas.popleft()
                tarea = self._colas[clave].popleft()
                self._activas.add(clave)
                self._ocupados += 1
            try:
                tarea.contexto.run(tarea.funcion, *tarea.argumentos)
            except Exception as e:
                logger.error("simplex: el manejador falló: %s", describir_excepcion(e))
            finally:
                with self._cond:
                    self._ocupados -= 1
                    self._pendientes -= 1
                    self._activas.discard(clave)
                    if self._colas[clave]:
                        self._listas.append(clave)
                    else:
                        del self._colas[clave]
                    self._cond.notify_all()

    def cerrar(self, timeout: float = 10.0) -> int:
        """Run what is queued, then stop the workers (waiting up to ``timeout`` s). Returns the tasks left undone."""
        with self._cond:
            self._cerrado = True
            self._cond.notify_all()
        actual = threading.current_thread()
        for hilo in list(self._hilos):
            if hilo is not actual:
                hilo.join(timeout)
        with self._cond:
            return self._pendientes


__all__ = ["EjecutorPorClave", "describir_excepcion"]

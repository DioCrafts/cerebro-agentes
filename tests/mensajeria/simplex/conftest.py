from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from asistente.plataforma.mensajeria.contrato import MensajeEntrante
from asistente.plataforma.mensajeria.simplex import CanalSimplex
from tests.mensajeria.simplex.falso import ServidorSimplexFalso, configuracion_para


def esperar(condicion: Callable[[], bool], timeout: float = 5.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.01)
    return condicion()


class Arnes:
    """A CanalSimplex running in its own thread against the fake CLI, recording what the handler receives."""

    def __init__(self, canal: CanalSimplex, manejador: Callable[[MensajeEntrante], None] | None = None) -> None:
        self.canal = canal
        self.recibidos: list[MensajeEntrante] = []
        self._lock = threading.Lock()
        self._manejador_extra = manejador
        self.hilo = threading.Thread(target=canal.iniciar, args=(self._manejar,), name="test-iniciar", daemon=True)

    def _manejar(self, mensaje: MensajeEntrante) -> None:
        with self._lock:
            self.recibidos.append(mensaje)
        if self._manejador_extra is not None:
            self._manejador_extra(mensaje)

    def arrancar(self, esperar_conexion: bool = True) -> Arnes:
        self.hilo.start()
        if esperar_conexion:
            assert esperar(lambda: self.canal.estado().conectado), self.canal.estado()
        return self

    def mensajes(self, n: int, timeout: float = 5.0) -> list[MensajeEntrante]:
        assert esperar(lambda: len(self.recibidos) >= n, timeout), f"recibidos {len(self.recibidos)} de {n}"
        with self._lock:
            return list(self.recibidos)

    def parar(self) -> None:
        self.canal.parar()
        self.hilo.join(10)


@pytest.fixture
def cli() -> Iterator[ServidorSimplexFalso]:
    servidor = ServidorSimplexFalso().arrancar()
    try:
        yield servidor
    finally:
        servidor.detener()


@pytest.fixture
def arrancar(tmp_path: Path) -> Iterator[Callable[..., Arnes]]:
    arneses: list[Arnes] = []

    def _arrancar(
        cli: ServidorSimplexFalso,
        manejador: Callable[[MensajeEntrante], None] | None = None,
        esperar_conexion: bool = True,
        **cambios: Any,
    ) -> Arnes:
        canal = CanalSimplex(configuracion_para(cli, tmp_path, **cambios))
        arnes = Arnes(canal, manejador)
        arneses.append(arnes)
        return arnes.arrancar(esperar_conexion)

    yield _arrancar
    for arnes in arneses:
        arnes.parar()

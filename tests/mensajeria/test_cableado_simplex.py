"""The gateway wired to the real SimpleX adapter (``CanalSimplex``) against the adapter's fake simplex-chat CLI:
the service publishes the bot's address, a code pairs the contact, a question is answered with the progress line
edited in place, and unpairing removes the contact on SimpleX."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from asistente.plataforma.mensajeria import servicio as servicio_mod
from asistente.plataforma.mensajeria.almacen import almacen
from asistente.plataforma.mensajeria.emparejamiento import nuevo_codigo
from asistente.plataforma.mensajeria.pasarela import Pasarela
from asistente.plataforma.mensajeria.simplex import CanalSimplex
from .conftest import CASE_ID, PREGUNTA_FISCAL
from .simplex.falso import ServidorSimplexFalso, configuracion_para
from .test_conversacion import _cerebro_fiscal


def _hasta(condicion: Any, segundos: float = 10.0) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return bool(condicion())


@pytest.fixture
def cli() -> Iterator[ServidorSimplexFalso]:
    servidor = ServidorSimplexFalso().arrancar()
    try:
        yield servidor
    finally:
        servidor.detener()


def test_the_gateway_through_the_simplex_adapter(crear_montaje: Any, cli: ServidorSimplexFalso, tmp_path: Path) -> None:
    montaje = crear_montaje()  # the case and the settings; its in-memory channel is not used here
    canal = CanalSimplex(configuracion_para(cli, tmp_path / "simplex"))
    brain, memoria = _cerebro_fiscal()
    pasarela = Pasarela(montaje.db, canales=[canal], agente=lambda: brain, demora_progreso=60, timeout=20)
    servicio = servicio_mod.ServicioMensajeria(montaje.db, canales=[canal], pasarela=pasarela, tick=0.02)
    parar = threading.Event()
    hilo = threading.Thread(target=servicio.arrancar, args=(parar,))
    hilo.start()
    try:
        # The adapter connects and the service publishes the bot's address for the app.
        assert _hasta(lambda: any(e.conectado and e.direccion for e, _m in almacen(montaje.db).estados()))
        estado, _momento = almacen(montaje.db).estados()[0]
        assert estado.canal == "simplex" and estado.direccion == "https://smp.ejemplo.org/a#direccion-1"

        # Pairing: a contact connects and sends the code generated in the app.
        codigo = nuevo_codigo(
            almacen(montaje.db), canal="simplex", user_id=None, case_id=CASE_ID, momento=datetime.now(UTC)
        )
        cli.contacto_conectado(7, "Ana")
        cli.mensaje(7, f"Hola, mi código es {codigo.codigo}")
        assert _hasta(lambda: almacen(montaje.db).vinculo_de("simplex", "7") is not None)
        assert _hasta(lambda: any("vinculado a *tu hogar*" in e["texto"] for e in cli.enviados))

        # A question: the progress line, then edited into the answer, in SimpleX's markup.
        cli.mensaje(7, PREGUNTA_FISCAL)
        assert _hasta(lambda: any(e["texto"].startswith("El plazo es de *dos años* [F2].") for e in cli.editados))
        progreso = next(e for e in cli.enviados if e["texto"].startswith("El plazo es de"))
        assert progreso["contacto"] == 7
        assert memoria.turnos and memoria.turnos[0].user_id == "u-cerebro"

        # Unpairing from the chat removes the contact on SimpleX.
        assert pasarela.esperar(10)
        cli.mensaje(7, "/desemparejar")
        pide = "Para hacerlo escribe: /desemparejar "
        assert _hasta(lambda: any(pide in e["texto"] for e in cli.enviados))
        aviso = next(e["texto"] for e in reversed(cli.enviados) if pide in e["texto"])
        cli.mensaje(7, f"/desemparejar {aviso.rsplit(' ', 1)[1]}")
        assert _hasta(lambda: 7 in cli.borrados)
        assert almacen(montaje.db).vinculo_de("simplex", "7") is None
    finally:
        parar.set()
        hilo.join(15)
    assert not hilo.is_alive()

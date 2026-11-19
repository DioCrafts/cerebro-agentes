"""End to end: the gateway (agent I's ``Pasarela``) with ``CanalSimplex`` over the fake CLI and a scripted brain.

A contact connects, sends the one-time code generated «in the app» and is paired; then asks one question and gets
the brain's verified answer as a reply to its message — every hop through the real SimpleX adapter and the
documented WebSocket protocol. Skips cleanly when the gateway's modules are not available.
"""

from __future__ import annotations

from datetime import UTC, datetime
import threading

import pytest

pytest.importorskip("asistente.plataforma.mensajeria.pasarela")
pytest.importorskip("asistente.plataforma.mensajeria.emparejamiento")

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore  # noqa: E402
from asistente.plataforma import settings  # noqa: E402
from asistente.plataforma.mensajeria import emparejamiento  # noqa: E402
from asistente.plataforma.mensajeria.almacen import almacen  # noqa: E402
from asistente.plataforma.mensajeria.pasarela import Pasarela  # noqa: E402
from asistente.plataforma.mensajeria.simplex import CanalSimplex  # noqa: E402
from tests.agent.fakes import text_turn, tool_turn, verdict  # noqa: E402
from tests.cerebro.fakes import Grabador, MemoriaFalsa, caso, cerebro  # noqa: E402
from tests.mensajeria.conftest import CASE_ID, PREGUNTA_FISCAL  # noqa: E402
from tests.mensajeria.simplex.conftest import esperar  # noqa: E402
from tests.mensajeria.simplex.falso import ServidorSimplexFalso, configuracion_para  # noqa: E402


def test_emparejar_y_responder_una_pregunta_por_simplex(tmp_path, monkeypatch, entorno_mensajeria):
    db = str(tmp_path / "estado.sqlite3")
    casos = SQLiteCaseStateStore(db)
    casos.save_case(caso())
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", db)
    fiscal = Grabador(
        [
            tool_turn(("buscar_fuentes", {"consulta": "plazo reinversión"})),
            text_turn("El plazo es de **dos años** [F2]."),
        ],
        [verdict("respuesta", ("Plazo dos años", ["F2"], "respaldada"))],
    )
    brain = cerebro(fiscal=fiscal, memoria=MemoriaFalsa())

    with ServidorSimplexFalso() as cli:
        canal = CanalSimplex(configuracion_para(cli, tmp_path))
        pasarela = Pasarela(db, canales=[canal], agente=lambda: brain, demora_progreso=60, timeout=20)
        hilo = threading.Thread(target=canal.iniciar, args=(pasarela.manejar,), daemon=True)
        hilo.start()
        try:
            assert esperar(lambda: canal.estado().conectado)
            assert canal.estado().direccion  # what the app shows as a QR

            # Pairing: the contact connects through the address and sends the code generated in the app.
            codigo = emparejamiento.nuevo_codigo(
                almacen(db), canal="simplex", user_id=None, case_id=CASE_ID, momento=datetime.now(UTC)
            ).codigo
            cli.contacto_conectado(7, "Lucía")
            cli.mensaje(7, f"Hola, mi código es {codigo}", nombre="Lucía")
            assert esperar(lambda: almacen(db).vinculo_de("simplex", "7") is not None, 10)
            assert esperar(lambda: len(cli.enviados) >= 1, 10)  # the gateway's confirmation reached the chat
            assert all(e["contacto"] == 7 for e in cli.enviados)

            # One question, answered by the brain as a reply to it.
            antes = len(cli.enviados)
            pregunta = cli.mensaje(7, PREGUNTA_FISCAL, nombre="Lucía")
            assert esperar(lambda: any("dos años" in e["texto"] for e in cli.enviados[antes:]), 30)
            assert pasarela.esperar(30)
            respuesta = next(e for e in cli.enviados[antes:] if "dos años" in e["texto"])
            assert respuesta["contacto"] == 7
            assert respuesta["cita_id"] == pregunta  # sent as a reply to the user's message
            assert "*dos años*" in respuesta["texto"]  # SimpleX rich text (bold is *…*)
        finally:
            canal.parar()
            hilo.join(10)
            pasarela.cerrar(5)
            casos.close()

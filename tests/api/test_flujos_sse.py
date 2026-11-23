"""Server-Sent Events: the framing, the snapshot stream, and ``/agent/stream`` (a slot cap, and a turn whose client
goes away stops spending)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from asistente.plataforma.api import api as api_mod
from asistente.plataforma.api import sse
from asistente.plataforma.api.api import app, get_agent
from asistente.plataforma.ops import consumo
from asistente.plataforma.ops.consumo import ConsumoStore, MeteredProvider, TurnoCanceladoError, contexto_consumo
from tests.agent.fakes import ScriptedProvider, text_turn

client = TestClient(app)


@pytest.fixture(autouse=True)
def _sin_agente() -> Any:
    yield
    app.dependency_overrides.pop(get_agent, None)


# --- framing --------------------------------------------------------------------------------------------------------


def test_an_event_has_its_id_name_and_json_data_and_parses_back() -> None:
    frame = sse.evento("progress", {"type": "thinking", "texto": "línea\nnueva"}, id="7", reintento_ms=3000)
    assert frame.endswith("\n\n") and frame.count("\n\n") == 1
    assert frame.splitlines()[:3] == ["retry: 3000", "id: 7", "event: progress"]
    texto = frame + sse.comentario() + sse.evento("result", {"ok": True}, id="8")
    assert sse.leer_eventos(texto) == [
        {"event": "progress", "id": "7", "data": {"type": "thinking", "texto": "línea\nnueva"}},
        {"event": "result", "id": "8", "data": {"ok": True}},
    ]


def test_an_id_cannot_break_the_frame() -> None:
    assert "id: ab\n" in sse.evento("x", 1, id="a\nb")


class _PeticionFalsa:
    """Just what :func:`sse.flujo_de_instantaneas` reads from a request."""

    def __init__(self, last_event_id: str | None = None, desconectar_tras: int | None = None) -> None:
        self.headers = {"last-event-id": last_event_id} if last_event_id else {}
        self._consultas = 0
        self._desconectar_tras = desconectar_tras

    async def is_disconnected(self) -> bool:
        self._consultas += 1
        return self._desconectar_tras is not None and self._consultas > self._desconectar_tras


async def _todo(flujo: Any) -> str:
    return "".join([frame async for frame in flujo])


def test_snapshots_are_sent_only_when_they_change_and_the_stream_ends_when_final() -> None:
    estados = iter([{"n": 1}, {"n": 1}, {"n": 2}, {"n": 3, "fin": True}])

    def leer() -> sse.Instantanea:
        datos = next(estados)
        return sse.Instantanea(datos=datos, final=bool(datos.get("fin")))

    texto = asyncio.run(_todo(sse.flujo_de_instantaneas(_PeticionFalsa(), leer, nombre="x", intervalo=0)))
    eventos = sse.leer_eventos(texto)
    assert [e["event"] for e in eventos] == ["x", "x", "x", "fin"]
    assert [e["data"].get("n") for e in eventos[:3]] == [1, 2, 3]
    assert eventos[-1]["data"] == {"motivo": "terminada"} and eventos[-1]["id"] == eventos[-2]["id"]
    assert len({e["id"] for e in eventos[:3]}) == 3


def test_a_reconnection_with_last_event_id_does_not_repeat_the_known_state() -> None:
    datos = {"estado": "terminada"}
    leer = lambda: sse.Instantanea(datos=datos, final=True)  # noqa: E731
    peticion = _PeticionFalsa(last_event_id=sse.huella(datos))
    eventos = sse.leer_eventos(asyncio.run(_todo(sse.flujo_de_instantaneas(peticion, leer, nombre="x"))))
    assert [e["event"] for e in eventos] == ["fin"]


def test_the_stream_stops_reading_when_the_client_goes_away_and_reports_a_vanished_item() -> None:
    lecturas: list[int] = []

    def leer() -> sse.Instantanea:
        lecturas.append(1)
        return sse.Instantanea(datos={"n": len(lecturas)}, final=False)

    flujo = sse.flujo_de_instantaneas(_PeticionFalsa(desconectar_tras=3), leer, nombre="x", intervalo=0)
    eventos = sse.leer_eventos(asyncio.run(_todo(flujo)))
    assert len(lecturas) == 3 and [e["event"] for e in eventos] == ["x", "x", "x"]

    vacio = sse.flujo_de_instantaneas(_PeticionFalsa(), lambda: None, nombre="x")
    assert sse.leer_eventos(asyncio.run(_todo(vacio))) == [
        {"event": "fin", "id": None, "data": {"motivo": "no_encontrada"}}
    ]


def test_a_long_quiet_stream_sends_keep_alives_and_ends_after_its_maximum_duration() -> None:
    leer = lambda: sse.Instantanea(datos={"n": 1}, final=False)  # noqa: E731
    flujo = sse.flujo_de_instantaneas(
        _PeticionFalsa(), leer, nombre="x", intervalo=0.01, latido=0.02, duracion_maxima=0.1
    )
    texto = asyncio.run(_todo(flujo))
    assert ": ping" in texto and len(sse.leer_eventos(texto)) == 1


# --- the cancellation token -----------------------------------------------------------------------------------------


def test_a_cancelled_question_makes_no_further_metered_call(tmp_path: Path) -> None:
    store = ConsumoStore(str(tmp_path / "state.sqlite3"), precios=consumo.cargar_precios(consumo.PRECIOS_PATH))
    guion = ScriptedProvider([text_turn("uno")], [{"a": 1}, {"a": 2}])
    medido = MeteredProvider(guion, store, purpose="hechos")
    cancelacion = threading.Event()
    with contexto_consumo(user_id="u1", cancelacion=cancelacion):
        assert medido.complete_json(system="S", user="u", schema={}, schema_name="x") == {"a": 1}
        cancelacion.set()
        with pytest.raises(TurnoCanceladoError):
            medido.complete_json(system="S", user="u", schema={}, schema_name="x")
    # Not an Exception: the agent's «never break the turn» handlers do not swallow it.
    assert not issubclass(TurnoCanceladoError, Exception)


# --- /agent/stream --------------------------------------------------------------------------------------------------


class _AgenteLento:
    """Emits a progress event every few milliseconds for a long time; records how it ended."""

    def __init__(self, eventos: int = 400) -> None:
        self.eventos = eventos
        self.emitidos = 0
        self.cancelado = False
        self.fin = threading.Event()

    def run(self, query: str, *, on_event: Any = None, **_: Any) -> Any:
        try:
            for i in range(self.eventos):
                on_event({"type": "thinking", "iteration": i})
                self.emitidos += 1
                time.sleep(0.01)
            raise AssertionError("the turn was never cancelled")
        except TurnoCanceladoError:
            self.cancelado = True
            raise
        finally:
            self.fin.set()


def _scope(cuerpo: bytes, spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/agent/stream",
        "raw_path": b"/agent/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(cuerpo)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


# 2.3 (uvicorn): Starlette cancels the response on ``http.disconnect``; 2.4: the stream notices on its own.
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_a_client_that_disconnects_cancels_the_turn_and_frees_its_slot(spec_version: str) -> None:
    agente = _AgenteLento()
    app.dependency_overrides[get_agent] = lambda: agente
    cuerpo = json.dumps({"query": "¿Cuánto pago?"}).encode()
    enviados: list[dict[str, Any]] = []

    async def conversar() -> None:
        pendientes = [{"type": "http.request", "body": cuerpo, "more_body": False}]
        desconectado = asyncio.Event()

        async def receive() -> dict[str, Any]:
            if pendientes:
                return pendientes.pop(0)
            await desconectado.wait()
            return {"type": "http.disconnect"}

        async def send(mensaje: dict[str, Any]) -> None:
            if desconectado.is_set() and spec_version == "2.4":
                raise OSError("the peer closed the connection")  # what a 2.4 server does after a disconnect
            enviados.append(mensaje)
            # The browser tab is closed as soon as the first progress event arrives.
            if b"event: progress" in mensaje.get("body", b""):
                desconectado.set()

        try:
            await asyncio.wait_for(app(_scope(cuerpo, spec_version), receive, send), timeout=10)
        except OSError:
            pass  # 2.4: the server sees its own send fail and drops the connection

    asyncio.run(conversar())
    assert agente.fin.wait(5), "the turn kept running after its client left"
    assert agente.cancelado and agente.emitidos < agente.eventos
    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    assert dict(inicio["headers"])[b"content-type"].startswith(b"text/event-stream")
    limite = time.monotonic() + 2
    while api_mod._limite_turnos.en_curso and time.monotonic() < limite:
        time.sleep(0.01)
    assert api_mod._limite_turnos.en_curso == 0


def test_turns_over_the_cap_wait_and_are_refused_after_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[get_agent] = lambda: _AgenteLento()
    monkeypatch.setattr(api_mod, "_limite_turnos", api_mod._LimiteTurnos(1))
    monkeypatch.setattr(api_mod, "AGENT_STREAM_ESPERA_SEGUNDOS", 0.3)
    assert api_mod._limite_turnos.tomar()  # another turn holds the only slot
    respuesta = client.post("/agent/stream", json={"query": "hola"})
    eventos = sse.leer_eventos(respuesta.text)
    assert [e["event"] for e in eventos] == ["cola", "error"]
    assert eventos[-1]["data"]["status"] == 503 and "demasiadas consultas" in eventos[-1]["data"]["detail"]
    assert [e["id"] for e in eventos] == ["1", "2"]
    assert api_mod._limite_turnos.en_curso == 1  # the refused turn never took a slot


def test_the_slot_counter_frees_and_never_goes_below_zero() -> None:
    limite = api_mod._LimiteTurnos(1)
    assert limite.tomar() and not limite.tomar()
    limite.soltar()
    assert limite.tomar()
    limite.soltar()
    limite.soltar()  # never below zero
    assert limite.en_curso == 0

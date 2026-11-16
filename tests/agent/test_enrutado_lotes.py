"""Batch mode: submit, poll, collect, replay; resumable state; batch prices; the recodificación by batches.

The real Anthropic SDK talks to a fake HTTP Message Batches API: no network, no key.
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
from typing import Any

import pytest

from asistente.ambitos.fiscal.calculo.params import DATA_DIR
from asistente.conocimiento.recodificacion import AlmacenPropuestas, ejecutar, prompts
from asistente.conocimiento.recodificacion import lotes as recod_lotes
from asistente.conocimiento.recodificacion.evaluacion import evaluar
from asistente.nucleo.agent import enrutado
from asistente.nucleo.agent.providers.anthropic_provider import AnthropicProvider
from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.agent.providers.lotes import (
    ColaDeLotes,
    LoteSinTerminarError,
    PendienteDeLoteError,
    ProveedorEnLote,
    clave_de_peticion,
    ejecutar_en_lotes,
)
from asistente.plataforma import settings
from asistente.plataforma.ops import consumo
from asistente.plataforma.ops.consumo import (
    ConsumoStore,
    MeteredProvider,
    Presupuesto,
    PresupuestoAgotadoError,
    cargar_precios,
)
from tests.agent.fakes_http import FakeAnthropicAPI
from tests.tax import test_recodificacion as recod
from tests.tax.test_recodificacion import HOY, NUEVA, Guion, _objetivo, lectura, revision, texto_de_ley

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}
SIN_ESPERA = {"esperar": lambda segundos: None, "intervalo": 0.0}


@pytest.fixture(autouse=True)
def sin_enrutado(monkeypatch: pytest.MonkeyPatch) -> None:
    for nombre in ("LLM_ROUTING_PROFILE", "LLM_MODEL_SMALL", "LLM_MODEL_MEDIUM", "LLM_MODEL_LARGE", "LLM_ROUTES"):
        monkeypatch.setattr(settings, nombre, "")
    for nombre in enrutado.AJUSTES_PREVIOS.values():
        monkeypatch.setattr(settings, nombre, "")
    monkeypatch.setattr(settings, "LLM_ESCALATION", True)
    for variable in list(os.environ):
        if variable.startswith("LLM_ROUTE_"):
            monkeypatch.delenv(variable)


@pytest.fixture
def datos(tmp_path: Path) -> Path:
    """The parameter files of the recodificación tests (Galicia hanging from their synthetic law)."""
    carpeta = tmp_path / "data"
    carpeta.mkdir()
    for ejercicio in (2025, 2026):
        raw = json.loads((DATA_DIR / f"irpf_{ejercicio}.json").read_text(encoding="utf-8"))
        galicia = raw["comunidades"]["galicia"]
        galicia["fuente_principal"]["boe_id"] = recod.LEY
        galicia["deducciones_calculables"] = [dict(recod.ANTIGUA)]
        galicia["minimos"] = None
        galicia["escala_general"] = {
            "tramos": [
                {"hasta": "12000", "tipo": "0.09", "cuota_hasta_inicio": "0"},
                {"hasta": None, "tipo": "0.19", "cuota_hasta_inicio": "1080"},
            ],
            "fuente": {"referencia": "art. 4 de la ley sintética", "en_vigor_desde": "2020-01-02", "verificado": True},
        }
        (carpeta / f"irpf_{ejercicio}.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", "utf-8")
    evidencia = json.loads((DATA_DIR / "evidencia_deducciones.json").read_text(encoding="utf-8"))
    evidencia["galicia"] = {"art. 5": list(recod.CITAS_ANTIGUAS)}
    (carpeta / "evidencia_deducciones.json").write_text(json.dumps(evidencia, ensure_ascii=False, indent=2), "utf-8")
    return carpeta


@pytest.fixture
def store(tmp_path: Path) -> ConsumoStore:
    return ConsumoStore(str(tmp_path / "state.sqlite3"), precios=cargar_precios(consumo.PRECIOS_PATH))


def _filas(store: ConsumoStore) -> list[dict[str, Any]]:
    return [dict(row) for row in store._conn.execute("SELECT * FROM llm_usage ORDER BY id")]


def _doble(params: dict[str, Any]) -> str:
    """The fake model: doubles the number in the user message."""
    return json.dumps({"n": 2 * int(params["messages"][0]["content"])})


def _medido(api: FakeAnthropicAPI, store: ConsumoStore, **kw: Any) -> MeteredProvider:
    provider = AnthropicProvider(model="claude-sonnet-5-5", client=api.client())
    return MeteredProvider(provider, store, purpose="recodificacion", **kw)


def _cadena(proveedor: Any, inicio: int, pasos: int) -> int:
    """A unit of work with sequential calls, each depending on the previous answer."""
    n = inicio
    for _ in range(pasos):
        n = proveedor.complete_json(system="S", user=str(n), schema=SCHEMA, schema_name="x")["n"]
    return n


# --------------------------------------------------------------------------- #
# The generic driver
# --------------------------------------------------------------------------- #


def test_batch_lifecycle_submit_poll_collect_replay_and_account_at_batch_prices(tmp_path, store) -> None:
    api = FakeAnthropicAPI(_doble, polls_hasta_terminar=2)
    real = _medido(api, store)
    cola = ColaDeLotes(tmp_path / "lotes" / "prueba.json", tipo="prueba")
    lote = ProveedorEnLote(real, cola)
    esperas: list[float] = []

    def ronda() -> list[int | None]:
        salida: list[int | None] = []
        for inicio in (1, 5):
            try:
                salida.append(_cadena(lote, inicio, 2))
            except PendienteDeLoteError:  # this unit waits for the batch; the other goes on
                salida.append(None)
        return salida

    resultado = ejecutar_en_lotes(
        ronda,
        cola,
        contable=real,
        esperar=esperas.append,
        intervalo=30.0,
    )

    assert resultado == [4, 20]
    # Two rounds of two requests (one per unit); every batch polled until it ended.
    creados = api.lotes_creados()
    assert [len(c["requests"]) for c in creados] == [2, 2] and esperas == [30.0, 30.0, 30.0, 30.0]
    assert api.cuerpos() == []  # nothing went to the synchronous endpoint
    peticion = creados[0]["requests"][0]
    params = peticion["params"]
    # The synchronous request, minus what batches reject; the custom_id is the request's hash.
    assert params["model"] == "claude-sonnet-5-5" and params["output_config"]["format"]["schema"] == SCHEMA
    assert params["thinking"] == {"type": "adaptive"} and "fallbacks" not in params and "stream" not in params
    assert peticion["custom_id"] == clave_de_peticion("anthropic", params) and len(peticion["custom_id"]) == 64
    # Every result recorded once, flagged as batch and priced at half: (1000 × 2 + 100 × 10) / 1e6 × 0.5.
    filas = _filas(store)
    assert len(filas) == 4 and all(f["batch"] == 1 and f["purpose"] == "recodificacion" for f in filas)
    assert [f["cost_usd"] for f in filas] == pytest.approx([0.0015] * 4)
    assert cola.resumen()["rondas"] == 2 and [h["coste_usd"] for h in cola.resumen()["lotes"]] == ["0.003", "0.003"]
    cola.terminar()
    assert not cola.path.exists()


def test_a_batch_that_outlives_the_wait_is_resumed_without_paying_twice(tmp_path, store) -> None:
    api = FakeAnthropicAPI(_doble, polls_hasta_terminar=3)
    path = tmp_path / "prueba.json"
    cola = ColaDeLotes(path, tipo="prueba")
    lote = ProveedorEnLote(_medido(api, store), cola)
    with pytest.raises(LoteSinTerminarError, match="vuelve a lanzar"):
        ejecutar_en_lotes(lambda: _cadena(lote, 3, 1), cola, max_espera=timedelta(0), **SIN_ESPERA)
    assert len(api.lotes_creados()) == 1 and json.loads(path.read_text())["en_curso"]

    # A new process: the state on disk says which batch is in flight; it is collected, not sent again.
    cola = ColaDeLotes(path, tipo="prueba")
    real = _medido(api, store)
    lote = ProveedorEnLote(real, cola)
    assert ejecutar_en_lotes(lambda: _cadena(lote, 3, 1), cola, contable=real, **SIN_ESPERA) == 6
    assert len(api.lotes_creados()) == 1 and len(_filas(store)) == 1
    with pytest.raises(ValueError, match="estado de lotes"):
        ColaDeLotes(path, tipo="otra-cosa")


def test_transient_failures_are_sent_again_and_rejected_requests_fail_as_a_synchronous_call_would(
    tmp_path, store
) -> None:
    intentos: dict[str, int] = {}

    def resultado_de(custom_id: str, params: dict[str, Any]) -> dict[str, Any] | None:
        n = int(params["messages"][0]["content"])
        if n == 7:
            return {"type": "errored", "error": {"type": "error", "error": {"type": "invalid_request_error"}}}
        intentos[custom_id] = intentos.get(custom_id, 0) + 1
        return {"type": "expired"} if intentos[custom_id] == 1 else None

    api = FakeAnthropicAPI(_doble, resultado_de=resultado_de, polls_hasta_terminar=0)
    cola = ColaDeLotes(tmp_path / "p.json", tipo="prueba")
    lote = ProveedorEnLote(_medido(api, store), cola)

    def ronda() -> list[Any]:
        salida: list[Any] = []
        for inicio in (1, 7):
            try:
                salida.append(_cadena(lote, inicio, 1))
            except PendienteDeLoteError:
                salida.append("pendiente")
            except ProviderError as exc:
                salida.append(f"error: {exc}")
        return salida

    assert ejecutar_en_lotes(ronda, cola, **SIN_ESPERA) == [2, "error: Claude (lote): invalid_request_error"]
    assert [len(c["requests"]) for c in api.lotes_creados()] == [2, 1]  # the expired one went out again


def test_an_exhausted_budget_stops_submitting_and_the_work_sees_the_budget_error(tmp_path, store) -> None:
    api = FakeAnthropicAPI(_doble, polls_hasta_terminar=0)
    real = _medido(api, store, presupuesto=Presupuesto(store=store, max_llamadas_por_pregunta=None))
    cola = ColaDeLotes(tmp_path / "p.json", tipo="prueba")
    lote = ProveedorEnLote(real, cola)

    def ronda() -> str:
        try:
            return str(_cadena(lote, 1, 3))
        except PresupuestoAgotadoError as exc:
            return f"presupuesto: {exc.limite}"
        except PendienteDeLoteError:
            return "pendiente"

    class Agotado:
        def __init__(self) -> None:
            self.comprobaciones = 0

        def comprobar_presupuesto(self) -> None:
            self.comprobaciones += 1
            if self.comprobaciones > 1:
                raise PresupuestoAgotadoError("agotado", limite="diario")

        def registrar_externo(self, usage: Any) -> Any:
            return real.registrar_externo(usage)

    assert ejecutar_en_lotes(ronda, cola, contable=Agotado(), **SIN_ESPERA) == "presupuesto: diario"
    assert len(api.lotes_creados()) == 1


def test_escalation_follows_the_routing_in_batch_mode_too(tmp_path, store, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    api = FakeAnthropicAPI(lambda p: "no es json" if p["model"] == "claude-haiku-5-5" else '{"n": 1}')
    base = AnthropicProvider(model="claude-opus-5-5", client=api.client())
    real = MeteredProvider(base, store, purpose="hechos")
    cola = ColaDeLotes(tmp_path / "p.json", tipo="prueba")
    lote = ProveedorEnLote(real, cola)
    assert ejecutar_en_lotes(lambda: _cadena(lote, 1, 1), cola, contable=real, **SIN_ESPERA) == 1
    modelos = [c["requests"][0]["params"]["model"] for c in api.lotes_creados()]
    assert modelos == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert [(f["model"], f["escalated"], f["batch"]) for f in _filas(store)] == [
        ("claude-haiku-5-5", 0, 1),
        ("claude-sonnet-5-5", 1, 1),
    ]


# --------------------------------------------------------------------------- #
# The recodificación by batches
# --------------------------------------------------------------------------- #


def _modelo_de_la_ley(params: dict[str, Any]) -> str:
    """Answers like the scripted reader and reviewer of the recodificación tests."""
    esquema = params["output_config"]["format"]["schema"]
    return json.dumps(lectura() if esquema == prompts.ESQUEMA_LECTOR else revision(), ensure_ascii=False)


def test_the_recodification_by_batches_gives_the_synchronous_proposal_at_batch_prices(datos, tmp_path, store) -> None:
    _, sincrono = ejecutar(
        [_objetivo(datos)],
        lector=Guion(lectura()),
        revisor=Guion(revision()),
        texto_de=texto_de_ley(),
        almacen=AlmacenPropuestas(tmp_path / "sincrono"),
        data_dir=datos,
        hoy=HOY,
    )

    api = FakeAnthropicAPI(_modelo_de_la_ley, polls_hasta_terminar=1)
    lector = _medido(api, store)
    revisor = lector.con_proposito("recodificacion-revisor")
    almacen = AlmacenPropuestas(tmp_path / "lotes")
    ejecutor = recod_lotes.EjecutorPorLotes(tmp_path / "estado.json", esperar=lambda s: None, intervalo=0.0)
    informe, nuevas = ejecutor(
        [_objetivo(datos)],
        lector=lector,
        revisor=revisor,
        texto_de=texto_de_ley(),
        almacen=almacen,
        data_dir=datos,
        hoy=HOY,
    )

    (propuesta,) = nuevas
    assert (
        (propuesta.estado, propuesta.resultado)
        == (sincrono[0].estado, sincrono[0].resultado)
        == (
            "propuesta",
            "recodificada",
        )
    )
    assert propuesta.despues == sincrono[0].despues and propuesta.despues["porcentaje"] == NUEVA["porcentaje"]
    assert propuesta.control is not None and propuesta.control.ok  # the same deterministic gate ran
    assert [p.id for p in almacen.listar("propuesta")] == [propuesta.id] and informe.errores == []
    # Reader round, then reviewer round; nothing synchronous; the state file is gone once done.
    assert [len(c["requests"]) for c in api.lotes_creados()] == [1, 1] and api.cuerpos() == []
    assert not (tmp_path / "estado.json").exists() and ejecutor.resumen["rondas"] == 2
    filas = _filas(store)
    assert [(f["purpose"], f["batch"]) for f in filas] == [("recodificacion", 1), ("recodificacion-revisor", 1)]

    # Run again: the proposal for this text exists, nothing is sent.
    informe, nuevas = ejecutor(
        [_objetivo(datos)], lector=lector, revisor=revisor, texto_de=texto_de_ley(), almacen=almacen, data_dir=datos
    )
    assert nuevas == [] and informe.ya_tratados and len(api.lotes_creados()) == 2


def test_the_evaluation_by_batches_gives_the_synchronous_result(datos, tmp_path, store) -> None:
    objetivos = [_objetivo(datos)]
    sincrona = evaluar(
        objetivos, lector=Guion(lectura()), revisor=Guion(revision()), texto_de=texto_de_ley(), data_dir=datos
    )
    api = FakeAnthropicAPI(_modelo_de_la_ley, polls_hasta_terminar=0)
    lector = _medido(api, store)
    evaluador = recod_lotes.EvaluadorPorLotes(tmp_path / "eval.json", esperar=lambda s: None, intervalo=0.0)
    por_lotes = evaluador(
        objetivos,
        lector=lector,
        revisor=lector.con_proposito("recodificacion-revisor"),
        texto_de=texto_de_ley(),
        data_dir=datos,
    )
    assert por_lotes.model_dump() == sincrona.model_dump() and por_lotes.convergieron == 1
    assert len(api.lotes_creados()) == 2


def test_a_provider_without_batches_runs_the_recodification_synchronously(datos, tmp_path) -> None:
    ejecutor = recod_lotes.EjecutorPorLotes(tmp_path / "estado.json")
    _, nuevas = ejecutor(
        [_objetivo(datos)],
        lector=Guion(lectura()),
        revisor=Guion(revision()),
        texto_de=texto_de_ley(),
        almacen=AlmacenPropuestas(tmp_path / "p"),
        data_dir=datos,
    )
    assert nuevas[0].estado == "propuesta" and not (tmp_path / "estado.json").exists()


def test_the_executor_is_chosen_by_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "RECODE_BATCH", False)
    sincrono = recod_lotes.ejecutor_de_recodificacion("autopilot")
    assert sincrono.func is ejecutar and sincrono.keywords == {"recodificador": recod_lotes.recodificar_con_escalado}
    assert recod_lotes.evaluador_de_recodificacion() is evaluar
    monkeypatch.setattr(settings, "RECODE_BATCH", True)
    monkeypatch.setattr(settings, "LLM_BATCH_STATE_PATH", str(tmp_path))
    por_lotes = recod_lotes.ejecutor_de_recodificacion("autopilot")
    assert isinstance(por_lotes, recod_lotes.EjecutorPorLotes) and por_lotes.estado_path == tmp_path / "autopilot.json"
    assert isinstance(recod_lotes.evaluador_de_recodificacion(), recod_lotes.EvaluadorPorLotes)


# --------------------------------------------------------------------------- #
# Escalation of a reader that fails the deterministic gate
# --------------------------------------------------------------------------- #


class GuionEscalable(Guion):
    def __init__(self, *respuestas: Any, superior: Guion | None = None, model: str = "pequeno") -> None:
        super().__init__(*respuestas, model=model)
        self.superior = superior

    def escalado(self) -> Guion | None:
        return self.superior


def test_a_reader_that_fails_the_gate_is_replaced_once_by_the_next_tier(datos) -> None:
    mala = lectura({**NUEVA, "maximo": "700"})  # 700 is in no quote of the law: the gate rejects it
    superior = Guion(lectura(), model="mediano")
    lector = GuionEscalable(mala, mala, mala, superior=superior)
    propuesta = recod_lotes.recodificar_con_escalado(
        _objetivo(datos), lector=lector, revisor=Guion(revision()), texto_de=texto_de_ley(), max_iter=3
    )
    assert propuesta.estado == "propuesta" and propuesta.modelo_lector == "guion/mediano"
    assert propuesta.llamadas_llm == 3 + 2  # three gated passes, then the reader and the reviewer of the second run

    # Nothing above (no routing): exactly recodificar.
    sola = recod_lotes.recodificar_con_escalado(
        _objetivo(datos), lector=GuionEscalable(mala, mala, mala), revisor=Guion(), texto_de=texto_de_ley()
    )
    assert sola.estado == "sin_converger" and recod_lotes.fallo_el_control(sola)

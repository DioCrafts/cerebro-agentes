"""Shared fixtures of the proactive pass's tests: a private cases database, a fixed «today», scripted providers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from decimal import Decimal
import json
import re
from typing import Any

import pytest

from asistente.nucleo.agent.providers.base import EstadoLote, ProviderError, ResultadoLote, Usage
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.almacen import olvidar_almacenes
from asistente.nucleo.modulos.acciones import AccionPendiente, AccionStore
from asistente.nucleo.proactive.store import Notification, NotificationStore
from asistente.plataforma import settings
from tests.proactive import make_case

HOY = date(2026, 11, 18)  # a Wednesday (ISO week 47)
LUNES = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def entorno(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No accounts, a fixed «today» for the decisions, the pass's settings at their defaults, no push."""
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(reloj, "hoy", lambda: HOY)
    monkeypatch.setattr(settings, "REPASO_ACTIVO", False)
    monkeypatch.setattr(settings, "REPASO_FRECUENCIA", "semanal")
    monkeypatch.setattr(settings, "REPASO_DIA", 0)
    monkeypatch.setattr(settings, "REPASO_HORA", "07:00")
    monkeypatch.setattr(settings, "REPASO_POR_LOTES", True)
    monkeypatch.setattr(settings, "REPASO_MAX_PUNTOS", 7)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    yield
    olvidar_almacenes()


@pytest.fixture
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    path = str(tmp_path / "estado.sqlite3")
    SQLiteCaseStateStore(path).save_case(make_case("c1"))
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", path)
    return path


@pytest.fixture
def store(db: str) -> NotificationStore:
    return NotificationStore(db)


@pytest.fixture
def acciones(db: str) -> AccionStore:
    return AccionStore(db)


def aviso(
    store: NotificationStore, key: str, *, kind: str = "modulo:luz", case_id: str = "c1", **kw: Any
) -> Notification:
    datos: dict[str, Any] = {"title": key, "body": "Texto del aviso.", "severity": "info"}
    datos.update(kw)
    nota = Notification(case_id=case_id, kind=kind, key=key, **datos)
    assert store.add(nota)
    return nota


def accion(acciones: AccionStore, *, case_id: str = "c1", **kw: Any) -> AccionPendiente:
    datos: dict[str, Any] = {
        "modulo": "luz",
        "herramienta": "luz_solicitar_cambio",
        "resumen": "Luz — Solicitar el cambio de comercializadora. Datos: oferta: Tarifa Plana.",
    }
    datos.update(kw)
    return acciones.add(AccionPendiente(case_id=case_id, **datos))


def creado(dia: date) -> datetime:
    return datetime.combine(dia, time(9), tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Model outputs
# --------------------------------------------------------------------------- #

_DATOS = re.compile(r"<datos_repaso>\n(.*?)\n</datos_repaso>", re.DOTALL)


def puntos_de(user: str) -> list[dict[str, Any]]:
    """The points the model was given (parsed from the data block of its user message)."""
    m = _DATOS.search(user)
    assert m is not None, user
    return list(json.loads(m.group(1))["puntos"])


def salida_valida(
    puntos: list[dict[str, Any]], *, titular: str = "Esta semana hay cosas que conviene mirar con calma."
) -> dict[str, Any]:
    """A model answer that respects every rule (no figures, every point in order, the first proposal)."""
    return {
        "titular": titular,
        "puntos": [
            {
                "punto_id": p["punto_id"],
                "texto": "Conviene que lo mires pronto y decidas qué hacer.",
                "por_que": "Lo indica su módulo y tiene que ver contigo.",
                "propuesta_id": (p.get("propuestas") or [{}])[0].get("id"),
            }
            for p in puntos
        ],
    }


class Guionizado:
    """A provider whose ``complete_json`` answers with a function of the request (or a fixed list)."""

    name = "guion"
    model = "guion-1"

    def __init__(self, *respuestas: Any) -> None:
        self.respuestas = list(respuestas)
        self.peticiones: list[dict[str, Any]] = []

    def start(self, **_: Any) -> Any:  # pragma: no cover - never a conversation here
        raise AssertionError("the pass only makes single-shot calls")

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, **_: Any) -> Any:
        self.peticiones.append({"system": system, "user": user, "schema": schema, "schema_name": schema_name})
        if not self.respuestas:
            raise AssertionError("unexpected model call")
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta(user) if callable(respuesta) else respuesta


def valida(user: str) -> dict[str, Any]:
    return salida_valida(puntos_de(user))


class Lotes(Guionizado):
    """A batch-capable provider (``ProveedorDeLotes``) with metering hooks, all in memory."""

    def __init__(self, *respuestas: Any, terminado: bool = True) -> None:
        super().__init__(*respuestas)
        self.lotes: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.terminado = terminado
        self.usos: list[Usage] = []
        self.falla_crear: Exception | None = None
        self.errores: set[str] = set()
        self.estados_pedidos = 0

    def peticion_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        return {"system": system, "user": user, "schema_name": schema_name}

    def crear_lote(self, peticiones: Any) -> str:
        if self.falla_crear is not None:
            raise self.falla_crear
        lote_id = f"lote_{len(self.lotes) + 1}"
        self.lotes[lote_id] = list(peticiones)
        return lote_id

    def estado_lote(self, lote_id: str) -> EstadoLote:
        self.estados_pedidos += 1
        return EstadoLote(id=lote_id, terminado=self.terminado, en_proceso=0 if self.terminado else 1)

    def resultados_lote(self, lote_id: str) -> list[ResultadoLote]:
        salida = []
        for custom_id, params in self.lotes[lote_id]:
            uso = Usage(provider=self.name, model=self.model, input_tokens=100, output_tokens=50)
            if custom_id in self.errores:
                salida.append(
                    ResultadoLote(custom_id=custom_id, error="Claude (lote): petición expired", reintentable=True)
                )
            else:
                salida.append(ResultadoLote(custom_id=custom_id, datos=valida(params["user"]), usage=uso))
        return salida

    def registrar_externo(self, usage: Usage) -> Decimal | None:
        self.usos.append(usage)
        return Decimal("0.001")

    def comprobar_presupuesto(self) -> None:
        return None


__all__ = [
    "HOY",
    "LUNES",
    "Guionizado",
    "Lotes",
    "ProviderError",
    "accion",
    "aviso",
    "creado",
    "puntos_de",
    "salida_valida",
    "valida",
]

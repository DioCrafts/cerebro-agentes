"""Shared fixtures of the conversation-memory tests: a fixed clock, a private database, fake providers."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from asistente.nucleo.agent.providers.base import EstadoLote, ResultadoLote, Usage
from asistente.nucleo.cerebro.contrato import TurnoConversacion
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.recuerdos import almacen as almacen_mod
from asistente.nucleo.recuerdos.almacen import Titular
from asistente.nucleo.recuerdos.servicio import ServicioMemoria, olvidar_servicios
from asistente.plataforma import settings

# The accounts API fixtures (``env``: AUTH_MODE=required over one private database file).
from tests.auth.conftest import env, fast_scrypt  # noqa: F401

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


class Reloj:
    """The memory's «now», movable by the test."""

    def __init__(self) -> None:
        self.momento = T0

    def __call__(self) -> datetime:
        return self.momento

    def avanzar(self, **delta: float) -> datetime:
        self.momento = self.momento + timedelta(**delta)
        return self.momento


@pytest.fixture(autouse=True)
def reloj(monkeypatch: pytest.MonkeyPatch) -> Iterator[Reloj]:
    fijo = Reloj()
    monkeypatch.setattr(almacen_mod, "ahora", fijo)
    monkeypatch.setattr(settings, "MEMORIA_CONVERSACIONES", True)
    monkeypatch.setattr(settings, "MEMORIA_RESUMEN_TRAS_MINUTOS", 30)
    monkeypatch.setattr(settings, "MEMORIA_RETENCION_MENSAJES_DIAS", 30)
    monkeypatch.setattr(settings, "MEMORIA_MAX_RECUERDOS", 200)
    monkeypatch.setattr(settings, "MEMORIA_RESUMEN_LOTES", True)
    monkeypatch.setattr(settings, "MEMORIA_RESUMEN_EN_SEGUNDO_PLANO", False)
    monkeypatch.setattr(settings, "MEMORIA_EMBEDDINGS", False)
    yield fijo
    olvidar_servicios()


@pytest.fixture
def db(tmp_path) -> str:
    """A private cases database (with the cases table, as in production)."""
    path = str(tmp_path / "estado.sqlite3")
    SQLiteCaseStateStore(path).close()
    return path


@pytest.fixture
def memoria(db: str) -> ServicioMemoria:
    return ServicioMemoria(db)


ANA = Titular("caso-ana", "ana")
BEA = Titular("caso-bea", "bea")


def turno(
    memoria: ServicioMemoria,
    pregunta: str,
    respuesta: str = "De acuerdo.",
    *,
    titular: Titular = ANA,
    conversacion: str = "conv-ana-0001",
    fecha: datetime | None = None,
    usados: Sequence[str] = (),
) -> None:
    memoria.registrar_turno(
        TurnoConversacion(
            conversacion_id=conversacion,
            case_id=titular.case_id or None,
            user_id=titular.user_id or None,
            fecha=fecha or almacen_mod.ahora(),
            pregunta=pregunta,
            respuesta=respuesta,
            recuerdos_usados=list(usados),
        )
    )


def salida(*recuerdos: dict[str, str], titulo: str = "Conversación", resumen: str = "Hablamos de cosas.") -> dict:
    return {"titulo": titulo, "resumen": resumen, "recuerdos": list(recuerdos)}


def candidato(tipo: str, clave: str, texto: str, cita: str) -> dict[str, str]:
    return {"tipo": tipo, "clave": clave, "texto": texto, "cita": cita}


class ProveedorFalso:
    """``complete_json`` answers from a script (one dict per call, or an exception), recording the requests."""

    name = "falso"
    model = "falso-1"

    def __init__(self, respuestas: Sequence[dict[str, Any] | Exception] = ()) -> None:
        self.respuestas = list(respuestas)
        self.peticiones: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str, attachments=None) -> dict:
        self.peticiones.append({"system": system, "user": user, "schema_name": schema_name})
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


@dataclass
class ProveedorDeLotesFalso:
    """A provider with a batch API (``ProveedorDeLotes``): results are set by the test per custom_id."""

    name: str = "lotes"
    model: str = "lotes-1"
    lotes: dict[str, list[tuple[str, dict[str, Any]]]] = field(default_factory=dict)
    terminados: set[str] = field(default_factory=set)
    respuestas: dict[str, dict[str, Any] | str] = field(default_factory=dict)
    usos_externos: list[Usage] = field(default_factory=list)
    sincronas: int = 0

    def peticion_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        return {"system": system, "user": user, "schema_name": schema_name}

    def crear_lote(self, peticiones: Sequence[tuple[str, dict[str, Any]]]) -> str:
        lote_id = f"lote-{len(self.lotes) + 1}"
        self.lotes[lote_id] = list(peticiones)
        return lote_id

    def estado_lote(self, lote_id: str) -> EstadoLote:
        return EstadoLote(id=lote_id, terminado=lote_id in self.terminados)

    def resultados_lote(self, lote_id: str) -> list[ResultadoLote]:
        salida_: list[ResultadoLote] = []
        for custom_id, _params in self.lotes[lote_id]:
            respuesta = self.respuestas.get(custom_id)
            uso = Usage(provider=self.name, model=self.model, input_tokens=100, output_tokens=20)
            if isinstance(respuesta, dict):
                salida_.append(ResultadoLote(custom_id=custom_id, datos=respuesta, usage=uso))
            else:
                salida_.append(ResultadoLote(custom_id=custom_id, error=respuesta or "fallo", reintentable=True))
        return salida_

    def complete_json(self, **_: Any) -> dict[str, Any]:
        self.sincronas += 1
        raise AssertionError("batch mode must not call the model synchronously")

    def registrar_externo(self, usage: Usage) -> None:
        self.usos_externos.append(usage)

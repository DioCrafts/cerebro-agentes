"""Doubles for the collaboration runtime's tests: thread-safe scripted providers, fake specialists, a turn."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
import threading
from typing import Any

from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.agent.providers.base import ModelTurn, ToolCall, ToolResult
from asistente.nucleo.cerebro.contrato import (
    Cifra,
    ContextoEncargo,
    ContextoTurno,
    ExpedienteCompartido,
    InformeEspecialista,
)
from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType

HOY = date(2026, 11, 18)

Turno = ModelTurn | Callable[["ConversacionGuion"], ModelTurn]


def texto(t: str) -> ModelTurn:
    return ModelTurn(text=t, tool_calls=[], stop_reason="end", usage={"input_tokens": 10, "output_tokens": 5})


def herramientas(*llamadas: tuple[str, dict[str, Any]]) -> ModelTurn:
    return ModelTurn(
        text="",
        tool_calls=[ToolCall(id=f"call_{i}", name=n, arguments=a) for i, (n, a) in enumerate(llamadas)],
        stop_reason="tool_use",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


class ConversacionGuion:
    def __init__(self, proveedor: ProveedorGuion, turnos: list[Turno], arranque: dict[str, Any]) -> None:
        self.proveedor = proveedor
        self.turnos = list(turnos)
        self.arranque = arranque
        self.resultados: list[ToolResult] = []

    def step(self, *, allow_tools: bool = True, on_text: Any = None) -> ModelTurn:
        siguiente = self.turnos.pop(0) if self.turnos else texto("")
        return siguiente(self) if callable(siguiente) else siguiente

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.resultados.extend(results)
        self.arranque["resultados"] = self.resultados

    def add_user_message(self, text: str) -> None:
        pass


@dataclass
class _Estado:
    guiones: dict[str, list[list[Turno]]]
    arranques: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ProveedorGuion:
    """Each ``start`` for a purpose takes that purpose's next script; safe across threads (one script per run)."""

    name = "guion"
    model = "guion-1"

    def __init__(
        self, guiones: dict[str, list[list[Turno]]] | None = None, *, proposito: str = "base", _estado: Any = None
    ):
        self._estado: _Estado = _estado or _Estado(guiones=dict(guiones or {}))
        self.proposito = proposito

    def con_proposito(self, proposito: str) -> ProveedorGuion:
        return type(self)(proposito=proposito, _estado=self._estado)

    @property
    def arranques(self) -> list[dict[str, Any]]:
        return self._estado.arranques

    def de(self, proposito: str) -> list[dict[str, Any]]:
        return [a for a in self._estado.arranques if a["proposito"] == proposito]

    def start(self, *, system: str, tools: Any, history: Any, user_message: str) -> ConversacionGuion:
        with self._estado.lock:
            guiones = self._estado.guiones.get(self.proposito) or [[texto("")]]
            guion = guiones.pop(0) if len(guiones) > 1 else list(guiones[0])
            arranque = {
                "proposito": self.proposito,
                "system": system,
                "tools": tools,
                "user_message": user_message,
                "hilo": threading.get_ident(),
            }
            self._estado.arranques.append(arranque)
        return ConversacionGuion(self, guion, arranque)

    def complete_json(self, **_kwargs: Any) -> dict[str, Any]:  # pragma: no cover - not used here
        raise AssertionError("complete_json no se usa en estas pruebas")


class Eventos:
    """Collects ``emit(tipo, **datos)`` calls (thread-safe)."""

    def __init__(self, al_recibir: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.lista: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()
        self._al_recibir = al_recibir

    def __call__(self, tipo: str, **datos: Any) -> None:
        with self._lock:
            self.lista.append((tipo, datos))
        if self._al_recibir is not None:
            self._al_recibir(tipo, datos)

    def de(self, tipo: str) -> list[dict[str, Any]]:
        with self._lock:
            return [d for t, d in self.lista if t == tipo]


def caso(**hechos: Any) -> CaseState:
    ahora = datetime.now(UTC)
    return CaseState(
        case_id="case-hogar",
        user_id="u",
        jurisdiction="ES",
        tax_period="2026",
        created_at=ahora,
        updated_at=ahora,
        facts=[
            Fact(
                fact_id=f"fact_{k}",
                name=k,
                value=v,
                value_type=FactValueType.NUMBER if isinstance(v, float) else FactValueType.STRING,
                source="t",
                confidence=1.0,
            )
            for k, v in hechos.items()
        ],
    )


def turno(
    proveedor: Any = None,
    *,
    case: CaseState | None = None,
    emit: Any = None,
    mensajes: Sequence[str] = (),
    ledger: EvidenceLedger | None = None,
) -> ContextoTurno:
    proveedor = proveedor or ProveedorGuion()
    return ContextoTurno(
        expediente=ExpedienteCompartido(case),
        ledger=ledger or EvidenceLedger(),
        hoy=HOY,
        emit=emit or Eventos(),
        proveedor_para=proveedor.con_proposito,
        mensajes_usuario=mensajes,
    )


def cifra(
    id_: str,
    especialista: str,
    concepto: str,
    valor: str,
    unidad: str | None = "EUR",
    fragment: str | None = None,
    literal: str = "",
) -> Cifra:
    return Cifra.model_validate(
        {
            "id": id_,
            "especialista": especialista,
            "concepto": concepto,
            "valor": Decimal(valor),
            "unidad": unidad,
            "fragment_id": fragment,
            "literal": literal,
        }
    )


Accion = Callable[[ContextoEncargo], "InformeEspecialista | str | None"]


class EspecialistaFalso:
    """A specialist whose behaviour is a function of its ``ContextoEncargo`` (records every call)."""

    def __init__(
        self,
        clave: str,
        nombre: str | None = None,
        *,
        accion: Accion | None = None,
        cifras: Sequence[Cifra] = (),
        descripcion: str = "",
    ) -> None:
        self.clave = clave
        self.nombre = nombre or clave.capitalize()
        self.descripcion = descripcion or f"Especialista en {clave}."
        self.accion = accion
        self.cifras = list(cifras)
        self.contextos: list[ContextoEncargo] = []
        self._lock = threading.Lock()

    @property
    def llamadas(self) -> int:
        return len(self.contextos)

    def atender(self, contexto: ContextoEncargo) -> InformeEspecialista:
        with self._lock:
            self.contextos.append(contexto)
        resultado = self.accion(contexto) if self.accion is not None else f"Informe de {self.nombre}."
        if isinstance(resultado, InformeEspecialista):
            return resultado
        return InformeEspecialista(
            encargo_id=contexto.encargo.id,
            especialista=self.clave,
            nombre=self.nombre,
            estado="ok" if resultado else "sin_respuesta",
            texto=resultado or "",
            cifras=list(self.cifras),
        )

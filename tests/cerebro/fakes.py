"""Doubles for the brain's tests: scripted providers per purpose, fake household specialists and a fake memory."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
import threading
import time
from typing import Any

from asistente.ambitos.fiscal import MODULO as FISCAL
from asistente.nucleo.agent.agent import TaxAgent
from asistente.nucleo.agent.verifier import Verifier
from asistente.nucleo.cerebro.cerebro import Cerebro
from asistente.nucleo.cerebro.contrato import (
    BloqueMemoria,
    Cifra,
    ContextoEncargo,
    InformeEspecialista,
    OrigenRecuerdo,
    RecuerdoMemoria,
    TurnoConversacion,
)
from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType
from asistente.nucleo.modulos.contrato import ModuloBase
from tests.agent.fakes import FakeSearcher, ScriptedConversation, ScriptedProvider, corpus


class _Conversacion(ScriptedConversation):
    """A scripted conversation whose next turn may be a callable, evaluated when it is due (to cite ids minted by
    the run itself, whatever the order in which parallel specialists registered their fragments)."""

    def step(self, *, allow_tools: bool = True, on_text: Any = None) -> Any:
        turnos = self._provider.turns
        if turnos and callable(turnos[0]):
            turnos[0] = turnos[0]()
        return super().step(allow_tools=allow_tools, on_text=on_text)


class Grabador(ScriptedProvider):
    """A scripted provider that keeps every conversation start (system, tools, message)."""

    def __init__(
        self, turns: list[Any] | None = None, json_responses: list[Any] | None = None, *, nombre: str = ""
    ) -> None:
        super().__init__(list(turns or []), json_responses)
        self.inicios: list[dict[str, Any]] = []
        self.nombre = nombre

    def start(self, **kwargs: Any):  # type: ignore[override]
        self.inicios.append(kwargs)
        super().start(**kwargs)
        return _Conversacion(self)

    @property
    def llamadas(self) -> int:
        """Model calls made: conversation steps plus JSON completions."""
        return len(self.allow_tools_log) + len(self.json_requests)


def modulo(clave: str, nombre: str, descripcion: str) -> ModuloBase:
    return ModuloBase(clave=clave, nombre=nombre, descripcion=descripcion, instrucciones=f"Especialista en {nombre}.")


HIPOTECA = modulo(
    "hipoteca",
    "Hipoteca",
    "Hipoteca del hogar: cuadro de amortización, revisión con el Euríbor, amortizar anticipadamente.",
)
LUZ = modulo("luz", "Luz", "Factura de la luz del hogar: tarifas, potencia y horas baratas.")
SUSCRIPCIONES = modulo("suscripciones", "Suscripciones y recibos", "Cargos recurrentes y bajas de servicios.")


class EspecialistaFalso:
    """A household specialist that registers ONE calculation in the turn's ledger and reports its figures."""

    def __init__(
        self,
        clave: str,
        nombre: str,
        *,
        calculo: str,
        cifras: Sequence[tuple[str, str, str | None]] = (),
        texto: str = "",
        estado: str = "ok",
        espera: threading.Barrier | None = None,
        retraso: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.clave = clave
        self.nombre = nombre
        self.descripcion = f"Especialista en {nombre}."
        self._calculo = calculo
        self._cifras = list(cifras)
        self._texto = texto
        self._estado = estado
        self._espera = espera
        self._retraso = retraso
        self._error = error
        self.contextos: list[ContextoEncargo] = []
        self.hilos: list[str] = []
        self.momentos: list[tuple[float, float]] = []

    def atender(self, contexto: ContextoEncargo) -> InformeEspecialista:
        inicio = time.monotonic()
        self.contextos.append(contexto)
        self.hilos.append(threading.current_thread().name)
        if self._espera is not None:
            self._espera.wait()
        if self._retraso:
            time.sleep(self._retraso)
        if self._error is not None:
            raise self._error
        if self._estado != "ok":
            self.momentos.append((inicio, time.monotonic()))
            return InformeEspecialista(
                encargo_id=contexto.encargo.id,
                especialista=self.clave,
                nombre=self.nombre,
                estado=self._estado,  # type: ignore[arg-type]
                avisos=["Límite de uso alcanzado."] if self._estado == "presupuesto" else [],
            )
        fragmento = contexto.turno.ledger.register(
            text=self._calculo,
            metadata={
                "title": f"{self.nombre} · {self.clave}_calcular",
                "source": f"modulo://{self.clave}/{self.clave}_calcular/1",
                "source_authority": "CALCULADORA",
                "section": "Cálculo del módulo",
            },
        )
        cifras = [
            Cifra(
                id=f"C{i}",
                especialista=self.clave,
                concepto=concepto,
                valor=Decimal(valor),
                unidad=unidad,  # type: ignore[arg-type]
                fragment_id=fragmento.fragment_id,
                herramienta=f"{self.clave}_calcular",
                literal="",
            )
            for i, (concepto, valor, unidad) in enumerate(self._cifras, start=1)
        ]
        self.momentos.append((inicio, time.monotonic()))
        return InformeEspecialista(
            encargo_id=contexto.encargo.id,
            especialista=self.clave,
            nombre=self.nombre,
            estado="ok",
            texto=(self._texto or self._calculo) + f" [{fragmento.fragment_id}]",
            cifras=cifras,
            fragment_ids=[fragmento.fragment_id],
        )


class MemoriaFalsa:
    """A conversation memory that returns fixed items and records the turns it is handed."""

    def __init__(self, recuerdos: Sequence[str] = (), *, falla: bool = False) -> None:
        self._recuerdos = list(recuerdos)
        self._falla = falla
        self.consultas: list[dict[str, Any]] = []
        self.turnos: list[TurnoConversacion] = []

    def recuperar(
        self,
        *,
        case_id: str | None,
        user_id: str | None,
        consulta: str,
        conversacion_id: str | None = None,
        limite: int = 8,
    ) -> BloqueMemoria:
        self.consultas.append({"case_id": case_id, "consulta": consulta, "conversacion_id": conversacion_id})
        if self._falla:
            raise RuntimeError("memoria rota")
        return BloqueMemoria(
            recuerdos=[
                RecuerdoMemoria(
                    id=f"R{i}",
                    tipo="dato",
                    texto=texto,
                    origen=OrigenRecuerdo(conversacion_id="conv-anterior-1", fecha=date(2026, 10, 1), cita=texto),
                )
                for i, texto in enumerate(self._recuerdos, start=1)
            ]
        )

    def registrar_turno(self, turno: TurnoConversacion) -> None:
        self.turnos.append(turno)


def caso(**hechos: Any) -> CaseState:
    ahora = datetime.now(UTC)
    return CaseState(
        case_id="case-cerebro",
        user_id="u-cerebro",
        jurisdiction="ES",
        tax_period="2026",
        created_at=ahora,
        updated_at=ahora,
        facts=[
            Fact(
                fact_id=f"fact_{k}",
                name=k,
                value=v,
                value_type=FactValueType.NUMBER if isinstance(v, int | float) else FactValueType.STRING,
                source="t",
                confidence=1.0,
            )
            for k, v in hechos.items()
        ],
    )


def cerebro(
    *,
    fiscal: ScriptedProvider | None = None,
    sintesis: ScriptedProvider | None = None,
    intencion: ScriptedProvider | None = None,
    verificador: ScriptedProvider | None = None,
    especialistas: dict[str, Any] | None = None,
    memoria: Any = None,
    **ajustes: Any,
) -> Cerebro:
    """A brain whose every purpose has its own scripted provider (no network, no FAISS)."""
    fiscal = fiscal or Grabador(nombre="fiscal")
    sintesis = sintesis or Grabador(nombre="sintesis")
    intencion = intencion or Grabador(nombre="intencion")
    verificador = verificador or Grabador(nombre="verificador")
    asesor = TaxAgent(provider=fiscal, searcher=FakeSearcher(corpus()))
    hogar = dict(especialistas or {})
    return Cerebro(
        asesor=asesor,
        proveedor=sintesis,
        verificador=Verifier(verificador),
        memoria=memoria,
        fabrica=lambda modulos: {m.clave: hogar[m.clave] for m in modulos if m.clave in hogar},
        proveedores={"cerebro": sintesis, "cerebro:intencion": intencion},
        hoy=lambda: date(2026, 11, 18),
        **ajustes,
    )


def modulos(*extra: ModuloBase) -> list[Any]:
    return [FISCAL, *extra]


def eventos() -> tuple[list[dict[str, Any]], Any]:
    recibidos: list[dict[str, Any]] = []
    return recibidos, recibidos.append

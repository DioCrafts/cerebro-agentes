"""Batch mode for non-urgent ``complete_json`` work: submit, poll, collect, replay.

A provider's batch API (Claude's Message Batches) runs requests asynchronously
at a discount, with results within hours. Code written as a sequence of
synchronous calls (the recodificación loop: reader → deterministic gate →
reviewer → …) is run in batch mode by REPLAY, without rewriting it:

1. A *round* runs the (side-effect-free) work with :class:`ProveedorEnLote` in
   place of the real providers. A call whose answer is already known returns it
   at once; the first unknown call of each unit of work queues its request and
   raises :class:`PendienteDeLoteError`, which stops that unit for this round.
2. The queued requests of the round go out as one batch per model; the driver
   polls until it ends, stores every answer (by request hash) and records the
   usage, at batch prices, through the metering wrapper.
3. The next round replays the work: known answers come back instantly, so each
   unit advances to its next call. When a round queues nothing, every unit ran
   to the end — with exactly the answers, and the same deterministic gates, a
   synchronous run with those answers would have had.

The state (answers, queued requests, batches in flight) is a JSON file written
atomically after every change, so a run that stops — a crash, or a batch still
running after ``LLM_BATCH_MAX_WAIT_HOURS`` — resumes where it was without paying
twice. It holds request texts: only use batch mode for work on public texts (the
law), never for a user's data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.agent import enrutado
from asistente.nucleo.agent.providers.base import (
    Attachment,
    EstadoLote,
    ProveedorDeLotes,
    ProviderError,
    ResultadoLote,
    Usage,
)

logger = logging.getLogger(__name__)

# Answers older than this are not reused by a new run (a weekly job starts afresh).
MAX_ANTIGUEDAD = timedelta(days=6)
# A request whose batch result was a transient failure is sent again at most this many times.
MAX_REINTENTOS = 2


class PendienteDeLoteError(ProviderError):
    """The answer to this call will come from a batch: the unit of work stops for this round."""

    def __init__(self, clave: str) -> None:
        super().__init__(f"Respuesta pendiente de un lote ({clave[:12]}…).", retryable=True)
        self.clave = clave


class LoteSinTerminarError(RuntimeError):
    """A batch did not end within the wait allowed. The state is kept: run again to resume it."""


# --------------------------------------------------------------------------- #
# Persistent state
# --------------------------------------------------------------------------- #


class _Pedido(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proveedor: str
    modelo: str
    proposito: str = ""
    nivel: str = ""
    escalado: bool = False
    params: dict[str, Any]
    intentos: int = 0


class _Respuesta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datos: dict[str, Any] | None = None
    error: str | None = None
    salida_invalida: bool = False


class _LoteEnviado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    proveedor: str
    modelo: str
    enviado_el: datetime
    # custom_id -> the request it carries (kept until its result is stored).
    pedidos: dict[str, _Pedido]


class _Historia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    modelo: str
    peticiones: int
    correctas: int = 0
    con_error: int = 0
    coste_usd: str | None = None
    enviado_el: datetime
    recogido_el: datetime | None = None


class _Estado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    tipo: str
    creado_el: datetime
    rondas: int = 0
    respuestas: dict[str, _Respuesta] = Field(default_factory=dict)
    reintentos: dict[str, int] = Field(default_factory=dict)
    en_curso: list[_LoteEnviado] = Field(default_factory=list)
    lotes: list[_Historia] = Field(default_factory=list)


class Contable(Protocol):
    """What the driver needs from the metering wrapper (:class:`~asistente.plataforma.ops.consumo.MeteredProvider`)."""

    def comprobar_presupuesto(self) -> None: ...

    def registrar_externo(self, usage: Usage) -> Decimal | None: ...


class ColaDeLotes:
    """The answers known so far, the requests queued in this round and the batches in flight."""

    def __init__(self, path: str | Path, *, tipo: str, ahora: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.tipo = tipo
        self._ahora = ahora or (lambda: datetime.now(UTC))
        self.pendientes: dict[str, _Pedido] = {}
        self._nativos: dict[str, ProveedorDeLotes] = {}
        # Set when no more batches may be sent: a missing answer then raises this.
        self.cierre: ProviderError | None = None
        self.estado = self._cargar()

    def _cargar(self) -> _Estado:
        if self.path.exists():
            estado = _Estado.model_validate_json(self.path.read_text(encoding="utf-8"))
            if estado.tipo != self.tipo:
                raise ValueError(f"{self.path} es el estado de lotes de «{estado.tipo}», no de «{self.tipo}».")
            if estado.en_curso or self._ahora() - estado.creado_el <= MAX_ANTIGUEDAD:
                return estado
            logger.info("Discarding batch state %s: older than %s and nothing in flight.", self.path, MAX_ANTIGUEDAD)
        return _Estado(tipo=self.tipo, creado_el=self._ahora())

    def guardar(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fichero:
                fichero.write(self.estado.model_dump_json(indent=1))
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def terminar(self) -> None:
        """The work is done: forget the answers (nothing in flight is lost)."""
        if not self.estado.en_curso:
            self.path.unlink(missing_ok=True)

    # --- used by ProveedorEnLote -------------------------------------------------

    def registrar_nativo(self, nativo: ProveedorDeLotes) -> None:
        self._nativos.setdefault(nativo.model, nativo)

    def respuesta(self, clave: str) -> _Respuesta | None:
        return self.estado.respuestas.get(clave)

    def pedir(self, clave: str, pedido: _Pedido) -> None:
        if clave not in self.estado.respuestas and not any(clave in lote.pedidos for lote in self.estado.en_curso):
            self.pendientes.setdefault(clave, pedido)

    # --- used by the driver ------------------------------------------------------

    def _nativo(self, modelo: str) -> ProveedorDeLotes:
        nativo = self._nativos.get(modelo) or next(iter(self._nativos.values()), None)
        if nativo is None:
            raise ProviderError("No hay ningún proveedor con lotes para consultar los lotes en curso.")
        return nativo

    def enviar(self) -> list[str]:
        """Submit the requests queued this round: one batch per model. Returns the batch ids."""
        por_modelo: dict[str, dict[str, _Pedido]] = {}
        for clave, pedido in sorted(self.pendientes.items()):
            por_modelo.setdefault(pedido.modelo, {})[clave] = pedido
        ids: list[str] = []
        for modelo, pedidos in por_modelo.items():
            nativo = self._nativos.get(modelo)
            if nativo is None:
                raise ProviderError(f"No hay proveedor con lotes para el modelo {modelo}.")
            lote_id = nativo.crear_lote([(clave, pedido.params) for clave, pedido in pedidos.items()])
            ahora = self._ahora()
            self.estado.en_curso.append(
                _LoteEnviado(id=lote_id, proveedor=nativo.name, modelo=modelo, enviado_el=ahora, pedidos=pedidos)
            )
            self.estado.lotes.append(_Historia(id=lote_id, modelo=modelo, peticiones=len(pedidos), enviado_el=ahora))
            self.guardar()
            ids.append(lote_id)
            logger.info("Submitted batch %s: %d request(s) to %s.", lote_id, len(pedidos), modelo)
        self.pendientes.clear()
        self.estado.rondas += 1
        self.guardar()
        return ids

    def recoger(
        self,
        *,
        contable: Contable | None,
        esperar: Callable[[float], None],
        intervalo: float,
        max_espera: timedelta,
    ) -> None:
        """Wait for every batch in flight, store its answers and record its usage."""
        while self.estado.en_curso:
            lote = self.estado.en_curso[0]
            nativo = self._nativo(lote.modelo)
            estado = self._esperar(nativo, lote, esperar=esperar, intervalo=intervalo, max_espera=max_espera)
            resultados = nativo.resultados_lote(lote.id)
            coste = self._guardar_resultados(lote, resultados, contable)
            historia = next((h for h in self.estado.lotes if h.id == lote.id), None)
            if historia is not None:
                historia.correctas, historia.con_error = estado.correctas, estado.con_error
                historia.coste_usd = None if coste is None else str(coste.normalize())
                historia.recogido_el = self._ahora()
            self.estado.en_curso.pop(0)
            self.guardar()

    def _esperar(
        self,
        nativo: ProveedorDeLotes,
        lote: _LoteEnviado,
        *,
        esperar: Callable[[float], None],
        intervalo: float,
        max_espera: timedelta,
    ) -> EstadoLote:
        while True:
            estado = nativo.estado_lote(lote.id)
            if estado.terminado:
                return estado
            if self._ahora() - lote.enviado_el >= max_espera:
                raise LoteSinTerminarError(
                    f"El lote {lote.id} ({lote.modelo}) sigue en proceso tras {max_espera}: "
                    f"{estado.en_proceso} petición(es) sin terminar. El estado se conserva en {self.path}; "
                    "vuelve a lanzar la misma orden para recogerlo."
                )
            esperar(intervalo)

    def _guardar_resultados(
        self, lote: _LoteEnviado, resultados: Sequence[ResultadoLote], contable: Contable | None
    ) -> Decimal | None:
        coste_total = Decimal(0)
        coste_conocido = True
        for resultado in resultados:
            pedido = lote.pedidos.get(resultado.custom_id)
            if pedido is None:
                logger.warning("Batch %s returned an unknown custom_id %s.", lote.id, resultado.custom_id)
                continue
            if resultado.usage is not None and contable is not None:
                usage = replace(
                    resultado.usage,
                    purpose=pedido.proposito,
                    tier=pedido.nivel,
                    escalated=pedido.escalado,
                    batch=True,
                )
                coste = contable.registrar_externo(usage)
                if coste is None:
                    coste_conocido = False
                else:
                    coste_total += coste
            if resultado.error is not None and resultado.reintentable:
                intentos = self.estado.reintentos.get(resultado.custom_id, 0) + 1
                self.estado.reintentos[resultado.custom_id] = intentos
                if intentos <= MAX_REINTENTOS:
                    # Not stored: the next round asks again.
                    continue
            self.estado.respuestas[resultado.custom_id] = _Respuesta(
                datos=resultado.datos, error=resultado.error, salida_invalida=resultado.salida_invalida
            )
        return coste_total if coste_conocido else None

    def resumen(self) -> dict[str, Any]:
        """What was sent, for the run's report."""
        return {
            "rondas": self.estado.rondas,
            "lotes": [historia.model_dump(mode="json") for historia in self.estado.lotes],
            "en_curso": [lote.id for lote in self.estado.en_curso],
        }


def clave_de_peticion(proveedor: str, params: dict[str, Any]) -> str:
    """Stable hash of a provider-native request; also its batch ``custom_id`` (64 hex chars)."""
    texto = json.dumps({"proveedor": proveedor, "params": params}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# The replaying provider
# --------------------------------------------------------------------------- #


def admite_lotes(provider: Any) -> bool:
    """Whether ``provider`` (metered or not) can run ``complete_json`` through a batch API."""
    return isinstance(getattr(provider, "wrapped", provider), ProveedorDeLotes)


class ProveedorEnLote:
    """Stands in for a provider during batch rounds (``complete_json`` only).

    ``real`` is the provider the synchronous run would use — normally a
    :class:`~asistente.plataforma.ops.consumo.MeteredProvider`, whose routing (model,
    effort) and escalation it follows. It never calls the model itself.
    """

    def __init__(self, real: Any, cola: ColaDeLotes) -> None:
        nativo = getattr(real, "wrapped", real)
        if not isinstance(nativo, ProveedorDeLotes):
            raise TypeError(f"El proveedor {getattr(real, 'name', '?')} no admite lotes.")
        self._real = real
        self._nativo = nativo
        self._cola = cola
        self.name = real.name
        self.model = real.model
        self.purpose = str(getattr(real, "purpose", "") or "")
        cola.registrar_nativo(nativo)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._real, name)

    def con_proposito(self, purpose: str) -> ProveedorEnLote:
        con_proposito = getattr(self._real, "con_proposito", None)
        return ProveedorEnLote(con_proposito(purpose) if callable(con_proposito) else self._real, self._cola)

    def escalado(self) -> ProveedorEnLote | None:
        escalar = getattr(self._real, "escalado", None)
        superior = escalar() if callable(escalar) else None
        return None if superior is None else ProveedorEnLote(superior, self._cola)

    def start(self, **_: Any) -> Any:
        raise ProviderError("El modo por lotes solo admite llamadas de una sola respuesta (complete_json).")

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        attachments: Sequence[Attachment] | None = None,
        validar: enrutado.Validador | None = None,
    ) -> dict[str, Any]:
        if attachments:
            raise ProviderError("El modo por lotes no envía documentos adjuntos.")
        params = self._nativo.peticion_json(system=system, user=user, schema=schema, schema_name=schema_name)
        clave = clave_de_peticion(self._nativo.name, params)
        guardada = self._cola.respuesta(clave)
        if guardada is None:
            if self._cola.cierre is not None:
                raise self._cola.cierre
            ruta = getattr(self._real, "ruta", None)
            self._cola.pedir(
                clave,
                _Pedido(
                    proveedor=self._nativo.name,
                    modelo=self._nativo.model,
                    proposito=self.purpose,
                    nivel=str(getattr(ruta, "nivel", "") or ""),
                    escalado=bool(getattr(self._real, "es_escalado", False)),
                    params=params,
                ),
            )
            raise PendienteDeLoteError(clave)
        superior = self.escalado()
        if guardada.error is not None:
            if guardada.salida_invalida and superior is not None:
                return superior.complete_json(system=system, user=user, schema=schema, schema_name=schema_name)
            raise ProviderError(guardada.error, salida_invalida=guardada.salida_invalida)
        datos = dict(guardada.datos or {})
        if superior is not None and enrutado.fallo_de_salida(datos, schema, [validar] if validar else []):
            return superior.complete_json(system=system, user=user, schema=schema, schema_name=schema_name)
        return datos


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #


def ejecutar_en_lotes[R](
    ronda: Callable[[], R],
    cola: ColaDeLotes,
    *,
    contable: Contable | None = None,
    esperar: Callable[[float], None] = time.sleep,
    intervalo: float = 60.0,
    max_espera: timedelta = timedelta(hours=24),
    max_rondas: int = 40,
) -> R:
    """Run ``ronda`` (side-effect free; its calls go to :class:`ProveedorEnLote`) to completion.

    Returns the result of the first round that queued nothing. A budget exhausted
    before a submission ends the loop with one last round in which every missing
    answer raises the budget error (so the work reports it as it would in a
    synchronous run). Raises :class:`LoteSinTerminarError` when a batch outlives
    ``max_espera`` (resumable) and RuntimeError past ``max_rondas``.
    """
    while True:
        cola.recoger(contable=contable, esperar=esperar, intervalo=intervalo, max_espera=max_espera)
        cola.pendientes.clear()
        try:
            resultado = ronda()
        except PendienteDeLoteError:
            # A round made of one unit of work may let it through: its request is queued.
            if not cola.pendientes:
                raise
        else:
            if not cola.pendientes:
                return resultado
        if cola.estado.rondas >= max_rondas:
            raise RuntimeError(f"El trabajo por lotes no ha terminado tras {max_rondas} rondas.")
        if contable is not None:
            try:
                contable.comprobar_presupuesto()
            except ProviderError as exc:
                cola.pendientes.clear()
                cola.cierre = exc
                return ronda()
        cola.enviar()


__all__ = [
    "ColaDeLotes",
    "Contable",
    "LoteSinTerminarError",
    "PendienteDeLoteError",
    "ProveedorEnLote",
    "admite_lotes",
    "clave_de_peticion",
    "ejecutar_en_lotes",
]

"""Running one specialist safely inside a turn: cancellation, the question's budget, isolation, events.

* :class:`Cancelacion` — a cooperative token per plan encargo (its requests to other specialists share it). When the
  dispatcher gives up on an encargo (its time ran out) it cancels the token: the specialist's next model call fails
  with :class:`CanceladoError` (a ``ProviderError``, which every loop already handles by degrading), its pending waits
  wake up, and its events, requests and side effects stop being recorded.
* :class:`ProveedorVigilado` — the provider each encargo gets: the turn's metered provider for that purpose, plus
  the token check before every call and the record of :class:`PresupuestoAgotadoError` in the turn's
  :class:`EstadoTurno` (the specialist loops swallow ``ProviderError``; the dispatcher still has to know that the
  budget ran out to stop scheduling).
* :func:`atender_aislado` — a specialist that raises, or returns something that is not an ``InformeEspecialista``,
  ends as a report with ``estado="error"``; the others go on.
* :func:`emisor_seguro` — progress events from several threads, serialised, and a failing callback never breaks the
  turn.
* :func:`neutralizar` — text one model wrote that another model will read (a report, a datum): injection bait
  redacted, and no ``<``/``>`` so it cannot close or open the wrapper tags it travels in.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time
from typing import Any, cast

from asistente.conocimiento.llm.security import neutralizar_inyeccion, sanitize_retrieved_context
from asistente.nucleo.agent.providers.base import LLMProvider, ProviderError
from asistente.nucleo.cerebro.contrato import ContextoEncargo, Emit, Encargo, Especialista, InformeEspecialista
from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

logger = logging.getLogger(__name__)


class CanceladoError(ProviderError):
    """The encargo was cancelled (its time ran out): no more model calls for it."""

    def __init__(self, motivo: str = "") -> None:
        super().__init__(f"Encargo cancelado{': ' + motivo if motivo else ''}.", retryable=False)


class Cancelacion:
    """A cooperative cancellation token (thread-safe; cancelling twice is harmless)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelada = False
        self.motivo = ""
        self._despertar: list[threading.Event] = []

    @property
    def cancelada(self) -> bool:
        return self._cancelada

    def cancelar(self, motivo: str = "") -> None:
        with self._lock:
            if self._cancelada:
                return
            self._cancelada = True
            self.motivo = motivo
            eventos, self._despertar = self._despertar, []
        for evento in eventos:
            evento.set()

    def avisar(self, evento: threading.Event) -> None:
        """Set ``evento`` when the token is cancelled (at once if it already is)."""
        with self._lock:
            if not self._cancelada:
                self._despertar.append(evento)
                return
        evento.set()

    def olvidar(self, evento: threading.Event) -> None:
        with self._lock:
            if evento in self._despertar:
                self._despertar.remove(evento)

    def comprobar(self) -> None:
        if self._cancelada:
            raise CanceladoError(self.motivo)


class EstadoTurno:
    """What the whole turn shares about its run: whether the question's model budget ran out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._presupuesto: PresupuestoAgotadoError | None = None

    def marcar_presupuesto(self, exc: PresupuestoAgotadoError) -> None:
        with self._lock:
            if self._presupuesto is None:
                self._presupuesto = exc

    @property
    def presupuesto(self) -> PresupuestoAgotadoError | None:
        return self._presupuesto

    @property
    def agotado(self) -> bool:
        return self._presupuesto is not None


class _ConversacionVigilada:
    def __init__(self, base: Any, token: Cancelacion, estado: EstadoTurno) -> None:
        self._base = base
        self._token = token
        self._estado = estado

    def step(self, *, allow_tools: bool = True, on_text: Callable[[str], None] | None = None) -> Any:
        self._token.comprobar()
        try:
            return self._base.step(allow_tools=allow_tools, on_text=on_text)
        except PresupuestoAgotadoError as exc:
            self._estado.marcar_presupuesto(exc)
            raise

    def add_tool_results(self, results: Any) -> None:
        self._base.add_tool_results(results)

    def add_user_message(self, text: str) -> None:
        self._base.add_user_message(text)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._base, name)


class ProveedorVigilado:
    """An ``LLMProvider`` that refuses calls once its encargo is cancelled and records budget exhaustion.

    Results and exceptions are the wrapped provider's; anything else (``name``, ``model``, ``effort``…) is read from
    it. ``con_proposito`` keeps the watch on the sibling it returns.
    """

    def __init__(self, base: LLMProvider, *, token: Cancelacion, estado: EstadoTurno) -> None:
        self._base = base
        self._token = token
        self._estado = estado

    @property
    def envuelto(self) -> LLMProvider:
        return self._base

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._base, name)

    def con_proposito(self, proposito: str) -> ProveedorVigilado:
        hermano = getattr(self._base, "con_proposito", None)
        base = hermano(proposito) if callable(hermano) else self._base
        return ProveedorVigilado(base, token=self._token, estado=self._estado)

    def start(self, *, system: str, tools: Any, history: Any, user_message: str) -> _ConversacionVigilada:
        self._token.comprobar()
        conversacion = self._base.start(system=system, tools=tools, history=history, user_message=user_message)
        return _ConversacionVigilada(conversacion, self._token, self._estado)

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        self._token.comprobar()
        try:
            datos: dict[str, Any] = self._base.complete_json(**kwargs)
        except PresupuestoAgotadoError as exc:
            self._estado.marcar_presupuesto(exc)
            raise
        return datos


def vigilar(
    proveedor_para: Callable[[str], LLMProvider], *, token: Cancelacion, estado: EstadoTurno
) -> Callable[[str], LLMProvider]:
    """``proveedor_para`` whose providers are watched by ``token`` and ``estado``."""

    def para(proposito: str) -> LLMProvider:
        return cast("LLMProvider", ProveedorVigilado(proveedor_para(proposito), token=token, estado=estado))

    return para


def emisor_seguro(emit: Emit | None) -> Emit:
    """``emit`` serialised across threads; a failing callback is logged, never raised."""
    lock = threading.Lock()

    def emitir(tipo: str, **datos: Any) -> None:
        if emit is None:
            return
        try:
            with lock:
                emit(tipo, **datos)
        except Exception:
            logger.exception("brain progress callback failed")

    return emitir


def emisor_de_encargo(emitir: Emit, token: Cancelacion) -> Emit:
    """``emitir`` that goes quiet once the encargo is cancelled (its report has already been given up)."""

    def emitir_encargo(tipo: str, **datos: Any) -> None:
        if not token.cancelada:
            emitir(tipo, **datos)

    return emitir_encargo


def neutralizar(texto: str) -> str:
    """Model-written text another model will read: injection bait redacted, no tag delimiters."""
    limpio = neutralizar_inyeccion(sanitize_retrieved_context(texto))
    return limpio.replace("<", "‹").replace(">", "›")


def informe_fallido(encargo: Encargo, nombre: str, estado: str, aviso: str) -> InformeEspecialista:
    """A report for an encargo that produced nothing (error, budget, time, refused)."""
    return InformeEspecialista.model_validate(
        {
            "encargo_id": encargo.id,
            "especialista": encargo.especialista,
            "nombre": nombre or encargo.especialista,
            "estado": estado,
            "avisos": [aviso],
        }
    )


AVISO_PRESUPUESTO = "Se agotó el presupuesto de modelo de esta pregunta antes de que este especialista respondiera."


def atender_aislado(
    especialista: Especialista, contexto: ContextoEncargo, *, estado: EstadoTurno | None = None
) -> InformeEspecialista:
    """Run ``especialista`` on ``contexto`` and always return its report, normalised.

    The report's ``encargo_id`` and ``especialista`` are the encargo's (a specialist cannot answer for another one)
    and ``duracion_ms`` is measured here. A specialist that raises yields ``estado="error"``; the budget running out
    (raised, or swallowed by the specialist's loop and seen by its watched provider) yields ``"presupuesto"``.
    """
    encargo = contexto.encargo
    nombre = str(getattr(especialista, "nombre", "") or encargo.especialista)
    inicio = time.monotonic()
    try:
        informe = especialista.atender(contexto)
        if not isinstance(informe, InformeEspecialista):
            raise TypeError(f"atender devolvió {type(informe).__name__}, no un InformeEspecialista")
    except PresupuestoAgotadoError as exc:
        if estado is not None:
            estado.marcar_presupuesto(exc)
        informe = informe_fallido(encargo, nombre, "presupuesto", AVISO_PRESUPUESTO)
    except Exception as exc:  # a specialist's bug never fails the turn: its report says so
        logger.exception("specialist %s failed on %s", encargo.especialista, encargo.id)
        informe = informe_fallido(encargo, nombre, "error", f"El especialista falló ({type(exc).__name__}).")
    cambios: dict[str, Any] = {
        "encargo_id": encargo.id,
        "especialista": encargo.especialista,
        "nombre": informe.nombre or nombre,
        "duracion_ms": max(0, int((time.monotonic() - inicio) * 1000)),
    }
    if estado is not None and estado.agotado and informe.estado not in ("ok", "presupuesto"):
        cambios["estado"] = "presupuesto"
        if AVISO_PRESUPUESTO not in informe.avisos:
            cambios["avisos"] = [*informe.avisos, AVISO_PRESUPUESTO]
    return informe.model_copy(update=cambios)


__all__ = [
    "AVISO_PRESUPUESTO",
    "CanceladoError",
    "Cancelacion",
    "EstadoTurno",
    "ProveedorVigilado",
    "atender_aislado",
    "emisor_de_encargo",
    "emisor_seguro",
    "informe_fallido",
    "neutralizar",
    "vigilar",
]

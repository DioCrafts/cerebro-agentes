"""The caps of ONE research (model calls, tokens, USD), on top of the service-wide limits of ``ops/consumo.py``.

Every model call of a research is still made through the metering wrapper (``MeteredProvider``): it is
recorded in ``llm_usage`` under its purpose (``investigacion:planificar`` / ``:web`` / ``:sintetizar``) and
checked against the daily limits. This module adds the per-research caps: before each call it refuses when
the calls, tokens or USD spent so far reach the cap; after each call it adds what the API reported.

How the tokens of a ``complete_json`` call are known: providers report every API call to their ``on_usage``
hook (Claude, OpenAI); :func:`observar` chains this module's observer onto that hook, keeping whatever was
there (the metering capture included), and the observer only records while a research is running in the
current context (a context variable), so concurrent researches never mix. A provider without the hook (a test
double, a third-party backend) leaves tokens and cost unknown: the report says so and only the call cap holds.
The web search fee (per search, not per token) is added from ``INVESTIGACION_PRECIO_BUSQUEDA_USD``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
import logging
from typing import Any

from asistente.capacidades.investigacion.modelo import Coste
from asistente.nucleo.agent.providers.base import Usage

logger = logging.getLogger(__name__)

_USOS: ContextVar[list[Usage] | None] = ContextVar("investigacion_usos", default=None)


def _observar(usage: Usage) -> None:
    usos = _USOS.get()
    if usos is not None:
        usos.append(usage)


def observar(proveedor: Any) -> bool:
    """Chain the observer onto ``proveedor.on_usage`` (once). False when the provider has no such hook.

    A metering wrapper forwards attribute reads to the provider it wraps but not writes: the hook is set on
    the provider that actually serves the calls (``wrapped``).
    """
    proveedor = getattr(proveedor, "wrapped", proveedor)
    if proveedor is None or not hasattr(proveedor, "on_usage"):
        return False
    previo = proveedor.on_usage
    if previo is _observar or getattr(previo, "observa_investigacion", False):
        return True
    if previo is None:
        proveedor.on_usage = _observar
        return True

    def ambos(usage: Usage) -> None:
        previo(usage)
        _observar(usage)

    ambos.observa_investigacion = True  # type: ignore[attr-defined]
    # The metering capture is already inside ``previo``: tell ops/consumo.py not to chain it again.
    if getattr(previo, "__name__", "") == "_capturar" or getattr(previo, "captura_consumo", False):
        ambos.captura_consumo = True  # type: ignore[attr-defined]
    proveedor.on_usage = ambos
    return True


@contextmanager
def capturar() -> Iterator[list[Usage]]:
    """The usages reported by the providers inside the block (in this context)."""
    usos: list[Usage] = []
    token = _USOS.set(usos)
    try:
        yield usos
    finally:
        _USOS.reset(token)


class PresupuestoInvestigacionAgotadoError(RuntimeError):
    """A per-research cap was reached; ``limite`` is ``llamadas``, ``tokens`` or ``usd``."""

    def __init__(self, mensaje: str, *, limite: str) -> None:
        super().__init__(mensaje)
        self.limite = limite


Coster = Callable[[Usage], Decimal | None]


def coste_por_defecto(usage: Usage) -> Decimal | None:
    from asistente.plataforma.ops.consumo import calcular_coste

    return calcular_coste(usage)


@dataclass
class PresupuestoInvestigacion:
    max_llamadas: int
    max_tokens: int
    max_usd: Decimal
    precio_busqueda: Decimal
    coster: Coster = coste_por_defecto
    llamadas: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    busquedas: int = 0
    usd: Decimal = Decimal(0)
    coste_desconocido: bool = False
    limite_alcanzado: str | None = None
    _usos: list[Usage] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.tokens_entrada + self.tokens_salida

    def comprobar(self) -> None:
        """Raise :class:`PresupuestoInvestigacionAgotadoError` if one more model call is not allowed."""
        motivo: tuple[str, str] | None = None
        if self.llamadas >= self.max_llamadas:
            motivo = ("llamadas", f"se ha llegado al máximo de {self.max_llamadas} llamadas al modelo")
        elif self.max_tokens and self.tokens >= self.max_tokens:
            motivo = ("tokens", f"se ha llegado al máximo de {self.max_tokens} tokens")
        elif self.max_usd and self.usd >= self.max_usd:
            motivo = ("usd", f"se ha llegado al máximo de {self.max_usd} USD")
        if motivo is not None:
            self.limite_alcanzado = motivo[0]
            raise PresupuestoInvestigacionAgotadoError(
                f"Investigación detenida: {motivo[1]} por investigación.", limite=motivo[0]
            )

    def anotar(self, usos: list[Usage], *, llamadas: int = 1, busquedas: int = 0) -> None:
        """Add one (or more) model calls and what the API reported for them."""
        self.llamadas += llamadas
        self.busquedas += busquedas
        if busquedas:
            self.usd += self.precio_busqueda * busquedas
        if not usos:
            self.coste_desconocido = True
        for usage in usos:
            self._usos.append(usage)
            self.tokens_entrada += usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
            self.tokens_salida += usage.output_tokens
            coste = self.coster(usage) if usage.reported else None
            if coste is None:
                self.coste_desconocido = True
            else:
                self.usd += coste

    def coste(self) -> Coste:
        return Coste(
            llamadas_modelo=self.llamadas,
            tokens_entrada=self.tokens_entrada,
            tokens_salida=self.tokens_salida,
            busquedas_web=self.busquedas,
            usd=None if self.coste_desconocido else self.usd.quantize(Decimal("0.000001")),
            limite_alcanzado=self.limite_alcanzado,
        )


__all__ = [
    "Coster",
    "PresupuestoInvestigacion",
    "PresupuestoInvestigacionAgotadoError",
    "capturar",
    "coste_por_defecto",
    "observar",
]

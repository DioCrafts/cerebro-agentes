"""The budget meter: tokens, euros, reads. Thread-safe (searchers/readers run in parallel)."""

from __future__ import annotations

import threading

from asistente.investigador.modelo import Coste, Presupuesto


class PresupuestoAgotadoError(RuntimeError):
    """A cap was reached: the research stops calling models and writes with what it has."""


def estimar_tokens(texto: str) -> int:
    return max(1, len(texto) // 4)


class Contador:
    def __init__(self, presupuesto: Presupuesto) -> None:
        self.presupuesto = presupuesto
        self.coste = Coste()
        self._lock = threading.Lock()

    def _excedido(self) -> str | None:
        p, c = self.presupuesto, self.coste
        if c.tokens >= p.max_tokens:
            return f"tokens ({c.tokens}/{p.max_tokens})"
        if p.max_euros is not None and c.euros >= p.max_euros:
            return f"euros ({c.euros:.4f}/{p.max_euros})"
        return None

    def comprobar(self, tokens_previstos: int = 0) -> None:
        """Raise before a call that would go over the token cap (or once any cap is spent)."""
        with self._lock:
            motivo = self._excedido()
            if motivo is None and self.coste.tokens + tokens_previstos > self.presupuesto.max_tokens:
                motivo = f"tokens (la llamada superaría {self.presupuesto.max_tokens})"
            if motivo:
                raise PresupuestoAgotadoError(f"Presupuesto agotado: {motivo}")

    def registrar(self, tokens: int, euros: float | None) -> None:
        with self._lock:
            self.coste.tokens += tokens
            self.coste.llamadas += 1
            if euros is None or euros <= 0:
                euros = tokens * self.presupuesto.euros_por_millon_tokens / 1_000_000
            self.coste.euros += euros

    def registrar_gasto(self, euros: float) -> None:
        """Money spent outside the roles' model calls (the web searcher's search and fetch calls): it counts for
        ``max_euros``, not for the token cap, which measures what the roles read and write."""
        if euros > 0:
            with self._lock:
                self.coste.euros += euros

    def reservar_lectura(self) -> bool:
        """One more page read; False once ``max_lecturas`` are spent.

        Reaching the reads cap only stops reading new pages: the research goes on with what it has read. (It used
        to raise PresupuestoAgotadoError, and as the first round could spend every read — 5 subquestions × 3
        sources = 15 — the gap-filling round aborted the whole research on its first new page.)
        """
        with self._lock:
            if self.coste.lecturas >= self.presupuesto.max_lecturas:
                return False
            self.coste.lecturas += 1
            return True

    @property
    def agotado(self) -> bool:
        with self._lock:
            return self._excedido() is not None

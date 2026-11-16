"""Operator settings of the research capability, read on every call (tests change them on the fly).

Every value is read from ``asistente.plataforma.settings`` when the attribute exists there, else from the environment,
else the default below — so the capability works before (or without) its block in ``settings.py``.

* ``INVESTIGACION_WEB`` (false) — the open web through the model provider's server-side web search. Off by
  default; when on, each user still has to give their own consent (``almacen.py``).
* ``INVESTIGACION_WEB_PERMITIDOS`` — the allow-list used when a request asks for official sites only (the
  default): comma-separated host names; empty = :data:`dominios.OFICIALES_POR_DEFECTO`.
* ``INVESTIGACION_WEB_BLOQUEADOS`` — hosts never searched nor read (comma-separated), in every mode.
* ``INVESTIGACION_MAX_USD`` (0.50) / ``INVESTIGACION_MAX_TOKENS`` (300000) — caps of ONE research, on top of the
  service-wide daily limits of ``ops/consumo.py``. The number of model calls is capped by depth.
* ``INVESTIGACION_PRECIO_BUSQUEDA_USD`` (0.01) — what one web search costs (see :data:`PRECIO_BUSQUEDA_FUENTE`).
* ``INVESTIGACION_CACHE_DIAS`` (7) — days a report is kept for the same user, question and day.
* ``INVESTIGACION_DB_PATH`` (``./data/investigacion.sqlite3``) — the capability's own SQLite file.
* ``INVESTIGACION_AGENTE`` (false) — offer the ``investigar`` tool to the central agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from typing import Any

from asistente.capacidades.investigacion.dominios import OFICIALES_POR_DEFECTO, normalizar_lista_dominios
from asistente.capacidades.investigacion.modelo import Profundidad
from asistente.plataforma import settings

# The web search fee is not a token price: it is not in ops/data/precios_llm.json. Value and source as read
# in Anthropic's documentation (claude-api reference, cached 2026-11-03: «web searches at $10 per 1,000»).
# Web fetch has no per-call fee there (its content is billed as input tokens). Operators update it here.
PRECIO_BUSQUEDA_POR_DEFECTO = "0.01"
PRECIO_BUSQUEDA_FUENTE = "Anthropic, documentación de la API (consultada 2026-11): 10 USD por cada 1.000 búsquedas web"


def _valor(nombre: str, defecto: str) -> str:
    valor: Any = getattr(settings, nombre, None)
    if valor is None:
        valor = os.environ.get(nombre, defecto)
    return str(valor)


def _bool(nombre: str, defecto: bool) -> bool:
    valor: Any = getattr(settings, nombre, None)
    if isinstance(valor, bool):
        return valor
    texto = str(valor if valor is not None else os.environ.get(nombre, "")).strip().lower()
    if not texto:
        return defecto
    return texto in {"1", "true", "yes", "si", "sí", "on"}


def _decimal(nombre: str, defecto: str) -> Decimal:
    texto = _valor(nombre, defecto).strip() or defecto
    try:
        numero = Decimal(texto)
    except InvalidOperation as exc:
        raise ValueError(f"{nombre} debe ser un número, no {texto!r}.") from exc
    if not numero.is_finite() or numero < 0:
        raise ValueError(f"{nombre} debe ser un número positivo, no {texto!r}.")
    return numero


def _entero(nombre: str, defecto: int) -> int:
    texto = _valor(nombre, str(defecto)).strip() or str(defecto)
    try:
        numero = int(texto)
    except ValueError as exc:
        raise ValueError(f"{nombre} debe ser un entero, no {texto!r}.") from exc
    if numero < 0:
        raise ValueError(f"{nombre} debe ser un entero positivo, no {texto!r}.")
    return numero


@dataclass(frozen=True)
class LimitesProfundidad:
    """What one depth may do: sub-questions, results per source, web searches and reads, model calls, sources."""

    subpreguntas: int
    corpus_k: int
    normas_boe: int
    busquedas_web: int
    lecturas_web: int
    llamadas: int
    max_fuentes: int


LIMITES: dict[Profundidad, LimitesProfundidad] = {
    # One plan, one web call (and up to 2 continuations), one synthesis.
    "rapida": LimitesProfundidad(1, 3, 1, 2, 2, 5, 8),
    "normal": LimitesProfundidad(3, 4, 2, 4, 4, 6, 16),
    "a_fondo": LimitesProfundidad(5, 5, 3, 8, 6, 8, 24),
}


@dataclass(frozen=True)
class Ajustes:
    web: bool
    permitidos: tuple[str, ...]
    bloqueados: tuple[str, ...]
    max_usd: Decimal
    max_tokens: int
    precio_busqueda_usd: Decimal
    cache_dias: int
    db_path: str
    agente: bool

    def limites(self, profundidad: Profundidad) -> LimitesProfundidad:
        return LIMITES[profundidad]

    def permitidos_efectivos(self) -> tuple[str, ...]:
        """The allow-list minus anything blocked (the API takes one list or the other, never both)."""
        bloqueados = set(self.bloqueados)
        return tuple(d for d in self.permitidos if d not in bloqueados)


def ajustes() -> Ajustes:
    """The current settings. Raises ValueError on an invalid value (a domain list, a number)."""
    permitidos = normalizar_lista_dominios(_valor("INVESTIGACION_WEB_PERMITIDOS", ""), "INVESTIGACION_WEB_PERMITIDOS")
    return Ajustes(
        web=_bool("INVESTIGACION_WEB", False),
        permitidos=permitidos or OFICIALES_POR_DEFECTO,
        bloqueados=normalizar_lista_dominios(
            _valor("INVESTIGACION_WEB_BLOQUEADOS", ""), "INVESTIGACION_WEB_BLOQUEADOS"
        ),
        max_usd=_decimal("INVESTIGACION_MAX_USD", "0.50"),
        max_tokens=_entero("INVESTIGACION_MAX_TOKENS", 300_000),
        precio_busqueda_usd=_decimal("INVESTIGACION_PRECIO_BUSQUEDA_USD", PRECIO_BUSQUEDA_POR_DEFECTO),
        cache_dias=_entero("INVESTIGACION_CACHE_DIAS", 7),
        db_path=_valor("INVESTIGACION_DB_PATH", "./data/investigacion.sqlite3"),
        agente=_bool("INVESTIGACION_AGENTE", False),
    )


__all__ = [
    "LIMITES",
    "PRECIO_BUSQUEDA_FUENTE",
    "PRECIO_BUSQUEDA_POR_DEFECTO",
    "Ajustes",
    "LimitesProfundidad",
    "ajustes",
]

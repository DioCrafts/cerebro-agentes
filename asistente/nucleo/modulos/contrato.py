"""The contract every life-admin module implements (fiscal, luz, caducidades, ayudas…).

A module is a package ``asistente.ambitos.<clave>`` whose ``__init__`` exposes ``MODULO``, an object
satisfying :class:`Modulo`. The central agent never computes anything itself: it routes the user's
question to the specialist of each module, whose tools are deterministic calculators over the household
profile. The same rules as the tax calculator hold for every module:

- every legal or official figure is READ from its source (BOE first) and stored as versioned data with a
  literal quote; never from memory;
- tools compute deterministically and return text with the figures, their sources and their caveats;
- proactive jobs return :class:`AvisoModulo` items; the core turns them into notifications;
- a tool whose effect reaches outside the app (sending, contracting, cancelling) is ``efecto="actua"`` and
  the core never runs it without the user's explicit confirmation.

This file is the stable interface between the core and the modules: change it only with every module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from asistente.nucleo.agent.providers.base import ToolSpec

if TYPE_CHECKING:
    from asistente.nucleo.agent.evidence import EvidenceLedger
    from asistente.nucleo.decision.schemas import CaseState

Efecto = Literal["lectura", "prepara", "actua"]
TipoHecho = Literal["string", "number", "boolean", "json"]
Gravedad = Literal["info", "aviso", "urgente"]
# The same values as ``asistente.nucleo.cerebro.contrato.Unidad`` (a test keeps them equal; repeated here so this
# contract does not import the brain's).
UnidadCifra = Literal["EUR", "EUR/mes", "EUR/año", "%", "kWh", "kW", "dias", "meses", "años", "unidades"]


@dataclass(frozen=True)
class CifraHerramienta:
    """A figure a tool computed, declared in structure (optional, see ``ResultadoHerramienta.cifras``).

    The household brain ties every figure of its final answer to the tool that produced it. A module that declares
    its figures here is attributed exactly; without them the core reads the figures from ``contenido`` (Spanish
    number parsing, ``cerebro/colaboracion/cifras.py``). ``literal`` is how the figure is written in ``contenido``
    («812,34 €»); ``valor`` is exact (Decimal, never float).
    """

    concepto: str  # «cuota mensual», «ahorro anual estimado»
    valor: Decimal
    unidad: UnidadCifra | None = None
    ejercicio: int | None = None
    literal: str = ""


@dataclass(frozen=True)
class ResultadoHerramienta:
    """What a module tool returns to the agent.

    ``contenido`` is the full text the model reads (figures, sources, caveats, in Spanish); ``resumen`` a
    one-line summary for the trace; ``citas`` the sources (BOE ids, URLs) the answer may cite; ``cifras`` (optional)
    the figures of ``contenido`` in structure, for the household brain's figures gate.
    """

    contenido: str
    resumen: str
    es_error: bool = False
    citas: tuple[str, ...] = ()
    cifras: tuple[CifraHerramienta, ...] = ()


@dataclass(frozen=True)
class ContextoModulo:
    """What a module sees when one of its tools or jobs runs.

    ``perfil`` is the household profile: every fact of the user's case (name -> value; JSON-valued facts
    already parsed). A module reads the facts it declared and any shared one (comunidad_autonoma,
    estado_civil, num_descendientes…). ``registrar`` is present only when the run may write facts: it
    stores facts this module declared, validated by their ``validar``.

    ``evidencia`` and ``caso`` are set only when the tool runs as a DIRECT tool of the central agent (a module
    with ``herramientas_directas``): ``evidencia`` is the run's evidence ledger, where the tool registers its
    own citable fragments (its ``contenido`` then reaches the model as it is, ``[F#]`` ids included), and
    ``caso`` the user's case as stored (None in a conversation without one).
    """

    perfil: Mapping[str, Any]
    hoy: date
    case_id: str | None = None
    registrar: Callable[[Mapping[str, Any]], None] | None = None
    evidencia: EvidenceLedger | None = None
    caso: CaseState | None = None


@dataclass(frozen=True)
class HechoModulo:
    """A fact of the household profile that a module owns (stored in the user's case).

    ``nombre`` must be prefixed with the module key (``luz_potencia_contratada_kw``) unless it is one of
    the shared facts the core already defines. ``validar`` normalises a value or raises ``ValueError`` with
    a Spanish message; for ``json`` facts it receives the parsed value and returns the canonical one.
    ``sensible`` marks personal data the privacy docs must mention (health, a third party, finances).
    """

    nombre: str
    tipo: TipoHecho
    etiqueta: str
    descripcion: str
    validar: Callable[[Any], Any] | None = None
    sensible: bool = False


@dataclass(frozen=True)
class HerramientaModulo:
    spec: ToolSpec
    ejecutar: Callable[[Mapping[str, Any], ContextoModulo], ResultadoHerramienta]
    efecto: Efecto = "lectura"


@dataclass(frozen=True)
class AvisoModulo:
    """A proactive notice. ``clave`` dedupes it per case (same key = same notice, never repeated)."""

    clave: str
    titulo: str
    cuerpo: str
    gravedad: Gravedad = "info"
    acciones: tuple[str, ...] = ()
    fuentes: tuple[str, ...] = ()
    importe: Decimal | None = None
    vence: date | None = None
    # Keys of earlier notices of this module that this one replaces (e.g. an updated estimate).
    sustituye_prefijo: str | None = None
    # Optional (event bus): wake this module again for this case on that date, even if nothing happens
    # (e.g. the day a deadline enters its notice window). The earliest future date of a run wins.
    proxima_revision: date | None = None


@dataclass(frozen=True)
class FuenteModulo:
    """A source a module's data is read from, for the maintenance loop (verification, format watch)."""

    identificador: str  # BOE id or a stable name
    descripcion: str
    url: str


@runtime_checkable
class Modulo(Protocol):
    clave: str  # "fiscal", "luz", "caducidades", "ayudas", "suscripciones", "hipoteca"
    nombre: str  # Spanish, for the UI
    descripcion: str  # Spanish, what the module answers: the central agent routes by it
    instrucciones: str  # Spanish system-prompt fragment for the module's specialist
    hechos: Sequence[HechoModulo]
    herramientas: Sequence[HerramientaModulo]
    fuentes: Sequence[FuenteModulo]

    def avisos(self, contexto: ContextoModulo) -> list[AvisoModulo]:
        """Deterministic proactive job, run daily per case. Must be idempotent (same keys)."""
        ...

    # Optional event hooks (asistente/nucleo/eventos), read with getattr so every existing module still
    # satisfies this protocol: ``eventos: Sequence[str]`` (event types the module subscribes to; empty =
    # the core's default for it, see eventos/rutas.py), ``al_evento(evento, contexto)`` (what to do when
    # woken; default: ``avisos``), ``proxima_revision(contexto) -> date | None`` (when to wake it next
    # without any event) and ``hechos_leidos: Sequence[str]`` (shared facts it reads without declaring).
    # Optional data hook, also read with getattr: ``borrar_datos_de_caso(case_id, db_path)`` erases what the
    # module keeps OUTSIDE the case's facts (its own tables, a third-party grant) when the user switches it off
    # with «borrar datos» (``PUT /me/modulos/{clave}``); the facts and decisions are erased by the core.
    # Optional core-integration hooks, also read with getattr (the tax module uses them; see agent/tools.py):
    # ``herramientas_directas: bool`` — the central agent offers the module's tools DIRECTLY, right after the
    # corpus search, instead of through a ``consultar_<clave>`` specialist; they run with ``ContextoModulo``'s
    # ``evidencia`` and ``caso`` and must only read (``efecto="lectura"``; any other is never offered).
    # ``ayuda_registrar_hechos: str`` — text appended to the description of the core's ``registrar_hechos``
    # tool, documenting the shared facts whose shape the module knows; ``hechos_estructurados:
    # Mapping[str, Callable[[Any], str]]`` — for those shared facts, a function that validates a value and
    # returns the canonical text to store (raises ``ValueError`` with a Spanish message).
    # ``ejecutar_en_bandeja(case, *, hoy, store) -> dict[str, int]`` — the module writes its own notifications
    # (kinds ``tipos_en_bandeja: Sequence[str]``, counted in the daily summary) instead of returning ``avisos``.


@dataclass
class ModuloBase:
    """Convenience base: a module with no proactive job until it overrides ``avisos``."""

    clave: str
    nombre: str
    descripcion: str
    instrucciones: str
    hechos: Sequence[HechoModulo] = field(default_factory=tuple)
    herramientas: Sequence[HerramientaModulo] = field(default_factory=tuple)
    fuentes: Sequence[FuenteModulo] = field(default_factory=tuple)
    # Event types this module wants to be woken by (asistente/nucleo/eventos/tipos.py); empty: the core's default.
    eventos: Sequence[str] = field(default_factory=tuple)
    # True: the central agent offers this module's (read-only) tools directly (see the Modulo hooks above).
    herramientas_directas: bool = False

    def avisos(self, contexto: ContextoModulo) -> list[AvisoModulo]:
        return []

    def al_evento(self, evento: Any, contexto: ContextoModulo) -> list[AvisoModulo]:
        """Woken by ``evento`` (a ``asistente.nucleo.eventos.Evento``): by default, the same notices as daily."""
        return self.avisos(contexto)

    def proxima_revision(self, contexto: ContextoModulo) -> date | None:
        """The next date to be woken for this case without any event (None: only events and the daily run)."""
        return None

    def borrar_datos_de_caso(self, case_id: str, db_path: str) -> None:
        """Erase what the module keeps outside the case's facts (switched off with «borrar datos»): nothing here."""
        return None

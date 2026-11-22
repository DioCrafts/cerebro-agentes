"""Contract v2 of a domain (``ámbito``): its optional integrations, DECLARED, in one object.

v1 (``contrato.py``) left a dozen optional hooks to be found with ``getattr`` here and there in the core
(``eventos``, ``al_evento``, ``proxima_revision``, ``borrar_datos_de_caso``, ``leer_documento``,
``herramientas_directas``, ``ejecutar_en_bandeja``, ``tarea_diaria``…), and the core still told the tax module
apart by its key. v2 declares them all in :class:`Ganchos`, one dataclass per concern, each with defaults, and the
registry validates them (``registro.problemas_de``)::

    class ModuloLuz:
        ...
        @cached_property
        def ganchos(self) -> Ganchos:
            return Ganchos(eventos=GanchosEventos(tipos=(...), hechos_leidos=("comunidad_autonoma",)), ...)

The core reads a domain's hooks ONLY through :func:`ganchos_de`, which returns the v2 declaration of a v2 domain
and, for a v1 one, builds the same object from its v1 attributes (the adapter): every v1 module keeps working.

What each concern means:

* :class:`GanchosEventos` — event types the domain subscribes to (``tipos``; empty: the core's default), the
  shared facts it reads without declaring them (``hechos_leidos``; ``("*",)`` = all), what to do when woken
  (``al_evento``; default: its ``avisos``) and when to wake it next without any event (``proxima_revision``).
* :class:`GanchosBandeja` — a domain that writes its own notifications instead of returning ``avisos``: the
  runner, the kinds it writes (``tipos``, counted in the daily summary; ``otros_tipos``, written by the core on its
  behalf), its reaction to an event (``al_evento``; default: the runner) and its next wake-up for a case.
* :class:`GanchosDocumentos` — it reads an uploaded document and proposes the arguments of one of its tools.
* :class:`GanchosAgente` — integration with the central agent: tools offered DIRECTLY (by name; read-only,
  checked), whether it has a specialist, the text added to ``registrar_hechos`` and the shared facts whose shape
  it knows.
* ``tarea_diaria`` — preparation once a day before the per-case notices; ``borrar_datos_de_caso`` — erase what it
  keeps outside the case's facts; ``ficheros_legales`` — its legal data files (``conocimiento.FicheroLegal``);
  ``puentes`` — values it proposes to OTHER domains (never written without the user); ``entradas`` — data other
  parts of the app may PROPOSE to it (kind -> its tool or fact; the user confirms each); ``capacidades`` — the
  capabilities it asks for with ``capacidades.pedir`` (``"documentos"`` or ``"documentos.leer_campos"``);
  ``obligatorio`` — always on (the user cannot switch it off: the tax module, the advisor's base);
  ``fuentes_vigiladas_aparte`` — its ``fuentes`` are watched by its own maintenance (the tax module's laws: the
  tax parameters' ``params-check``), so the household modules' BOE watch (``modulos/fuentes.py``) leaves them out.
* :class:`Declaracion` — what the domain PROMISES, for the plan's rule «a module only counts if it passes its own
  evaluation»: the capabilities it claims (Spanish sentences), its own evaluation cases (a JSONL file under
  ``eval_data/modulos/``, run by ``asistente.nucleo.evaluacion.modulos``) and the minimum score it must reach, the
  data it OFFERS other domains (:class:`DatoOfrecido`: one of its facts or read-only tools) and the data it may ASK
  of them (:class:`DatoPedido`), served by ``asistente.nucleo.modulos.datos.pedir_dato``. A v1 module (or a v2 one
  that declares nothing) gets an empty declaration: it still loads, but the contract test flags it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from asistente.nucleo.modulos.contrato import AvisoModulo, ContextoModulo

if TYPE_CHECKING:
    from asistente.nucleo.decision.schemas import CaseState
    from asistente.nucleo.modulos.puentes import PropuestaHecho
    from asistente.nucleo.proactive.store import NotificationStore

TODOS: Literal["*"] = "*"


@dataclass(frozen=True)
class GanchosEventos:
    tipos: tuple[str, ...] = ()
    hechos_leidos: tuple[str, ...] = ()
    al_evento: Callable[[Any, ContextoModulo], Sequence[AvisoModulo]] | None = None
    proxima_revision: Callable[[ContextoModulo], date | None] | None = None


@dataclass(frozen=True)
class GanchosBandeja:
    # (case, *, hoy, store) -> notices created per kind
    ejecutar: Callable[..., Mapping[str, int]]
    tipos: tuple[str, ...] = ()
    otros_tipos: tuple[str, ...] = ()
    # (evento, case, *, hoy, store) -> notices created, or None to run ``ejecutar`` (and ``proxima_revision``).
    al_evento: Callable[..., int | None] | None = None
    # (case, *, hoy) -> next wake-up for the case
    proxima_revision: Callable[..., date | None] | None = None


@dataclass(frozen=True)
class GanchosDocumentos:
    # (provider, *, filename, data) -> {"campos": [...], "avisos": [...], "descartados": [...]} or a pydantic model
    leer: Callable[..., Any]
    # The domain's tool that receives the confirmed fields (None: shown, not applied).
    herramienta_destino: str | None = None


@dataclass(frozen=True)
class GanchosAgente:
    # Tools the central agent offers directly (no specialist): names of the domain's READ-ONLY tools.
    directas: tuple[str, ...] = ()
    # False: the domain is never consulted through a ``consultar_<clave>`` specialist.
    especialista: bool = True
    ayuda_registrar_hechos: str = ""
    hechos_estructurados: Mapping[str, Callable[[Any], str]] = field(default_factory=dict)


Puente = Callable[["CaseState", dict[str, Any], date], "PropuestaHecho | None"]


@dataclass(frozen=True)
class PuenteAmbito:
    """A value this domain proposes to another (``destino``): computed by ``proponer``, never written by it."""

    destino: str
    proponer: Puente


@dataclass(frozen=True)
class DatoOfrecido:
    """A datum this domain serves to others: ``nombre`` (unique within the domain) from ``origen``.

    ``origen`` is ``hecho:<one of its facts>`` (the stored value) or ``herramienta:<one of its read-only tools>``
    (its result, run with ``argumentos``).
    """

    nombre: str
    descripcion: str
    origen: str
    argumentos: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatoPedido:
    """A datum this domain may ask of another (``modulo``'s :class:`DatoOfrecido` ``dato``) and what for."""

    modulo: str
    dato: str
    para: str


@dataclass(frozen=True)
class Declaracion:
    capacidades: tuple[str, ...] = ()
    # Path relative to the repository root (``eval_data/modulos/<clave>.jsonl``); None: no evaluation (flagged).
    evaluacion: str | None = None
    # Minimum share of its cases the domain must pass (0..1) to count.
    umbral_evaluacion: float = 1.0
    ofrece: tuple[DatoOfrecido, ...] = ()
    pide: tuple[DatoPedido, ...] = ()


@dataclass(frozen=True)
class Ganchos:
    version: int = 2
    obligatorio: bool = False
    eventos: GanchosEventos = field(default_factory=GanchosEventos)
    bandeja: GanchosBandeja | None = None
    documentos: GanchosDocumentos | None = None
    agente: GanchosAgente = field(default_factory=GanchosAgente)
    tarea_diaria: Callable[[date], Any] | None = None
    borrar_datos_de_caso: Callable[[str, str], None] | None = None
    ficheros_legales: tuple[Any, ...] = ()
    puentes: tuple[PuenteAmbito, ...] = ()
    # kind of proposed data -> the domain's tool (``herramienta:<nombre>``) or fact (``hecho:<nombre>``)
    entradas: Mapping[str, str] = field(default_factory=dict)
    capacidades: tuple[str, ...] = ()
    fuentes_vigiladas_aparte: bool = False
    declaracion: Declaracion = field(default_factory=Declaracion)

    @property
    def directas(self) -> tuple[str, ...]:
        return self.agente.directas


# --------------------------------------------------------------------------- #
# v1 -> v2 adapter
# --------------------------------------------------------------------------- #

# Daily tasks of v1 modules found by name in their package (what ``proactive/bandeja.TAREAS_DIARIAS`` did).
TAREAS_DIARIAS_V1: dict[str, str] = {"ayudas": "actualizar_convocatorias"}
# The key the v1 core told the tax module apart by (only the v1 adapter still reads it).
CLAVE_FISCAL_V1 = "fiscal"


def _invocable(valor: Any) -> Callable[..., Any] | None:
    return valor if callable(valor) else None


def _tarea_diaria_v1(modulo: Any) -> Callable[[date], Any] | None:
    propia = _invocable(getattr(modulo, "tarea_diaria", None))
    if propia is not None:
        return propia
    nombre = TAREAS_DIARIAS_V1.get(str(getattr(modulo, "clave", "")))
    if nombre is None:
        return None
    import importlib

    try:
        paquete = importlib.import_module(f"asistente.ambitos.{modulo.clave}")
    except Exception:
        return None
    return _invocable(getattr(paquete, nombre, None))


def _puentes_v1(modulo: Any) -> tuple[PuenteAmbito, ...]:
    from asistente.nucleo.modulos.puentes import PUENTES

    puente = PUENTES.get(str(getattr(modulo, "clave", "")))
    return (PuenteAmbito(destino="fiscal", proponer=puente),) if puente is not None else ()


def _ley_v1(evento: Any, case: CaseState, *, hoy: date, store: NotificationStore) -> int | None:
    """What the v1 core did for ANY module with its own inbox runner: notify new tax law to the case."""
    from asistente.ambitos.fiscal.modulo import al_evento_en_bandeja

    return al_evento_en_bandeja(evento, case, hoy=hoy, store=store)


def desde_v1(modulo: Any) -> Ganchos:
    """The v2 declaration a v1 module amounts to (its optional attributes, read as the core always did)."""
    directas_v1 = getattr(modulo, "herramientas_directas", False) is True
    herramientas = tuple(getattr(modulo, "herramientas", ()) or ())
    ejecutar = _invocable(getattr(modulo, "ejecutar_en_bandeja", None))
    leer = _invocable(getattr(modulo, "leer_documento", None))
    destino = getattr(modulo, "herramienta_de_documento", None)
    return Ganchos(
        version=1,
        # v1 compatibility: the v1 core kept the tax module on by its key and watched its laws apart.
        obligatorio=getattr(modulo, "clave", None) == CLAVE_FISCAL_V1,
        fuentes_vigiladas_aparte=getattr(modulo, "clave", None) == CLAVE_FISCAL_V1,
        eventos=GanchosEventos(
            tipos=tuple(str(t) for t in (getattr(modulo, "eventos", None) or ())),
            hechos_leidos=tuple(str(n) for n in (getattr(modulo, "hechos_leidos", None) or ())),
            al_evento=_invocable(getattr(modulo, "al_evento", None)),
            proxima_revision=_invocable(getattr(modulo, "proxima_revision", None)),
        ),
        bandeja=(
            GanchosBandeja(
                ejecutar=ejecutar, tipos=tuple(getattr(modulo, "tipos_en_bandeja", ()) or ()), al_evento=_ley_v1
            )
            if ejecutar is not None
            else None
        ),
        documentos=(
            GanchosDocumentos(leer=leer, herramienta_destino=destino if isinstance(destino, str) else None)
            if leer is not None
            else None
        ),
        agente=GanchosAgente(
            # v1 offered only the read-only tools of a flagged module, silently skipping the others.
            directas=tuple(
                h.spec.name for h in herramientas if directas_v1 and getattr(h, "efecto", None) == "lectura"
            ),
            especialista=not directas_v1,
            ayuda_registrar_hechos=str(getattr(modulo, "ayuda_registrar_hechos", "") or ""),
            hechos_estructurados=dict(getattr(modulo, "hechos_estructurados", None) or {}),
        ),
        tarea_diaria=_tarea_diaria_v1(modulo),
        borrar_datos_de_caso=_invocable(getattr(modulo, "borrar_datos_de_caso", None)),
        puentes=_puentes_v1(modulo),
    )


def ganchos_de(modulo: Any) -> Ganchos:
    """The hooks of ``modulo``: its v2 declaration, or the adapter's reading of its v1 attributes."""
    declarados = getattr(modulo, "ganchos", None)
    if isinstance(declarados, Ganchos):
        return declarados
    return desde_v1(modulo)


def declaracion_de(modulo: Any) -> Declaracion:
    return ganchos_de(modulo).declaracion


def es_v2(modulo: Any) -> bool:
    return isinstance(getattr(modulo, "ganchos", None), Ganchos)


# --------------------------------------------------------------------------- #
# Questions the core asks (so no caller looks at a module's key)
# --------------------------------------------------------------------------- #


def obligatorio(modulo: Any) -> bool:
    """Always on: the user cannot switch it off and the operator's switch does not apply."""
    return ganchos_de(modulo).obligatorio


def fuentes_vigiladas_aparte(modulo: Any) -> bool:
    """Its sources are watched by its own maintenance, not by the household modules' BOE watch."""
    return ganchos_de(modulo).fuentes_vigiladas_aparte


def tiene_especialista(modulo: Any) -> bool:
    return ganchos_de(modulo).agente.especialista


def escribe_su_bandeja(modulo: Any) -> bool:
    return ganchos_de(modulo).bandeja is not None


def directas_de(modulo: Any) -> tuple[str, ...]:
    return ganchos_de(modulo).agente.directas


def proxima_revision_de_caso(modulo: Any, case: CaseState, *, hoy: date) -> date | None:
    """The next wake-up of a domain for a case, without producing or storing any notice (cheap)."""
    ganchos = ganchos_de(modulo)
    if ganchos.bandeja is not None:
        propia_caso = ganchos.bandeja.proxima_revision
        if propia_caso is None:
            return None
        valor = propia_caso(case, hoy=hoy)
        return valor if isinstance(valor, date) and valor > hoy else None
    propia = ganchos.eventos.proxima_revision
    if propia is None:
        return None
    from asistente.nucleo.modulos.perfil import perfil_de

    valor = propia(ContextoModulo(perfil=perfil_de(case), hoy=hoy, case_id=case.case_id))
    return valor if isinstance(valor, date) and valor > hoy else None


def propietario_de_tipo(kind: str, modulos: Sequence[Any]) -> str | None:
    """The domain that writes notifications of ``kind`` itself (its ``bandeja`` kinds), or None."""
    for modulo in modulos:
        bandeja = ganchos_de(modulo).bandeja
        if bandeja is not None and (kind in bandeja.tipos or kind in bandeja.otros_tipos):
            return str(modulo.clave)
    return None


def destino_de_entrada(tipo: str, modulos: Sequence[Any]) -> tuple[Any, str, str] | None:
    """The domain that accepts proposed data of ``tipo``: (module, "herramienta"|"hecho", name), or None."""
    for modulo in modulos:
        destino = ganchos_de(modulo).entradas.get(tipo)
        if not destino or ":" not in destino:
            continue
        clase, nombre = destino.split(":", 1)
        if clase in ("herramienta", "hecho"):
            return modulo, clase, nombre
    return None


__all__ = [
    "TAREAS_DIARIAS_V1",
    "TODOS",
    "DatoOfrecido",
    "DatoPedido",
    "Declaracion",
    "Ganchos",
    "GanchosAgente",
    "GanchosBandeja",
    "GanchosDocumentos",
    "GanchosEventos",
    "Puente",
    "PuenteAmbito",
    "declaracion_de",
    "desde_v1",
    "destino_de_entrada",
    "directas_de",
    "es_v2",
    "escribe_su_bandeja",
    "fuentes_vigiladas_aparte",
    "ganchos_de",
    "obligatorio",
    "propietario_de_tipo",
    "proxima_revision_de_caso",
    "tiene_especialista",
]

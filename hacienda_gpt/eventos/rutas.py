"""Which module an event wakes: subscriptions, and what each module reads of the profile.

A module subscribes to event types through the optional contract attribute ``Modulo.eventos``. While a
module declares none (every module written before the bus), the core's defaults below apply: they were
set by reading each module's code, so no module package had to change. An event wakes a module only when

1. the module is subscribed to the event type,
2. the event concerns it: a directed event names it; a profile event touches a fact it reads (its own
   facts, the shared facts it declares, the shared facts listed below, or ``Modulo.hechos_leidos``), and
3. the module is on for the owner of the case (checked by the bus when it fans out, and again just before
   it runs).

Anything this routing misses is still caught by the daily run (``proactive run-daily``), the safety net.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from hacienda_gpt.eventos.tipos import (
    CAMBIO_NORMATIVO,
    CARGOS_IMPORTADOS,
    DIRIGIDOS,
    FECHA_UMBRAL,
    INDICE_PUBLICADO,
    MODULO_ACTIVADO,
    NORMA_BOE,
    PERFIL_CAMBIADO,
    Evento,
)

# What every module gets unless it declares its own: its facts changed, it was switched on, or a date it
# asked for arrived.
SUSCRIPCION_MINIMA: Final = frozenset({PERFIL_CAMBIADO, MODULO_ACTIVADO, FECHA_UMBRAL})

SUSCRIPCIONES_POR_DEFECTO: Final[dict[str, frozenset[str]]] = {
    # The tax jobs: profile changes, deadlines entering their notice window, and new tax law.
    "fiscal": frozenset({PERFIL_CAMBIADO, FECHA_UMBRAL, NORMA_BOE, CAMBIO_NORMATIVO}),
    "luz": SUSCRIPCION_MINIMA,
    "caducidades": SUSCRIPCION_MINIMA,
    "ayudas": SUSCRIPCION_MINIMA,
    # A statement import changes the detected charges: renewals and price rises are recomputed at once.
    "suscripciones": SUSCRIPCION_MINIMA | {CARGOS_IMPORTADOS},
    # A new official index moves the estimated instalment of the next review.
    "hipoteca": SUSCRIPCION_MINIMA | {INDICE_PUBLICADO},
}

TODOS_LOS_COMPARTIDOS: Final = "*"
# Shared facts (the core's tax vocabulary) each module reads without declaring them in ``hechos``.
LECTURAS_COMPARTIDAS: Final[dict[str, frozenset[str] | str]] = {
    "fiscal": TODOS_LOS_COMPARTIDOS,
    # Eligibility for aid reads the household (children, disability, region, income…).
    "ayudas": TODOS_LOS_COMPARTIDOS,
    "luz": frozenset({"comunidad_autonoma"}),
    "caducidades": frozenset({"descendientes"}),
}

# Facts whose change means "new charges were imported" rather than a plain profile edit.
HECHOS_DE_CARGOS: Final = frozenset({"suscripciones_servicios"})


def suscripciones_de(modulo: Any) -> frozenset[str]:
    """Event types the module is subscribed to (its own declaration, else the core's default)."""
    declaradas = getattr(modulo, "eventos", None)
    if declaradas:
        return frozenset(str(tipo) for tipo in declaradas)
    return SUSCRIPCIONES_POR_DEFECTO.get(str(modulo.clave), SUSCRIPCION_MINIMA)


def hechos_que_lee(modulo: Any, compartidos: Iterable[str]) -> frozenset[str]:
    """Fact names whose change concerns the module."""
    compartidos = frozenset(compartidos)
    propios = {h.nombre for h in getattr(modulo, "hechos", ()) or ()}
    extra = {str(n) for n in getattr(modulo, "hechos_leidos", ()) or ()}
    lectura = LECTURAS_COMPARTIDAS.get(str(modulo.clave), frozenset())
    leidos = compartidos if lectura == TODOS_LOS_COMPARTIDOS else frozenset(lectura)
    return frozenset(propios | extra | leidos)


def le_afecta(modulo: Any, evento: Evento, compartidos: Iterable[str]) -> bool:
    """Whether ``evento`` should wake ``modulo`` (on/off is checked by the bus, not here)."""
    if evento.tipo not in suscripciones_de(modulo):
        return False
    if evento.tipo in DIRIGIDOS:
        return evento.modulo == str(modulo.clave)
    if evento.tipo in (PERFIL_CAMBIADO, CARGOS_IMPORTADOS):
        return bool(set(evento.hechos) & hechos_que_lee(modulo, compartidos))
    return True


__all__ = [
    "HECHOS_DE_CARGOS",
    "LECTURAS_COMPARTIDAS",
    "SUSCRIPCIONES_POR_DEFECTO",
    "SUSCRIPCION_MINIMA",
    "hechos_que_lee",
    "le_afecta",
    "suscripciones_de",
]

"""What an event is: its type, its scope (one case or every case) and a minimal payload.

An event says that something happened, never what it means for the user: the modules decide that when
they are woken. The payload carries identifiers only — fact NAMES (never their values), a module key, a
BOE id, a date — plus, for public data such as a BOE norm, the public metadata the handler needs. Personal
data stays in the case; a handler that needs it reads the case.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #

# A case's facts changed (any path: PUT/DELETE /cases/{id}/facts, the advisor's registrar_hechos, a module
# tool, a document the user accepted). Payload: {"hechos": [names set or changed], "borrados": [names removed]}.
PERFIL_CAMBIADO: Final = "perfil.cambiado"
# A statement import or upload changed the detected recurring charges. Payload: {"hechos": [names]}.
CARGOS_IMPORTADOS: Final = "cargos.importados"
# A module was switched on / off for the owner of the case. Payload: {"modulo": key}.
MODULO_ACTIVADO: Final = "modulo.activado"
MODULO_DESACTIVADO: Final = "modulo.desactivado"
# A date the module asked to be woken on has arrived (scheduled, never polled). Payload: {"modulo", "fecha"}.
FECHA_UMBRAL: Final = "fecha.umbral"
# Global: the daily BOE watch found a new tax norm of high relevance. Payload: the public novelty
# (``proactive.novedades.Novedad``: BOE id, title, taxes, norms it amends).
NORMA_BOE: Final = "boe.norma_nueva"
# Global: a consolidated law changed and was analysed (``proactive.impacto.CambioNormativo``, public).
CAMBIO_NORMATIVO: Final = "boe.cambio_normativo"
# Global: new official mortgage reference indices (euríbor…) were read from the BOE. Payload: {"boe_ids": [...]}.
INDICE_PUBLICADO: Final = "indice.publicado"
# A mail sync classified new messages of a case (module ``correo``). Payload: {"nuevos": n, "categorias":
# {categoria: n}} — counts only, never a subject, an address or a message id.
CORREO_CLASIFICADO: Final = "correo.clasificado"

TIPOS: Final = frozenset(
    {
        PERFIL_CAMBIADO,
        CARGOS_IMPORTADOS,
        MODULO_ACTIVADO,
        MODULO_DESACTIVADO,
        FECHA_UMBRAL,
        NORMA_BOE,
        CAMBIO_NORMATIVO,
        INDICE_PUBLICADO,
        CORREO_CLASIFICADO,
    }
)
# Events that concern every case (fanned out to the cases whose owner has a subscribed module on).
GLOBALES: Final = frozenset({NORMA_BOE, CAMBIO_NORMATIVO, INDICE_PUBLICADO})
# Events aimed at one module (payload["modulo"]): only that module can be woken.
DIRIGIDOS: Final = frozenset({MODULO_ACTIVADO, MODULO_DESACTIVADO, FECHA_UMBRAL})

# Payloads are small by construction; a bigger one is a bug (or personal data that should not be there).
MAX_PAYLOAD_BYTES: Final = 16_000


class EventoInvalidoError(ValueError):
    """An event that breaks the rules above (unknown type, wrong scope, oversized payload)."""


class Evento(BaseModel):
    """A stored event. ``id`` is the position in the log (consumers keep a cursor over it)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    uid: str
    tipo: str
    # None: a global event (every case).
    case_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @property
    def modulo(self) -> str | None:
        """The module a directed event is for."""
        valor = self.payload.get("modulo")
        return valor if isinstance(valor, str) else None

    @property
    def hechos(self) -> tuple[str, ...]:
        """Fact names a profile event touches (set, changed or removed)."""
        nombres = [*self.payload.get("hechos", ()), *self.payload.get("borrados", ())]
        return tuple(n for n in nombres if isinstance(n, str))


def validar(tipo: str, case_id: str | None, payload: Mapping[str, Any]) -> str:
    """Check an event before it is written; returns its payload as JSON."""
    if tipo not in TIPOS:
        raise EventoInvalidoError(f"tipo de evento desconocido: {tipo!r}")
    if tipo in GLOBALES and case_id is not None:
        raise EventoInvalidoError(f"{tipo} es global: no lleva case_id")
    if tipo not in GLOBALES and not case_id:
        raise EventoInvalidoError(f"{tipo} necesita case_id")
    if tipo in DIRIGIDOS and not isinstance(payload.get("modulo"), str):
        raise EventoInvalidoError(f"{tipo} necesita payload.modulo")
    texto = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str)
    if len(texto.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise EventoInvalidoError(f"payload demasiado grande para {tipo}")
    return texto


__all__ = [
    "CAMBIO_NORMATIVO",
    "CARGOS_IMPORTADOS",
    "CORREO_CLASIFICADO",
    "DIRIGIDOS",
    "FECHA_UMBRAL",
    "GLOBALES",
    "INDICE_PUBLICADO",
    "MODULO_ACTIVADO",
    "MODULO_DESACTIVADO",
    "NORMA_BOE",
    "PERFIL_CAMBIADO",
    "TIPOS",
    "Evento",
    "EventoInvalidoError",
    "validar",
]

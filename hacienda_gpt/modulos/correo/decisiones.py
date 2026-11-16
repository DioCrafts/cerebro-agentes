"""«No me interesa» for a category or a sender, through the shared decision memory (``hacienda_gpt/memoria``).

Ámbitos of this module:

* ``correo:<categoria>`` — the whole category (e.g. ``correo:promocion``, ``correo:viaje``);
* ``correo:<categoria>:<dominio>`` — that category from one sender domain (what the «No me interesa» button
  of one of the module's notices records, see ``memoria.ambitos.ambito_de_aviso``);
* ``correo:remitente:<dominio>`` — everything from one sender domain, EXCEPT phishing warnings: a sender
  you mute can still be impersonated, so a suspected fraud is only held back by a decision about
  ``correo:phishing``.

A decision in force holds the items back from the summary, the search, the proposals and the notices.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
import logging
from typing import Literal

from hacienda_gpt.memoria.modelo import BLOQUEAN_AVISOS, Decision, cubre, evaluar
from hacienda_gpt.memoria.servicio import decisiones_del_caso, registrar
from hacienda_gpt.modulos.correo.almacen import Elemento

logger = logging.getLogger(__name__)

TipoSilencio = Literal["categoria", "remitente"]


def ambito_de(elemento: Elemento) -> str:
    return f"correo:{elemento.categoria}:{elemento.dominio or 'desconocido'}"


def ambito_silencio(tipo: TipoSilencio, valor: str) -> str:
    return f"correo:{valor}" if tipo == "categoria" else f"correo:remitente:{valor}"


def vigentes(db_path: str, case_id: str, hoy: date) -> list[Decision]:
    """Decisions of this module that hold proposals back today (never raises: [] on error)."""
    try:
        decisiones = decisiones_del_caso(db_path, case_id)
    except Exception:  # noqa: BLE001 - the memory must never break the mail module
        logger.exception("Could not read the decisions of case %s", case_id)
        return []
    return [d for d in decisiones if d.modulo == "correo" and d.tipo in BLOQUEAN_AVISOS and evaluar(d, hoy=hoy).vigente]


def suprimido(elemento: Elemento, decisiones: Iterable[Decision]) -> bool:
    propio = ambito_de(elemento)
    remitente = f"correo:remitente:{elemento.dominio}"
    for decision in decisiones:
        if cubre(decision.ambito, propio):
            return True
        if elemento.categoria != "phishing" and elemento.dominio and cubre(decision.ambito, remitente):
            return True
    return False


def silenciar(
    db_path: str, *, case_id: str, tipo: TipoSilencio, valor: str, user_id: str | None, razon: str | None = None
) -> Decision:
    """Record «No me interesa» for a category or a sender domain (button in the module's panel)."""
    return registrar(
        db_path,
        case_id=case_id,
        ambito=ambito_silencio(tipo, valor),
        tipo="rechazo",
        razon=razon or "No me interesa",
        fuente="boton",
        user_id=user_id,
        boton="no_me_interesa",
    ).decision


__all__ = ["TipoSilencio", "ambito_de", "ambito_silencio", "silenciar", "suprimido", "vigentes"]

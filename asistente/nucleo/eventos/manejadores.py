"""Waking one module for one case: run it, store its notices, and work out when to wake it next.

The notices are written exactly as the daily run writes them (same kinds, keys and replacement rules,
through :mod:`hacienda_gpt.proactive.bandeja` and the tax jobs), so a notice created by an event and the
same notice found by the daily run are one and the same row: handlers are idempotent by construction.

The module's hooks are read through ``modulos.ganchos.ganchos_de`` (contract v2, or the v1 adapter); no module
is told apart by its key:

* A module that writes its own notifications (``Ganchos.bandeja``, the tax module) first gets the event through
  ``bandeja.al_evento`` (the tax module notifies new tax law there: a new BOE norm and an analysed change of a
  consolidated law, matched against this case's profile with the same criteria as ever); when that returns None
  its runner runs, and its next wake-up is ``bandeja.proxima_revision(case)`` (for the tax module, from the
  fiscal calendar: the day the next filing deadline enters the 30-day reminder window, or 1 October).
* Any other module: ``eventos.al_evento(evento, contexto)`` when it declares it, else ``avisos(contexto)``; its
  next wake-up is the earliest future ``proxima_revision`` of its notices or of ``eventos.proxima_revision``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
import logging
from typing import Any

from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.eventos.tipos import INDICE_PUBLICADO, Evento
from hacienda_gpt.modulos.contrato import AvisoModulo, ContextoModulo
from hacienda_gpt.modulos.ganchos import Ganchos, ganchos_de, proxima_revision_de_caso
from hacienda_gpt.proactive.store import NotificationStore

logger = logging.getLogger(__name__)

# The reminder window of ``proactive.jobs.recordatorios_de_plazos`` (its default ``dias``; the tax module's).
DIAS_AVISO_PLAZO = 30


class AvisoInvalidoError(RuntimeError):
    """The module returned something that is not a valid notice: a bug in the module (retried, then dead)."""


@dataclass(frozen=True)
class Resultado:
    avisos: int
    proxima_revision: date | None = None


# --------------------------------------------------------------------------- #
# Fresh data before a module runs
# --------------------------------------------------------------------------- #


def _refrescar_indices() -> None:
    """The mortgage module caches the index series in-process: a long-running worker must re-read it."""
    try:
        from hacienda_gpt.modulos.hipoteca import indices
    except Exception:  # the module may be absent or switched off
        return
    limpiar = getattr(getattr(indices, "cargar_serie", None), "cache_clear", None)
    if callable(limpiar):
        limpiar()


REFRESCOS: dict[str, Callable[[], None]] = {INDICE_PUBLICADO: _refrescar_indices}


def proxima_revision_fiscal(case: CaseState, *, hoy: date) -> date | None:
    """The tax module's next wake-up for a case (kept for callers; it lives in ``modulos/fiscal/modulo.py``)."""
    from hacienda_gpt.modulos.fiscal.modulo import proxima_revision

    return proxima_revision(case, hoy=hoy)


# --------------------------------------------------------------------------- #
# Any other module
# --------------------------------------------------------------------------- #


def guardar_avisos(case_id: str, modulo: Any, avisos: Sequence[Any], store: NotificationStore) -> int:
    """Store a module's notices exactly as the daily run does. Invalid ones raise after the valid are kept."""
    from pydantic import ValidationError

    from hacienda_gpt.proactive.bandeja import MAX_AVISOS_POR_MODULO, _notificacion, clave_de

    if len(avisos) > MAX_AVISOS_POR_MODULO:
        logger.error("Module %s returned %d notices; only the first are kept", modulo.clave, len(avisos))
        avisos = list(avisos)[:MAX_AVISOS_POR_MODULO]
    nuevos = 0
    invalidos: list[str] = []
    for aviso in avisos:
        try:
            notificacion = _notificacion(case_id, modulo, aviso)
        except (ValidationError, TypeError, ValueError, ArithmeticError) as exc:
            invalidos.append(f"{type(exc).__name__}: {exc}"[:200])
            continue
        if store.add(notificacion):
            nuevos += 1
            if aviso.sustituye_prefijo:
                store.remove_superseded(
                    case_id, key_prefix=clave_de(modulo.clave, aviso.sustituye_prefijo), keep_key=notificacion.key
                )
    if invalidos:
        raise AvisoInvalidoError(f"el módulo {modulo.clave} devolvió avisos no válidos: {'; '.join(invalidos)}")
    return nuevos


def _proxima(avisos: Sequence[Any], ganchos: Ganchos, contexto: ContextoModulo) -> date | None:
    fechas: list[date] = [
        a.proxima_revision for a in avisos if isinstance(a, AvisoModulo) and isinstance(a.proxima_revision, date)
    ]
    propia = ganchos.eventos.proxima_revision
    if propia is not None:
        valor = propia(contexto)
        if isinstance(valor, date):
            fechas.append(valor)
    futuras = [f for f in fechas if f > contexto.hoy]
    return min(futuras, default=None)


def despertar_modulo(modulo: Any, evento: Evento, case: CaseState, *, hoy: date, store: NotificationStore) -> Resultado:
    """Run ``modulo`` for ``case`` because of ``evento``. Exceptions propagate (the bus retries)."""
    refresco = REFRESCOS.get(evento.tipo)
    if refresco is not None:
        refresco()
    ganchos = ganchos_de(modulo)
    if ganchos.bandeja is not None:
        bandeja = ganchos.bandeja
        if bandeja.al_evento is not None:
            propios = bandeja.al_evento(evento, case, hoy=hoy, store=store)
            if propios is not None:
                return Resultado(avisos=int(propios))
        por_tipo = bandeja.ejecutar(case, hoy=hoy, store=store)
        proxima = bandeja.proxima_revision(case, hoy=hoy) if bandeja.proxima_revision is not None else None
        return Resultado(avisos=sum(int(n) for n in dict(por_tipo).values()), proxima_revision=proxima)

    from hacienda_gpt.modulos.perfil import perfil_de

    contexto = ContextoModulo(perfil=perfil_de(case), hoy=hoy, case_id=case.case_id)
    al_evento = ganchos.eventos.al_evento
    avisos = list(al_evento(evento, contexto) if al_evento is not None else modulo.avisos(contexto))
    creados = guardar_avisos(case.case_id, modulo, avisos, store)
    return Resultado(avisos=creados, proxima_revision=_proxima(avisos, ganchos, contexto))


def proxima_revision_de(modulo: Any, case: CaseState, *, hoy: date) -> date | None:
    """Cheap recomputation for the daily reconciliation: no notices are produced or stored."""
    return proxima_revision_de_caso(modulo, case, hoy=hoy)


__all__ = [
    "DIAS_AVISO_PLAZO",
    "REFRESCOS",
    "AvisoInvalidoError",
    "Resultado",
    "despertar_modulo",
    "guardar_avisos",
    "proxima_revision_de",
    "proxima_revision_fiscal",
]

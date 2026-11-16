"""Waking one module for one case: run it, store its notices, and work out when to wake it next.

The notices are written exactly as the daily run writes them (same kinds, keys and replacement rules,
through :mod:`hacienda_gpt.proactive.bandeja` and the tax jobs), so a notice created by an event and the
same notice found by the daily run are one and the same row: handlers are idempotent by construction.

* The tax module keeps its own runner (``ejecutar_en_bandeja``). It also reacts to new tax law: a new BOE
  norm (``proactive.novedades.notificar_novedad``) and an analysed change of a consolidated law
  (``proactive.impacto.notificar_cambio``), matched against this case's profile with the same criteria
  as ever (``impacto.CRITERIOS``). Its next wake-up is computed from the fiscal calendar: the day the next
  filing deadline enters the 30-day reminder window, or 1 October (the year-end plan).
* Any other module: ``al_evento(evento, contexto)`` when it defines it, else ``avisos(contexto)``; its next
  wake-up is the earliest future ``proxima_revision`` of its notices or of ``proxima_revision(contexto)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import logging
from typing import Any

from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.eventos.tipos import CAMBIO_NORMATIVO, INDICE_PUBLICADO, NORMA_BOE, Evento
from hacienda_gpt.modulos.contrato import AvisoModulo, ContextoModulo
from hacienda_gpt.proactive.store import NotificationStore

logger = logging.getLogger(__name__)

FISCAL = "fiscal"
# The reminder window of ``proactive.jobs.recordatorios_de_plazos`` (its default ``dias``).
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


# --------------------------------------------------------------------------- #
# The tax module
# --------------------------------------------------------------------------- #


def proxima_revision_fiscal(case: CaseState, *, hoy: date) -> date | None:
    """The next day the tax jobs could write something new for this case without any event.

    A filing deadline is reminded when it is at most ``DIAS_AVISO_PLAZO`` days away, so the case is woken
    the day the next one enters that window; the year-end plan starts on 1 October of the case's year.
    """
    from hacienda_gpt.decision.schemas import parse_fiscal_year
    from hacienda_gpt.proactive.jobs import _CIERRE_EJERCICIO_ID, facts_de
    from hacienda_gpt.tax.calendario import proximos_vencimientos

    candidatos: list[date] = []
    for vencimiento in proximos_vencimientos(facts_de(case), hoy=hoy, dias=400):
        if vencimiento.id.split(":", 1)[0] == _CIERRE_EJERCICIO_ID:
            continue  # never reminded on its own: the year-end plan covers it
        entra = vencimiento.fecha - timedelta(days=DIAS_AVISO_PLAZO)
        if entra > hoy:
            candidatos.append(entra)
    inicio_plan = date(hoy.year, 10, 1)
    if parse_fiscal_year(case.tax_period) == hoy.year and hoy < inicio_plan:
        candidatos.append(inicio_plan)
    return min(candidatos, default=None)


def _fiscal(modulo: Any, evento: Evento, case: CaseState, *, hoy: date, store: NotificationStore) -> Resultado:
    if evento.tipo == NORMA_BOE:
        from hacienda_gpt.proactive.novedades import Novedad, notificar_novedad

        novedad = Novedad.model_validate(evento.payload["novedad"])
        return Resultado(avisos=notificar_novedad(novedad, [case], store))
    if evento.tipo == CAMBIO_NORMATIVO:
        from hacienda_gpt.proactive.impacto import CambioNormativo, notificar_cambio

        cambio = CambioNormativo.model_validate(evento.payload["cambio"])
        return Resultado(avisos=notificar_cambio(cambio, [case], store))
    creados = modulo.ejecutar_en_bandeja(case, hoy=hoy, store=store)
    return Resultado(avisos=sum(int(n) for n in dict(creados).values()), proxima_revision=None)


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


def _proxima(avisos: Sequence[Any], modulo: Any, contexto: ContextoModulo) -> date | None:
    fechas: list[date] = [
        a.proxima_revision for a in avisos if isinstance(a, AvisoModulo) and isinstance(a.proxima_revision, date)
    ]
    propia = getattr(modulo, "proxima_revision", None)
    if callable(propia):
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
    if callable(getattr(modulo, "ejecutar_en_bandeja", None)):
        resultado = _fiscal(modulo, evento, case, hoy=hoy, store=store)
        if modulo.clave == FISCAL and evento.tipo not in (NORMA_BOE, CAMBIO_NORMATIVO):
            return Resultado(avisos=resultado.avisos, proxima_revision=proxima_revision_fiscal(case, hoy=hoy))
        return resultado

    from hacienda_gpt.modulos.perfil import perfil_de

    contexto = ContextoModulo(perfil=perfil_de(case), hoy=hoy, case_id=case.case_id)
    al_evento = getattr(modulo, "al_evento", None)
    avisos = list(al_evento(evento, contexto) if callable(al_evento) else modulo.avisos(contexto))
    creados = guardar_avisos(case.case_id, modulo, avisos, store)
    return Resultado(avisos=creados, proxima_revision=_proxima(avisos, modulo, contexto))


def proxima_revision_de(modulo: Any, case: CaseState, *, hoy: date) -> date | None:
    """Cheap recomputation for the daily reconciliation: no notices are produced or stored."""
    if modulo.clave == FISCAL:
        return proxima_revision_fiscal(case, hoy=hoy)
    propia = getattr(modulo, "proxima_revision", None)
    if not callable(propia):
        return None
    from hacienda_gpt.modulos.perfil import perfil_de

    valor = propia(ContextoModulo(perfil=perfil_de(case), hoy=hoy, case_id=case.case_id))
    return valor if isinstance(valor, date) and valor > hoy else None


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

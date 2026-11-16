"""The core's daily proactive run: every module's notices for every case, no LLM involved.

Modules that write their own notifications (``ejecutar_en_bandeja``; the tax module's deadline reminders and
year-end plan, in :mod:`asistente.ambitos.fiscal.avisos`) run first; then the household modules switched on
for each case's owner return their notices through the contract (:mod:`asistente.nucleo.proactive.bandeja`).

The tax jobs lived here before they moved to the tax module: their old names (``recordatorios_de_plazos``,
``plan_fin_de_ejercicio``, ``ejecutar_para_caso``, ``facts_de``…) still resolve from this module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
import logging
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asistente.nucleo.decision.schemas import CaseState, CaseStatus
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.store import NotificationStore

if TYPE_CHECKING:  # the re-exported tax jobs, for type checkers (at run time ``__getattr__`` serves them)
    from asistente.ambitos.fiscal.avisos import _CIERRE_EJERCICIO_ID as _CIERRE_EJERCICIO_ID
    from asistente.ambitos.fiscal.avisos import ejecutar_para_caso as ejecutar_para_caso
    from asistente.ambitos.fiscal.avisos import facts_de as facts_de
    from asistente.ambitos.fiscal.avisos import plan_fin_de_ejercicio as plan_fin_de_ejercicio
    from asistente.ambitos.fiscal.avisos import recordatorios_de_plazos as recordatorios_de_plazos

logger = logging.getLogger(__name__)


def hoy_en_espana() -> date:
    """Today's date in mainland Spain (deadlines are Spanish calendar days)."""
    try:
        return datetime.now(ZoneInfo("Europe/Madrid")).date()
    except ZoneInfoNotFoundError:  # pragma: no cover - only without a tz database
        return datetime.now(UTC).date()


def con_bandeja_propia() -> list[Any]:
    """The modules (operator-enabled) that write their own notifications: ``Ganchos.bandeja`` (contract v2)."""
    from asistente.nucleo.modulos.ganchos import escribe_su_bandeja
    from asistente.nucleo.modulos.registro import registro

    return [modulo for modulo in registro().activos() if escribe_su_bandeja(modulo)]


def ejecutar_bandejas_propias(case: CaseState, *, hoy: date, store: NotificationStore) -> dict[str, int]:
    """Run, for one case, the modules that write their own notifications; notifications created per kind."""
    from asistente.nucleo.modulos.ganchos import ganchos_de

    creadas: dict[str, int] = {}
    for modulo in con_bandeja_propia():
        bandeja = ganchos_de(modulo).bandeja
        assert bandeja is not None
        for tipo, n in bandeja.ejecutar(case, hoy=hoy, store=store).items():
            creadas[tipo] = creadas.get(tipo, 0) + n
    return creadas


def ejecutar_para_todos(
    case_store: SQLiteCaseStateStore,
    store: NotificationStore,
    *,
    hoy: date,
    modulos_de: Callable[[CaseState], Sequence[Any]] | None = None,
    auth: Any | None = None,
    preparar_modulos: bool = False,
) -> dict[str, Any]:
    """Run the modules' own jobs over every case that is not closed. One failing case never stops the batch.

    Modules that write their own notifications (``ejecutar_en_bandeja``: the tax module's deadline reminders and
    year-end plan) run first, for every case; the summary counts each of their kinds (``tipos_en_bandeja``).

    Then the household modules switched on for each case's owner (``modulos_de``; by default the owner's
    choices in ``auth``, or the defaults) write their notices (see :mod:`asistente.nucleo.proactive.bandeja`). The
    summary gains ``modulos`` (notices per module) and ``errores_modulos`` only when some module beyond the
    tax one is on, so a tax-only deployment reports exactly what it always did. ``preparar_modulos`` (the
    ``run-daily`` command) first runs each such module's daily task once (e.g. reading the day's calls for aid).
    """
    from asistente.nucleo.modulos.ganchos import GanchosBandeja, ganchos_de

    propios: list[tuple[Any, GanchosBandeja]] = [
        (modulo, bandeja) for modulo in con_bandeja_propia() if (bandeja := ganchos_de(modulo).bandeja) is not None
    ]
    summary: dict[str, Any] = {
        "hoy": hoy.isoformat(),
        "cases": 0,
        **{tipo: 0 for _modulo, bandeja in propios for tipo in bandeja.tipos},
        "errors": 0,
    }
    for case in case_store.list_all_cases():
        if case.status == CaseStatus.CLOSED:
            continue
        summary["cases"] += 1
        for _modulo, bandeja in propios:
            try:
                created = bandeja.ejecutar(case, hoy=hoy, store=store)
            except Exception:
                logger.exception("Proactive jobs failed for case %s", case.case_id)
                summary["errors"] += 1
                continue
            for tipo, creadas in created.items():
                summary[tipo] = summary.get(tipo, 0) + creadas

    from asistente.nucleo.proactive.bandeja import avisos_de_modulos_para_todos

    def por_defecto(case: CaseState) -> Sequence[Any]:
        from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso

        return modulos_activos_para_caso(case.case_id, auth)

    try:
        modulos = avisos_de_modulos_para_todos(
            case_store, store, hoy=hoy, modulos_de=modulos_de or por_defecto, preparar=preparar_modulos
        )
    except Exception:  # the household modules never take the tax jobs down with them
        logger.exception("Household module notices failed")
        modulos = {"modulos": {}, "errores_modulos": 1}
    if modulos["modulos"] or modulos["errores_modulos"]:
        summary.update(modulos)
    return summary


def __getattr__(name: str) -> Any:
    """The tax jobs' old names (they moved to :mod:`asistente.ambitos.fiscal.avisos`)."""
    if not name.startswith("__"):
        from asistente.ambitos.fiscal import avisos

        if hasattr(avisos, name):
            return getattr(avisos, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "con_bandeja_propia",
    "ejecutar_bandejas_propias",
    "ejecutar_para_caso",
    "ejecutar_para_todos",
    "facts_de",
    "hoy_en_espana",
    "plan_fin_de_ejercicio",
    "recordatorios_de_plazos",
]

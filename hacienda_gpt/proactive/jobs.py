"""The core's daily proactive run: every module's notices for every case, no LLM involved.

Modules that write their own notifications (``ejecutar_en_bandeja``; the tax module's deadline reminders and
year-end plan, in :mod:`hacienda_gpt.modulos.fiscal.avisos`) run first; then the household modules switched on
for each case's owner return their notices through the contract (:mod:`hacienda_gpt.proactive.bandeja`).

The tax jobs lived here before they moved to the tax module: their old names (``recordatorios_de_plazos``,
``plan_fin_de_ejercicio``, ``ejecutar_para_caso``, ``facts_de``…) still resolve from this module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
import logging
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hacienda_gpt.decision.schemas import CaseState, CaseStatus
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.proactive.store import NotificationStore

if TYPE_CHECKING:  # the re-exported tax jobs, for type checkers (at run time ``__getattr__`` serves them)
    from hacienda_gpt.modulos.fiscal.avisos import _CIERRE_EJERCICIO_ID as _CIERRE_EJERCICIO_ID
    from hacienda_gpt.modulos.fiscal.avisos import ejecutar_para_caso as ejecutar_para_caso
    from hacienda_gpt.modulos.fiscal.avisos import facts_de as facts_de
    from hacienda_gpt.modulos.fiscal.avisos import plan_fin_de_ejercicio as plan_fin_de_ejercicio
    from hacienda_gpt.modulos.fiscal.avisos import recordatorios_de_plazos as recordatorios_de_plazos

logger = logging.getLogger(__name__)


def hoy_en_espana() -> date:
    """Today's date in mainland Spain (deadlines are Spanish calendar days)."""
    try:
        return datetime.now(ZoneInfo("Europe/Madrid")).date()
    except ZoneInfoNotFoundError:  # pragma: no cover - only without a tz database
        return datetime.now(UTC).date()


def con_bandeja_propia() -> list[Any]:
    """The modules (operator-enabled) that write their own notifications: ``ejecutar_en_bandeja``."""
    from hacienda_gpt.modulos.registro import registro

    return [modulo for modulo in registro().activos() if callable(getattr(modulo, "ejecutar_en_bandeja", None))]


def ejecutar_bandejas_propias(case: CaseState, *, hoy: date, store: NotificationStore) -> dict[str, int]:
    """Run, for one case, the modules that write their own notifications; notifications created per kind."""
    creadas: dict[str, int] = {}
    for modulo in con_bandeja_propia():
        for tipo, n in modulo.ejecutar_en_bandeja(case, hoy=hoy, store=store).items():
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
    choices in ``auth``, or the defaults) write their notices (see :mod:`hacienda_gpt.proactive.bandeja`). The
    summary gains ``modulos`` (notices per module) and ``errores_modulos`` only when some module beyond the
    tax one is on, so a tax-only deployment reports exactly what it always did. ``preparar_modulos`` (the
    ``run-daily`` command) first runs each such module's daily task once (e.g. reading the day's calls for aid).
    """
    propios = con_bandeja_propia()
    summary: dict[str, Any] = {
        "hoy": hoy.isoformat(),
        "cases": 0,
        **{tipo: 0 for modulo in propios for tipo in getattr(modulo, "tipos_en_bandeja", ())},
        "errors": 0,
    }
    for case in case_store.list_all_cases():
        if case.status == CaseStatus.CLOSED:
            continue
        summary["cases"] += 1
        for modulo in propios:
            try:
                created = modulo.ejecutar_en_bandeja(case, hoy=hoy, store=store)
            except Exception:
                logger.exception("Proactive jobs failed for case %s", case.case_id)
                summary["errors"] += 1
                continue
            for tipo, creadas in created.items():
                summary[tipo] = summary.get(tipo, 0) + creadas

    from hacienda_gpt.proactive.bandeja import avisos_de_modulos_para_todos

    def por_defecto(case: CaseState) -> Sequence[Any]:
        from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso

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
    """The tax jobs' old names (they moved to :mod:`hacienda_gpt.modulos.fiscal.avisos`)."""
    if not name.startswith("__"):
        from hacienda_gpt.modulos.fiscal import avisos

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

"""``POST /planificar`` — the household planner over HTTP, without a language model.

Same access rules as ``/optimize``: API key, session when accounts are on, and ownership of the case (404 for
someone else's case, like one that does not exist). With ``case_id`` the case's facts are the household's
figures; the mortgage and aid data are used only when the user has those modules switched on. Nothing is stored:
the plan is computed and returned. Body-validation errors and bad data come back as a 422 with one Spanish
sentence in ``detail``.

Built by :func:`build_router` with the case store passed in (see ``api.py``), like the other routers.
The module deliberately has no ``from __future__ import annotations`` (FastAPI resolves the local aliases).
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from hacienda_gpt.api.routers.autonomos import _RutaConErroresEnEspanol
from hacienda_gpt.api.security import require_api_key
from hacienda_gpt.auth.deps import CaseAccess, get_case_access
from hacienda_gpt.decision.schemas import parse_fiscal_year
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.modulos.preferencias import modulos_activos_para, modulos_activos_para_caso
from hacienda_gpt.planificador.modelos import EntradaPlan, PlanHogar
from hacienda_gpt.planificador.motor import planificar
from hacienda_gpt.proactive.jobs import hoy_en_espana
from hacienda_gpt.tax.params import EjercicioNoDisponibleError
from hacienda_gpt.tax.profile import DatosInvalidosError


class PlanificarRequest(EntradaPlan):
    """The planner's input plus, optionally, the case whose facts describe the household."""

    case_id: str | None = None


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["planificador"], route_class=_RutaConErroresEnEspanol)
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]

    @router.post("/planificar", response_model=PlanHogar, dependencies=[Depends(require_api_key)])
    def post_planificar(payload: PlanificarRequest, store: case_store_dep, access: access_dep) -> PlanHogar:
        """Deterministic household planner: the best allocation of a sum of money among mortgage repayment,
        pension contributions, a donation and liquidity, with the interactions computed by each module."""
        facts: dict[str, object] = {}
        case = None
        if payload.case_id is not None:
            access.check(payload.case_id)
            case = store.get_case(payload.case_id)
            if case is None:
                raise HTTPException(status_code=404, detail="case not found")
            facts = {fact.name: fact.value for fact in case.facts}
            activos = {m.clave for m in modulos_activos_para_caso(payload.case_id, access.store)}
        else:
            user_id = access.user.id if access.user is not None else None
            activos = {m.clave for m in modulos_activos_para(user_id, access.store)}
        entrada = EntradaPlan.model_validate(payload.model_dump(exclude={"case_id"}, exclude_unset=True))
        if entrada.ejercicio is None and case is not None:
            ano = parse_fiscal_year(case.tax_period)
            if ano is not None:
                entrada = entrada.model_copy(update={"ejercicio": ano})
        try:
            return planificar(entrada, facts=facts, modulos_activos=activos, hoy=hoy_en_espana())
        except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = ["PlanificarRequest", "build_router"]

"""The platform in the API: migrate the cases' database at start-up, and ``GET /plataforma/salud``.

Including this router makes the API bring the schema up to date before it serves (``MIGRACIONES_AUTOMATICAS``,
on by default; the stores also do it for their own tables when they open). The health endpoint is the
operator's (aggregates only: migrations, worker heartbeats, scheduled jobs; no case data).
"""

# No ``from __future__ import annotations``: FastAPI must see the ``Depends`` of a provider that is local
# to ``build_router``.
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from hacienda_gpt.api.security import require_operator
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.plataforma.migraciones import automaticas, migrar
from hacienda_gpt.plataforma.procesos import salud

logger = logging.getLogger(__name__)


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    def _ruta(app: Any) -> str:
        # The store the app really uses (tests override the dependency with a private database).
        proveedor = getattr(app, "dependency_overrides", {}).get(get_case_store, get_case_store)
        return str(proveedor().db_path)

    @asynccontextmanager
    async def ciclo_de_vida(app: Any) -> AsyncIterator[None]:
        if automaticas():
            try:
                informe = migrar(_ruta(app))
                if informe.aplicadas:
                    logger.info("Migrations applied at start-up: %d", len(informe.aplicadas))
            except Exception:  # the stores migrate their own tables on first use: the API still starts
                logger.exception("Start-up migration failed")
        yield

    router = APIRouter(tags=["plataforma"], lifespan=ciclo_de_vida)

    @router.get("/plataforma/salud", dependencies=[Depends(require_operator)])
    def get_salud(cases: Annotated[SQLiteCaseStateStore, Depends(get_case_store)]) -> dict[str, Any]:
        """Pending migrations, worker heartbeats and the last run of each scheduled job."""
        return salud(cases.db_path)

    return router


__all__ = ["build_router"]

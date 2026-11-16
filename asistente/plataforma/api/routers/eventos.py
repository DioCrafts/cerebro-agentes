"""``GET /eventos/salud``: the event bus as the operator sees it (aggregates only, no case data).

Including this router also wires the bus into the API: at start-up the case store's save/delete hooks
are installed (profile changes write their events in the same transaction) and in-process dispatch
starts, so a change wakes its modules within seconds.
"""

# No ``from __future__ import annotations``: FastAPI must see the ``Depends`` of a provider that is local
# to ``build_router``.
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.eventos import despacho, productores
from asistente.nucleo.eventos.bus import metricas_de
from asistente.nucleo.eventos.salud import estado, problemas
from asistente.plataforma.api.security import require_operator

logger = logging.getLogger(__name__)


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    @asynccontextmanager
    async def ciclo_de_vida(_app: Any) -> AsyncIterator[None]:
        productores.instalar()
        try:
            despacho.habilitar(get_case_store().db_path)
        except Exception:  # the API must start even if the bus cannot: the daily run is the safety net
            logger.exception("In-process event dispatch could not start")
        try:
            yield
        finally:
            despacho.detener()

    router = APIRouter(tags=["eventos"], lifespan=ciclo_de_vida)

    @router.get("/eventos/salud", dependencies=[Depends(require_operator)])
    def get_salud(cases: Annotated[SQLiteCaseStateStore, Depends(get_case_store)]) -> dict[str, Any]:
        """Backlog, dead letters, timers and workers of the event bus, and per type and module metrics."""
        salud = estado(cases.db_path)
        metricas: list[dict[str, Any]] = []
        if salud.get("instalado"):
            conn = sqlite3.connect(cases.db_path, timeout=10.0)
            try:
                metricas = metricas_de(conn)
            finally:
                conn.close()
        return {
            "salud": salud,
            "metricas": metricas,
            "problemas": problemas(cases.db_path),
            "despacho_en_proceso": despacho.habilitado(),
        }

    return router


__all__ = ["build_router"]

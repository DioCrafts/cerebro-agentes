"""The brain's proactive pass over HTTP: the structured digest the inbox card (``RepasoCard``) renders.

* ``GET /cases/{case_id}/repaso?notification_id=`` — the pass that wrote that digest notification (without
  ``notification_id``: the case's latest written pass): the headline, the ranked points with their text, their
  «por qué» (the model's sentence and the reasons code computed), their sources (module, title, date) and their
  proposals with their CURRENT state (an action confirmed elsewhere since shows as decided). 404 when there is none.

This router never acts: each proposal is answered through its own existing endpoint (``/acciones/{id}/confirmar``,
``/tareas/{id}/aceptar``, ``/tareas/{id}/pasos/{paso}/confirmar``, ``/correo/propuestas/{id}/confirmar``,
``POST /tareas`` with ``aceptar: false`` for a template), with that endpoint's own checks. Same access rules as
every ``/cases/{id}/…`` endpoint (API key, session, ownership: somebody else's case is a 404).

Built by :func:`build_router` with the case store passed in (see ``api.py``). No ``from __future__ import
annotations``: FastAPI resolves the local aliases.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access, require_case_access


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AmbitoInfo(_Modelo):
    clave: str
    nombre: str


class FuenteInfo(_Modelo):
    tipo: str
    id: str
    modulo: str | None = None
    nombre_modulo: str | None = None
    titulo: str = ""
    fecha: date | None = None
    # The inbox notice it is, when it is one (the card can open it).
    notification_id: str | None = None


class PropuestaInfo(_Modelo):
    id: str
    tipo: str
    ref: str
    paso: str | None = None
    parametros: dict[str, Any] = Field(default_factory=dict)
    objetivo: str | None = None
    etiqueta: str
    resumen: str
    # pendiente | decidida | desconocido (read now from the layer that owns it).
    estado: str = "pendiente"


class PuntoInfo(_Modelo):
    id: str
    rango: int
    titulo: str
    texto: str
    por_que: str
    motivos: list[str] = Field(default_factory=list)
    urgente: bool = False
    gravedad: str = "info"
    vence: date | None = None
    ambitos: list[AmbitoInfo] = Field(default_factory=list)
    fuentes: list[FuenteInfo] = Field(default_factory=list)
    propuestas: list[PropuestaInfo] = Field(default_factory=list)
    # «modelo»: written by the model and accepted by the gate; «determinista»: written by code.
    origen: str = "determinista"


class RepasoInfo(_Modelo):
    case_id: str
    notification_id: str | None
    periodo: str
    frecuencia: str
    origen: str | None
    titular: str
    puntos: list[PuntoInfo]
    restantes: int = 0
    actualizado: datetime


class RepasoResponse(_Modelo):
    repaso: RepasoInfo


def _respuesta(pasada: Any, db_path: str) -> RepasoInfo:
    from asistente.nucleo.repaso.servicio import estado_propuestas, nombres_de_ambitos

    nombres = nombres_de_ambitos()
    redaccion = pasada.redaccion
    items = {i.punto_id: i for i in (redaccion.items if redaccion is not None else [])}
    todas = [p for punto in pasada.prioridad.puntos for p in punto.propuestas]
    estados = estado_propuestas(db_path, pasada.case_id, todas)
    puntos: list[PuntoInfo] = []
    orden = [i.punto_id for i in redaccion.items] if redaccion is not None else []
    por_id = {p.id: p for p in pasada.prioridad.puntos}
    for punto_id in orden or list(por_id):
        punto = por_id[punto_id]
        item = items.get(punto_id)
        puntos.append(
            PuntoInfo(
                id=punto.id,
                rango=punto.rango,
                titulo=punto.titulo,
                texto=item.texto if item is not None else "",
                por_que=item.por_que if item is not None else "",
                motivos=list(punto.por_que),
                urgente=punto.urgente,
                gravedad=punto.gravedad,
                vence=punto.vence,
                ambitos=[AmbitoInfo(clave=a, nombre=nombres.get(a, a)) for a in punto.ambitos],
                fuentes=[
                    FuenteInfo(
                        tipo=s.fuente.tipo,
                        id=s.fuente.id,
                        modulo=s.fuente.modulo,
                        nombre_modulo=nombres.get(s.fuente.modulo or s.ambito, s.fuente.modulo or s.ambito),
                        titulo=s.fuente.titulo or s.titulo,
                        fecha=s.fuente.fecha,
                        notification_id=s.fuente.id if s.fuente.tipo == "aviso" else None,
                    )
                    for s in punto.senales
                ],
                propuestas=[
                    PropuestaInfo(
                        id=p.id,
                        tipo=p.tipo,
                        ref=p.ref,
                        paso=p.paso,
                        parametros=dict(p.parametros),
                        objetivo=p.objetivo,
                        etiqueta=p.etiqueta,
                        resumen=p.resumen,
                        estado=estados.get(p.id, "desconocido"),
                    )
                    for p in punto.propuestas
                ],
                origen=item.origen if item is not None else "determinista",
            )
        )
    return RepasoInfo(
        case_id=pasada.case_id,
        notification_id=pasada.notification_id,
        periodo=pasada.periodo,
        frecuencia=pasada.frecuencia,
        origen=pasada.origen,
        titular=redaccion.titular if redaccion is not None else "",
        puntos=puntos,
        restantes=pasada.prioridad.restantes,
        actualizado=pasada.actualizada,
    )


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["repaso"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key), Depends(require_case_access)]

    @router.get("/cases/{case_id}/repaso", response_model=RepasoResponse, dependencies=guarded)
    def get_repaso(
        case_id: str, cases: case_store_dep, access: access_dep, notification_id: str | None = None
    ) -> RepasoResponse:
        from asistente.nucleo.repaso.almacen import RepasoStore, tabla_existe

        access.check(case_id)
        if cases.get_case(case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        if not tabla_existe(cases.db_path):
            raise HTTPException(status_code=404, detail="No hay ningún repaso de este caso.")
        almacen = RepasoStore(cases.db_path)
        if notification_id is not None:
            pasada = almacen.por_notificacion(case_id, notification_id)
        else:
            pasada = next((p for p in almacen.de_caso(case_id) if p.estado == "aplicada"), None)
        if pasada is None or pasada.estado != "aplicada":
            raise HTTPException(status_code=404, detail="No hay ningún repaso de este caso.")
        return RepasoResponse(repaso=_respuesta(pasada, cases.db_path))

    return router


__all__ = ["PuntoInfo", "RepasoInfo", "RepasoResponse", "build_router"]

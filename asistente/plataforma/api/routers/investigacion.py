"""«Investigar»: research with sources over HTTP (``asistente/capacidades/investigacion``).

* ``GET  /investigacion/estado`` — which sources this server offers (corpus, BOE, web), whether the web is on
  for the operator and consented by the user, the allow / block lists, the caps, and the privacy note.
* ``POST /investigacion/consentimiento`` — the user gives or withdraws consent to the web search
  (withdrawing also drops their cached reports that used the web).
* ``POST /investigacion`` — run a research (or return today's cached report): the report with the answer,
  the claims and their literal quotes, the sources table, conflicts, confidence, what could not be verified
  and the cost. It goes through the capability contract (``pedir``), as any domain's request does.
* ``GET  /investigacion/exportar`` and ``DELETE /investigacion/datos`` — everything the capability keeps about
  the user (consents, cached reports), and its erasure.

Access: API key; with accounts on, a session (the user is the session's); an optional ``case_id`` (to charge the
model calls to that case) must be the user's. Errors carry one Spanish sentence in ``detail``. No
``from __future__ import annotations``: FastAPI resolves the local aliases.
"""

import functools
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from asistente.capacidades.contrato import ContextoPeticion, PeticionDenegada, pedir
from asistente.capacidades.investigacion import capacidad
from asistente.capacidades.investigacion.ajustes import LIMITES, PRECIO_BUSQUEDA_FUENTE, ajustes
from asistente.capacidades.investigacion.almacen import VERSION_TEXTO_CONSENTIMIENTO, AlmacenInvestigacion
from asistente.capacidades.investigacion.modelo import Informe, PeticionInvestigacion
from asistente.capacidades.investigacion.web import NO_DISPONIBLE_OPENAI
from asistente.nucleo.proactive.jobs import hoy_en_espana
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access

logger = logging.getLogger(__name__)

PRIVACIDAD = (
    "Lo que escribes en la pregunta se envía al proveedor del modelo de IA para planificar la búsqueda y redactar "
    "la respuesta y, si activas la web, también a su buscador. No se envían los datos de tu expediente: no los "
    "incluyas en la pregunta si no quieres que salgan. Las páginas encontradas se tratan como datos, nunca como "
    "instrucciones, y cada afirmación se comprueba contra el texto literal de su fuente."
)

router = APIRouter(tags=["investigacion"], dependencies=[Depends(require_api_key)])


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigarRequest(PeticionInvestigacion):
    case_id: str | None = Field(default=None, max_length=200)


class ConsentimientoRequest(_M):
    concedido: bool


class LimitesInfo(_M):
    max_usd: str
    max_tokens: int
    llamadas_por_profundidad: dict[str, int]
    precio_busqueda_web_usd: str
    precio_busqueda_web_fuente: str


class EstadoInvestigacion(_M):
    fuentes: dict[str, bool]
    web_activada_por_el_servicio: bool
    web_consentida: bool
    web_proveedor_compatible: bool
    web_motivo: str | None = None
    dominios_permitidos: list[str]
    dominios_bloqueados: list[str]
    limites: LimitesInfo
    privacidad: str
    version_texto_consentimiento: str


def get_almacen() -> AlmacenInvestigacion:
    try:
        almacen = capacidad.almacen_por_defecto()
    except Exception as exc:
        logger.error("research store unavailable (%s)", type(exc).__name__)
        almacen = None
    if almacen is None:
        raise HTTPException(status_code=503, detail="El almacén de la investigación no está disponible.")
    return almacen


@functools.lru_cache(maxsize=1)
def _proveedor() -> Any | None:
    from asistente.nucleo.agent.providers import create_provider
    from asistente.plataforma.ops.consumo import medir

    try:
        return medir(create_provider(), "investigacion:sintetizar")
    except Exception as exc:  # no model configured: the research lists sources without an answer
        logger.warning("research without a model (%s)", type(exc).__name__)
        return None


def get_proveedor() -> Any | None:
    """The LLM provider for the research (overridable in tests); None when none is configured."""
    return _proveedor()


def _usuario(access: CaseAccess) -> str | None:
    return access.user.id if access.user is not None else None


def _estado(almacen: AlmacenInvestigacion, user_id: str | None, proveedor: Any | None) -> EstadoInvestigacion:
    try:
        aj = ajustes()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    nombre = str(getattr(proveedor, "name", "") or "").lower()
    compatible = nombre in ("anthropic", "claude")
    motivo = None
    if not aj.web:
        motivo = "La búsqueda web está desactivada en este servicio."
    elif proveedor is None:
        motivo = "No hay modelo de IA configurado: la búsqueda web lo necesita."
    elif not compatible:
        motivo = NO_DISPONIBLE_OPENAI
    investigador = capacidad.investigador_construido()
    return EstadoInvestigacion(
        fuentes={
            # Until the first research builds it, the corpus is assumed present when an index is configured.
            "corpus": investigador.searcher is not None if investigador is not None else True,
            "boe": investigador.boe is not None if investigador is not None else True,
            "web": aj.web and compatible,
        },
        web_activada_por_el_servicio=aj.web,
        web_consentida=almacen.consentimiento_web(user_id),
        web_proveedor_compatible=compatible,
        web_motivo=motivo,
        dominios_permitidos=list(aj.permitidos_efectivos()),
        dominios_bloqueados=list(aj.bloqueados),
        limites=LimitesInfo(
            max_usd=str(aj.max_usd),
            max_tokens=aj.max_tokens,
            llamadas_por_profundidad={clave: lim.llamadas for clave, lim in LIMITES.items()},
            precio_busqueda_web_usd=str(aj.precio_busqueda_usd),
            precio_busqueda_web_fuente=PRECIO_BUSQUEDA_FUENTE,
        ),
        privacidad=PRIVACIDAD,
        version_texto_consentimiento=VERSION_TEXTO_CONSENTIMIENTO,
    )


AccessDep = Annotated[CaseAccess, Depends(get_case_access)]
AlmacenDep = Annotated[AlmacenInvestigacion, Depends(get_almacen)]
ProveedorDep = Annotated[Any, Depends(get_proveedor)]


@router.get("/investigacion/estado", response_model=EstadoInvestigacion)
def estado(access: AccessDep, almacen: AlmacenDep, proveedor: ProveedorDep) -> EstadoInvestigacion:
    return _estado(almacen, _usuario(access), proveedor)


@router.post("/investigacion/consentimiento", response_model=EstadoInvestigacion)
def consentimiento(
    body: ConsentimientoRequest, access: AccessDep, almacen: AlmacenDep, proveedor: ProveedorDep
) -> EstadoInvestigacion:
    almacen.registrar_consentimiento(_usuario(access), body.concedido)
    return _estado(almacen, _usuario(access), proveedor)


@router.post("/investigacion", response_model=Informe)
def investigar(body: InvestigarRequest, access: AccessDep, proveedor: ProveedorDep) -> Informe:
    access.check(body.case_id)
    argumentos = body.model_dump(exclude={"case_id"}, exclude_none=True)
    contexto = ContextoPeticion(
        ambito="nucleo",
        hoy=hoy_en_espana(),
        case_id=body.case_id,
        user_id=_usuario(access),
        proveedor=proveedor,
        origen="usuario",
    )
    try:
        resultado = pedir(capacidad.CLAVE, "investigar", argumentos, contexto)
    except PeticionDenegada as exc:
        codigo = 422 if exc.motivo == "entrada" else 403 if exc.motivo in ("consentimiento", "ambito") else 503
        raise HTTPException(status_code=codigo, detail=str(exc)) from exc
    except ValueError as exc:  # an invalid setting (domain list, number)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Informe.model_validate(dict(resultado.datos))


@router.get("/investigacion/exportar")
def exportar(access: AccessDep, almacen: AlmacenDep) -> dict[str, Any]:
    return almacen.exportar(_usuario(access))


@router.delete("/investigacion/datos")
def borrar(access: AccessDep, almacen: AlmacenDep) -> dict[str, int]:
    return {"borrados": almacen.borrar_usuario(_usuario(access))}


__all__ = ["PRIVACIDAD", "get_almacen", "get_proveedor", "router"]

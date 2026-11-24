"""The research capability under the capability contract (``asistente/capacidades/contrato.py``).

One operation, ``investigar`` (effect ``lectura``: it reads public sources and returns a report; there is no
write or act operation). Its result quotes third-party text, so :func:`~asistente.capacidades.contrato.pedir`
wraps the text for a model as untrusted data. Consent: ``ninguno`` at the capability level — the corpus and
the BOE only process the question the caller hands over, like any question to the assistant — while the WEB
part needs the operator's switch AND the user's own consent, kept by this capability (``almacen.py``) and
checked on every research.

The :class:`~asistente.capacidades.investigacion.servicio.Investigador` that serves the operation is built
once from the settings (:func:`investigador_por_defecto`); the app or the tests can replace it
(:func:`configurar`).
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Any

from asistente.capacidades.contrato import (
    CapacidadBase,
    ContextoPeticion,
    OperacionCapacidad,
    ResultadoCapacidad,
)
from asistente.capacidades.investigacion.ajustes import ajustes
from asistente.capacidades.investigacion.almacen import AlmacenInvestigacion
from asistente.capacidades.investigacion.fuentes import ClienteBOE
from asistente.capacidades.investigacion.modelo import PROFUNDIDADES, TIPOS_FUENTE, PeticionInvestigacion
from asistente.capacidades.investigacion.servicio import Investigador, texto_informe
from asistente.capacidades.investigacion.sintesis import PROPOSITO as PROPOSITO_SINTESIS
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

CLAVE = "investigacion"

ENTRADA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pregunta": {"type": "string", "minLength": 5, "maxLength": 1000},
        "ambito": {"type": ["string", "null"], "maxLength": 60},
        "fuentes": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": list(TIPOS_FUENTE)}},
        "profundidad": {"type": "string", "enum": list(PROFUNDIDADES)},
        "solo_oficiales": {"type": "boolean"},
    },
    "required": ["pregunta"],
}

SALIDA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pregunta": {"type": "string"},
        "respuesta": {"type": "string"},
        "afirmaciones": {"type": "array"},
        "fuentes": {"type": "array"},
        "conflictos": {"type": "array"},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "no_verificado": {"type": "array", "items": {"type": "string"}},
        "avisos": {"type": "array", "items": {"type": "string"}},
        "web": {"type": "object"},
        "coste": {"type": "object"},
        "desde_cache": {"type": "boolean"},
    },
    "required": ["pregunta", "respuesta", "afirmaciones", "fuentes", "confianza", "coste", "web"],
}

_lock = threading.RLock()
_investigador: list[Investigador] = []
_almacenes: dict[str, AlmacenInvestigacion] = {}


def almacen_por_defecto() -> AlmacenInvestigacion | None:
    """The store in use: the configured researcher's, else the settings' file (opened once per path).

    On first open, with accounts on, the rows of accounts deleted since are erased.
    """
    with _lock:
        if _investigador:
            return _investigador[0].almacen
        ruta = ajustes().db_path
        almacen = _almacenes.get(ruta)
        if almacen is None:
            almacen = _almacenes[ruta] = AlmacenInvestigacion(ruta)
            if settings.AUTH_MODE == "required":
                try:
                    almacen.purgar_cuentas_borradas(settings.DECISION_STATE_DB_PATH)
                except Exception:  # never block a research over housekeeping
                    logger.warning("could not purge research data of deleted accounts", exc_info=True)
        return almacen


def _construir() -> Investigador:
    almacen = almacen_por_defecto()
    searcher = None
    try:
        from asistente.nucleo.agent.search import buscador_del_corpus

        searcher = buscador_del_corpus()
    except Exception as exc:  # no index on this server: the corpus is reported as unavailable
        logger.warning("research: corpus unavailable (%s)", type(exc).__name__)
    grafo = None
    try:
        if Path(settings.DOCTRINE_GRAPH_PATH).exists():
            from asistente.conocimiento.doctrine.grafo import DoctrineGraph

            grafo = DoctrineGraph(settings.DOCTRINE_GRAPH_PATH)
    except Exception:
        logger.warning("research: doctrine graph unavailable", exc_info=True)
    return Investigador(searcher=searcher, boe=ClienteBOE(), grafo=grafo, almacen=almacen)


def investigador_por_defecto() -> Investigador:
    """The process-wide researcher (built on first use from the settings)."""
    if _investigador:
        return _investigador[0]
    with _lock:
        if not _investigador:
            _investigador.append(_construir())
        return _investigador[0]


def investigador_construido() -> Investigador | None:
    """The process-wide researcher if it has been built or configured already (never builds it)."""
    return _investigador[0] if _investigador else None


def configurar(investigador: Investigador | None) -> None:
    """Replace the process-wide researcher (None: build it again from the settings on next use)."""
    with _lock:
        _investigador.clear()
        _almacenes.clear()
        if investigador is not None:
            _investigador.append(investigador)


def usuario_del_contexto(contexto: ContextoPeticion) -> str | None:
    """The user on whose behalf the research runs: ``user_id``, else the owner of the case (accounts on)."""
    if contexto.user_id is not None:
        return contexto.user_id
    if contexto.case_id is None or settings.AUTH_MODE != "required":
        return None
    try:
        from asistente.plataforma.auth.deps import get_auth_store

        dueno = get_auth_store().owner_of(contexto.case_id)
    except Exception:
        logger.warning("could not read the owner of a case", exc_info=True)
        return None
    return str(dueno) if dueno is not None else None


def _investigar(args: Any, contexto: ContextoPeticion) -> ResultadoCapacidad:
    peticion = PeticionInvestigacion.model_validate(dict(args))
    informe = investigador_por_defecto().investigar(
        peticion, proveedor=contexto.proveedor, user_id=usuario_del_contexto(contexto)
    )
    citadas = [f for f in informe.fuentes if f.citada]
    return ResultadoCapacidad(
        datos=informe.model_dump(mode="json"),
        resumen=(
            f"Investigación: {len(informe.afirmaciones)} afirmación(es) verificada(s), {len(informe.fuentes)} "
            f"fuente(s), confianza {informe.confianza}{' (caché)' if informe.desde_cache else ''}"
        ),
        citas=tuple(f"{f.id}: {f.referencia or f.url or f.titulo}" for f in citadas),
        avisos=tuple(informe.avisos),
        texto_para_modelo=texto_informe(informe),
    )


CAPACIDAD = CapacidadBase(
    clave=CLAVE,
    nombre="Investigar con fuentes",
    descripcion=(
        "Responde preguntas que no cubren los datos de la aplicación buscando en el corpus legal, en el BOE y, si "
        "lo activas, en la web; cada afirmación lleva una cita literal comprobada de su fuente."
    ),
    consentimiento="ninguno",
    operaciones=(
        OperacionCapacidad(
            clave="investigar",
            descripcion=(
                "Investiga una pregunta con fuentes (corpus legal, BOE y, con consentimiento, web) y devuelve un "
                "informe: respuesta, afirmaciones con citas literales, tabla de fuentes, conflictos, confianza, lo "
                "no verificado y el coste."
            ),
            entrada=ENTRADA,
            salida=SALIDA,
            ejecutar=_investigar,
            efecto="lectura",
            proposito=PROPOSITO_SINTESIS,
            datos_de_terceros=True,
        ),
    ),
)


def _almacen() -> AlmacenInvestigacion | None:
    """The store in use, or the settings' file when it exists (never builds the corpus searcher)."""
    if _investigador or ajustes().db_path in _almacenes or Path(ajustes().db_path).exists():
        return almacen_por_defecto()
    return None


def exportar_usuario(user_id: str | None) -> dict[str, Any]:
    """Everything the capability keeps about a user (for «descargar mis datos»)."""
    almacen = _almacen()
    return almacen.exportar(user_id) if almacen is not None else {"consentimientos_web": [], "informes": []}


def borrar_usuario(user_id: str | None) -> int:
    """Erase everything the capability keeps about a user (account deletion). Returns the rows deleted."""
    almacen = _almacen()
    return almacen.borrar_usuario(user_id) if almacen is not None else 0


__all__ = [
    "CAPACIDAD",
    "CLAVE",
    "ENTRADA",
    "almacen_por_defecto",
    "SALIDA",
    "borrar_usuario",
    "configurar",
    "exportar_usuario",
    "investigador_construido",
    "investigador_por_defecto",
    "usuario_del_contexto",
]

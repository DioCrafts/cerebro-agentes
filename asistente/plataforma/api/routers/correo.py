"""Mail module (Gmail, read-only): connect, sync, summary, proposals the user confirms (``modulos/correo``).

* ``GET /correo/estado?case_id=`` — whether the operator configured it, the connection (account, scope, last
  sync, error) and how many items and pending proposals there are.
* ``POST /correo/conexion`` — start the OAuth flow (PKCE): returns Google's consent URL.
* ``GET /correo/oauth/callback`` — Google's redirect. No session header is possible here: the single-use
  ``state`` issued to the case owner is the credential. Redirects to ``CORREO_URL_RETORNO``.
* ``POST /correo/desconectar`` — revoke at Google and delete the token (optionally the derived data too).
* ``POST /correo/sincronizar`` — incremental sync now.
* ``GET /correo/resumen?case_id=&periodo=hoy|semana`` — the summary (derived items only).
* ``GET /correo/propuestas?case_id=`` and ``POST /correo/propuestas/{id}/confirmar|rechazar`` — data for
  other modules: written only when the owner confirms here, through the destination module's own tool or
  validator, once.
* ``POST /correo/silenciar`` — «No me interesa» for a category or a sender (decision memory).
* ``GET|POST|DELETE /correo/contactos`` — pinned contacts (their full address is kept on their items).
* ``GET /correo/exportar?case_id=`` — everything stored by the module for the case (without the token).

Same access rules as ``/cases/{id}/…``: another user's case reads as missing. Built by :func:`build_router`
with the stores passed in (see ``api.py``). No ``from __future__ import annotations`` (FastAPI).
"""

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Annotated, Any, Final, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from asistente.capacidades.correo import HECHO_VENTANA, almacen, cifrado, decisiones, oauth, propuestas
from asistente.capacidades.correo import resumen as resumenes
from asistente.capacidades.correo.clasificacion import CATEGORIAS
from asistente.capacidades.correo.config import SCOPES_FASE_1, configuracion
from asistente.capacidades.correo.http import CorreoError
from asistente.capacidades.correo.sincronizacion import sincronizar
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.memoria.servicio import DecisionInvalidaError
from asistente.nucleo.modulos.contrato import Modulo
from asistente.nucleo.modulos.perfil import perfil_de, registrar_hechos_de_modulo
from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso
from asistente.nucleo.proactive.jobs import hoy_en_espana
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import CaseAccess, get_case_access

logger = logging.getLogger(__name__)

CLAVE = "correo"
CONSENTIMIENTO_LLM: Final = "envio_documentos_a_proveedor_llm"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CasoRequest(_M):
    case_id: str = Field(min_length=1, max_length=200)


class DesconectarRequest(CasoRequest):
    borrar_datos: bool = False


class SilenciarRequest(CasoRequest):
    tipo: Literal["categoria", "remitente"]
    valor: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9_.\-]+$")


class ContactoRequest(CasoRequest):
    direccion: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


class EstadoCorreo(_M):
    case_id: str
    configurado: bool
    activo: bool
    conectado: bool
    estado: str | None = None
    cuenta: str | None = None
    alcance: list[str] = Field(default_factory=list)
    conectada_el: datetime | None = None
    ultima_sincronizacion: datetime | None = None
    ultimo_error: str | None = None
    elementos: int = 0
    propuestas_pendientes: int = 0
    ventana_dias: int
    solo_lectura: bool = True


class ConexionResponse(_M):
    url: str


class DesconectarResponse(_M):
    revocado_en_google: bool | None
    datos_borrados: int


class SincronizacionResponse(_M):
    modo: str
    leidos: int
    nuevos: int
    completos: int
    categorias: dict[str, int]
    propuestas: int
    purgados: int


class PropuestasResponse(_M):
    case_id: str
    propuestas: list[almacen.Propuesta]


class ContactosResponse(_M):
    case_id: str
    contactos: list[str]


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter()
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    guarded = [Depends(require_api_key)]

    def _caso(case_id: str, access: CaseAccess, cases: SQLiteCaseStateStore) -> Any:
        access.check(case_id)
        case = cases.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    def _activos(case_id: str, access: CaseAccess) -> dict[str, Modulo]:
        return {m.clave: m for m in modulos_activos_para_caso(case_id, access.store)}

    def _exigir_activo(case_id: str, access: CaseAccess) -> dict[str, Modulo]:
        activos = _activos(case_id, access)
        if CLAVE not in activos:
            raise HTTPException(status_code=409, detail="Activa primero el módulo «Correo» en «Módulos».")
        return activos

    def _user_id(access: CaseAccess) -> str | None:
        return access.user.id if access.user is not None else None

    @router.get("/correo/estado", response_model=EstadoCorreo, dependencies=guarded)
    def get_estado(case_id: str, cases: case_store_dep, access: access_dep) -> EstadoCorreo:
        case = _caso(case_id, access, cases)
        conf = configuracion()
        conexion = almacen.conexion_de(case_id, cases.db_path)
        ventana = perfil_de(case).get(HECHO_VENTANA)
        return EstadoCorreo(
            case_id=case_id,
            configurado=conf.completa and cifrado.disponible(),
            activo=CLAVE in _activos(case_id, access),
            conectado=conexion is not None,
            estado=conexion.estado if conexion else None,
            cuenta=conexion.cuenta if conexion else None,
            alcance=conexion.alcance if conexion else [],
            conectada_el=conexion.conectada_el if conexion else None,
            ultima_sincronizacion=conexion.ultima_sincronizacion if conexion else None,
            ultimo_error=conexion.ultimo_error if conexion else None,
            elementos=len(almacen.ids_guardados(case_id, cases.db_path)),
            propuestas_pendientes=len(almacen.propuestas(case_id, estado="pendiente", db_path=cases.db_path)),
            ventana_dias=int(ventana) if isinstance(ventana, int | float) else conf.ventana_dias,
        )

    @router.post("/correo/conexion", response_model=ConexionResponse, dependencies=guarded)
    def post_conexion(payload: CasoRequest, cases: case_store_dep, access: access_dep) -> ConexionResponse:
        _caso(payload.case_id, access, cases)
        _exigir_activo(payload.case_id, access)
        try:
            url = oauth.iniciar(payload.case_id, _user_id(access), db_path=cases.db_path)
        except (CorreoError, cifrado.CifradoNoDisponibleError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        cases.append_audit_event(
            payload.case_id, {"event_type": "correo_conexion_iniciada", "alcance": list(SCOPES_FASE_1)}
        )
        return ConexionResponse(url=url)

    @router.get("/correo/oauth/callback", include_in_schema=False)
    def get_callback(cases: case_store_dep, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
        """Google's redirect. Authenticated by the single-use ``state`` (no session header on a redirect)."""
        if not state:  # the state IS the credential of this endpoint: without it, nobody is asking
            raise HTTPException(status_code=401, detail="Falta la autorización (state) de la solicitud de conexión.")
        conf = configuracion()
        destino = conf.url_retorno
        separador = "&" if "?" in destino else "?"
        if error or not code:
            almacen.consumir_estado_oauth(state[:200], cases.db_path)  # the request is used up either way
            motivo = "cancelado" if error == "access_denied" else "error"
            return RedirectResponse(f"{destino}{separador}{urlencode({'correo': motivo})}", status_code=303)
        try:
            conexion = oauth.completar(state[:200], code[:2000], db_path=cases.db_path)
        except (CorreoError, cifrado.CifradoNoDisponibleError) as exc:
            logger.warning("Gmail connection could not be completed: %s", type(exc).__name__)
            return RedirectResponse(f"{destino}{separador}{urlencode({'correo': 'error'})}", status_code=303)
        cases.append_audit_event(conexion.case_id, {"event_type": "correo_conectado", "alcance": conexion.alcance})
        return RedirectResponse(f"{destino}{separador}{urlencode({'correo': 'conectado'})}", status_code=303)

    @router.post("/correo/desconectar", response_model=DesconectarResponse, dependencies=guarded)
    def post_desconectar(payload: DesconectarRequest, cases: case_store_dep, access: access_dep) -> DesconectarResponse:
        _caso(payload.case_id, access, cases)
        revocado = oauth.desconectar(payload.case_id, db_path=cases.db_path)
        borrados = almacen.borrar_datos(payload.case_id, cases.db_path) if payload.borrar_datos else 0
        cases.append_audit_event(
            payload.case_id,
            {"event_type": "correo_desconectado", "revocado_en_google": revocado, "datos_borrados": borrados},
        )
        return DesconectarResponse(revocado_en_google=revocado, datos_borrados=borrados)

    @router.post("/correo/sincronizar", response_model=SincronizacionResponse, dependencies=guarded)
    def post_sincronizar(payload: CasoRequest, cases: case_store_dep, access: access_dep) -> SincronizacionResponse:
        case = _caso(payload.case_id, access, cases)
        _exigir_activo(payload.case_id, access)
        permitir_llm = access.user is None or (
            access.store is not None and access.store.has_consent(access.user.id, CONSENTIMIENTO_LLM)
        )
        ventana = perfil_de(case).get(HECHO_VENTANA)
        try:
            resultado = sincronizar(
                payload.case_id,
                db_path=cases.db_path,
                hoy=hoy_en_espana(),
                ventana_dias=int(ventana) if isinstance(ventana, int | float) else None,
                permitir_llm=permitir_llm,
            )
        except oauth.ConexionCaducadaError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (CorreoError, cifrado.CifradoNoDisponibleError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        cases.append_audit_event(
            payload.case_id,
            {"event_type": "correo_sincronizado", "modo": resultado.modo, "nuevos": resultado.nuevos},
        )
        return SincronizacionResponse(**resultado.__dict__)

    @router.get("/correo/resumen", dependencies=guarded)
    def get_resumen(
        case_id: str, cases: case_store_dep, access: access_dep, periodo: Literal["hoy", "semana"] = "semana"
    ) -> dict[str, Any]:
        _caso(case_id, access, cases)
        hoy = hoy_en_espana()
        vigentes = decisiones.vigentes(cases.db_path, case_id, hoy)
        resumen = resumenes.construir(
            almacen.elementos(case_id, db_path=cases.db_path), hoy=hoy, periodo=periodo, decisiones=vigentes
        )
        return resumenes.resumen_dict(resumen)

    @router.get("/correo/propuestas", response_model=PropuestasResponse, dependencies=guarded)
    def get_propuestas(case_id: str, cases: case_store_dep, access: access_dep) -> PropuestasResponse:
        _caso(case_id, access, cases)
        return PropuestasResponse(case_id=case_id, propuestas=almacen.propuestas(case_id, db_path=cases.db_path))

    def _propuesta(propuesta_id: str, access: CaseAccess, cases: SQLiteCaseStateStore) -> almacen.Propuesta:
        propuesta = almacen.propuesta(propuesta_id, cases.db_path)
        if propuesta is None:
            raise HTTPException(status_code=404, detail="propuesta no encontrada")
        try:
            access.check(propuesta.case_id)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="propuesta no encontrada") from exc
        return propuesta

    @router.post("/correo/propuestas/{propuesta_id}/confirmar", response_model=almacen.Propuesta, dependencies=guarded)
    def post_confirmar(propuesta_id: str, cases: case_store_dep, access: access_dep) -> almacen.Propuesta:
        """The owner confirms: the destination module's own tool or validator writes the data, once."""
        propuesta = _propuesta(propuesta_id, access, cases)
        if propuesta.estado != "pendiente":
            raise HTTPException(status_code=409, detail="Esta propuesta ya se decidió.")
        case = _caso(propuesta.case_id, access, cases)
        activos = _exigir_activo(propuesta.case_id, access)
        destino = activos.get(propuesta.destino)
        if propuesta.tipo != "nota" and destino is None:
            nombre = propuestas.NOMBRES_DESTINO.get(propuesta.destino, propuesta.destino)
            raise HTTPException(status_code=409, detail=f"Activa primero el módulo «{nombre}» para guardar este dato.")
        if almacen.transicion(propuesta_id, desde="pendiente", hacia="aplicando", db_path=cases.db_path) is None:
            raise HTTPException(status_code=409, detail="Esta propuesta ya se decidió.")
        try:
            aplicacion = propuestas.aplicar(
                propuesta,
                case,
                destino,
                perfil=perfil_de(case),
                hoy=hoy_en_espana(),
                registrar_hechos=lambda c, m, v: registrar_hechos_de_modulo(
                    c, m, v, fuente=f"correo:{propuesta.elemento_id}"
                ),
            )
        except Exception:
            logger.exception("Applying mail proposal %s failed", propuesta_id)
            almacen.transicion(propuesta_id, desde="aplicando", hacia="pendiente", db_path=cases.db_path)
            raise HTTPException(
                status_code=500, detail="No se pudo aplicar la propuesta. No se ha guardado nada."
            ) from None
        if aplicacion.es_error:
            almacen.transicion(
                propuesta_id, desde="aplicando", hacia="pendiente", resultado=aplicacion.texto, db_path=cases.db_path
            )
            raise HTTPException(status_code=422, detail=aplicacion.texto)
        if aplicacion.case is not None:
            cases.save_case(aplicacion.case)
        final = almacen.transicion(
            propuesta_id, desde="aplicando", hacia="confirmada", resultado=aplicacion.texto, db_path=cases.db_path
        )
        cases.append_audit_event(
            propuesta.case_id,
            {
                "event_type": "correo_propuesta_confirmada",
                "propuesta_id": propuesta_id,
                "destino": propuesta.destino,
                "tipo": propuesta.tipo,
                "herramienta": propuesta.herramienta,
                # Names only: the values are in the case (and redacted from here if the user deletes them).
                "hechos": sorted(propuesta.hechos),
                "argumentos_recibidos": sorted(propuesta.argumentos),
            },
        )
        assert final is not None
        return final

    @router.post("/correo/propuestas/{propuesta_id}/rechazar", response_model=almacen.Propuesta, dependencies=guarded)
    def post_rechazar(propuesta_id: str, cases: case_store_dep, access: access_dep) -> almacen.Propuesta:
        propuesta = _propuesta(propuesta_id, access, cases)
        rechazada = almacen.transicion(propuesta_id, desde="pendiente", hacia="rechazada", db_path=cases.db_path)
        if rechazada is None:
            raise HTTPException(status_code=409, detail="Esta propuesta ya se decidió.")
        cases.append_audit_event(
            propuesta.case_id, {"event_type": "correo_propuesta_rechazada", "propuesta_id": propuesta_id}
        )
        return rechazada

    @router.post("/correo/silenciar", dependencies=guarded)
    def post_silenciar(payload: SilenciarRequest, cases: case_store_dep, access: access_dep) -> dict[str, Any]:
        _caso(payload.case_id, access, cases)
        if payload.tipo == "categoria" and payload.valor not in CATEGORIAS:
            raise HTTPException(status_code=422, detail="Categoría desconocida.")
        try:
            decision = decisiones.silenciar(
                cases.db_path, case_id=payload.case_id, tipo=payload.tipo, valor=payload.valor, user_id=_user_id(access)
            )
        except DecisionInvalidaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"decision_id": decision.id, "ambito": decision.ambito, "caducidad": decision.caducidad}

    @router.get("/correo/contactos", response_model=ContactosResponse, dependencies=guarded)
    def get_contactos(case_id: str, cases: case_store_dep, access: access_dep) -> ContactosResponse:
        _caso(case_id, access, cases)
        return ContactosResponse(case_id=case_id, contactos=almacen.contactos(case_id, cases.db_path))

    @router.post("/correo/contactos", response_model=ContactosResponse, dependencies=guarded)
    def post_contacto(payload: ContactoRequest, cases: case_store_dep, access: access_dep) -> ContactosResponse:
        _caso(payload.case_id, access, cases)
        almacen.fijar_contacto(payload.case_id, payload.direccion.strip().lower(), cases.db_path)
        return ContactosResponse(case_id=payload.case_id, contactos=almacen.contactos(payload.case_id, cases.db_path))

    @router.delete("/correo/contactos", response_model=ContactosResponse, dependencies=guarded)
    def delete_contacto(case_id: str, direccion: str, cases: case_store_dep, access: access_dep) -> ContactosResponse:
        _caso(case_id, access, cases)
        almacen.soltar_contacto(case_id, direccion.strip().lower(), cases.db_path)
        return ContactosResponse(case_id=case_id, contactos=almacen.contactos(case_id, cases.db_path))

    @router.get("/correo/exportar", dependencies=guarded)
    def get_exportar(case_id: str, cases: case_store_dep, access: access_dep) -> dict[str, Any]:
        _caso(case_id, access, cases)
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "case_id": case_id,
            **almacen.exportar(case_id, cases.db_path),
        }

    return router

"""User feedback on the assistant's answers, and the review queue that turns mistakes into regression cases.

Feedback (the user, on their own cases):

* ``GET /evaluacion/consentimiento`` — the opt-in text (and its version) the UI shows next to the checkbox, the
  kinds of correction the user can choose, and the text of the per-case «aprender de los fallos» consent.
* ``POST /evaluacion/feedback`` — 👍/👎 on one answer, optionally «corregir» (what was wrong and the right
  answer or figure). Storing the conversation as an evaluation case needs ``guardar_para_evaluacion`` AND the
  current consent version, every time: there is no standing consent for that. See
  :mod:`asistente.nucleo.evaluacion.captura` for what is stored and where. A 👎 becomes a candidate case.
* ``GET /evaluacion/feedback?case_id=…`` — the caller's feedback on one of their cases, with what is stored.
* ``DELETE /evaluacion/feedback/{id}`` — withdraw it: the stored content leaves the case's audit trail, and the
  candidate (or accepted case) made from it is deleted.
* ``GET|PUT /evaluacion/aprendizaje?case_id=…`` — the standing, per-case consent to learn from detected mistakes
  (weak signals and plain 👎 votes become masked candidates). Withdrawing it deletes the pending ones.

The review queue (:mod:`asistente.nucleo.evaluacion.revision`), for the OWNER of the cases (their own candidates)
or the OPERATOR (``X-Operator-Key`` = ``HACIENDA_OPERATOR_KEY``, all candidates); anybody else's candidate answers
404, like a missing one. Without accounts (``AUTH_MODE=off``) the single owner sees everything:

* ``GET /evaluacion/revision`` — deduplicated groups, highest priority first (runs the weak-signal detection of
  the visible cases that consented first);
* ``GET /evaluacion/revision/{id}`` — one candidate: the masked conversation, the brain's plan, what went wrong,
  what was masked;
* ``POST /evaluacion/revision/{id}/aceptar`` — with the expected behaviour (:class:`EdicionCaso`): stored in the
  private evaluation database, never in the repository; 422 with every problem when it cannot be accepted;
* ``POST /evaluacion/revision/{id}/rechazar`` — with a reason;
* ``GET /evaluacion/revision/aceptados`` and ``DELETE /evaluacion/revision/aceptados/{id}`` — the accepted cases.

Built by :func:`build_router` with the case store provider passed in (see ``api.py``), like the other routers.
The module deliberately has no ``from __future__ import annotations`` (FastAPI resolves the local aliases).
"""

from collections.abc import Callable
from dataclasses import dataclass
import hmac
import os
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.evaluacion import privado, revision
from asistente.nucleo.evaluacion.captura import (
    CONSENTIMIENTO_TEXTO,
    CONSENTIMIENTO_VERSION,
    ETIQUETAS_CORRECCION,
    FeedbackEntrada,
    FeedbackRegistrado,
    FeedbackStore,
    RespuestaNoEncontradaError,
    capturar,
)
from asistente.nucleo.evaluacion.redaccion import nombres_de_perfil
from asistente.nucleo.evaluacion.revision import (
    CONSENTIMIENTO_APRENDIZAJE_TEXTO,
    CONSENTIMIENTO_APRENDIZAJE_VERSION,
    ETIQUETAS_FALLO,
    AceptacionRechazadaError,
    Candidato,
    CandidatoStore,
    EdicionCaso,
)
from asistente.plataforma.api.security import require_api_key
from asistente.plataforma.auth.deps import (
    COOKIE_SESION,
    CaseAccess,
    auth_required,
    get_auth_store,
    get_case_access,
    require_user,
)
from asistente.plataforma.auth.store import AuthStore, User


class TipoCorreccionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clave: str
    etiqueta: str


class ConsentimientoEvaluacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    texto: str
    tipos: list[TipoCorreccionInfo]
    aprendizaje_version: str
    aprendizaje_texto: str


class FeedbackRespuesta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    guardado_para_evaluacion: bool
    estado: str
    etiquetas: list[str] = Field(default_factory=list)
    mensaje: str
    # A candidate case of the review queue was made from this feedback.
    candidato: bool = False


class FeedbackDetalle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registro: FeedbackRegistrado
    # What is stored for evaluation (None: vote only, or withdrawn).
    contenido: dict[str, Any] | None = None


class FeedbackLista(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    feedback: list[FeedbackDetalle]


class EstadoAprendizaje(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    activo: bool
    version: str
    texto: str
    otorgado_el: str | None = None
    candidatos_pendientes: int = 0


class CambioAprendizaje(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    activo: bool
    # Required to switch it on: the version of the text the user saw.
    version: str | None = None


class CategoriaInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clave: str
    descripcion: str
    critica: bool


class GrupoResumen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # the representative candidate
    grupo: str
    pregunta: str
    tipo_fallo: str
    etiqueta_fallo: str
    categoria_sugerida: str
    critica: bool
    origen: str
    frecuencia: int
    prioridad: float
    motivos_prioridad: list[str]
    importe_en_juego: str | None = None
    creado_el: str
    estado: str


class ColaRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grupos: list[GrupoResumen]
    operador: bool
    categorias: list[CategoriaInfo]
    detectados: int = 0


class DetalleCandidato(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidato: Candidato
    etiqueta_fallo: str
    frecuencia: int
    duplicados: list[str]
    categorias: list[CategoriaInfo]


class Rechazo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=3, max_length=500)


class Aceptado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    categoria: str
    descripcion: str
    expectativas: list[dict[str, Any]]
    avisos: list[str]
    aceptado_el: str
    vigente: bool
    motivo_baja: str | None = None


class ListaAceptados(BaseModel):
    model_config = ConfigDict(extra="forbid")

    casos: list[Aceptado]


_stores: dict[str, FeedbackStore] = {}
_stores_lock = threading.Lock()


def store_para(db_path: str) -> FeedbackStore:
    """One feedback store per database file (the cases' file: one backup and one erasure cover both)."""
    with _stores_lock:
        store = _stores.get(db_path)
        if store is None:
            store = _stores[db_path] = FeedbackStore(db_path)
        return store


@dataclass(frozen=True)
class Quien:
    """Who is reviewing: the operator (sees everything) or a user (their own cases only)."""

    usuario: User | None
    operador: bool
    # None: every case is visible.
    case_ids: frozenset[str] | None

    @property
    def revisor(self) -> revision.Revisor:
        return "operador" if self.operador else "propietario"

    def ve(self, candidato: Candidato) -> bool:
        return self.case_ids is None or candidato.case_id in self.case_ids


def _clave_operador_valida(clave: str | None) -> bool:
    esperado = os.environ.get("HACIENDA_OPERATOR_KEY", "")
    return bool(esperado and clave and hmac.compare_digest(clave, esperado))


def get_quien(
    auth: Annotated[AuthStore, Depends(get_auth_store)],
    authorization: Annotated[str | None, Header()] = None,
    x_operator_key: Annotated[str | None, Header(alias="X-Operator-Key")] = None,
    sesion: Annotated[str | None, Cookie(alias=COOKIE_SESION)] = None,
) -> Quien:
    """The reviewer behind the request. Without accounts, the single owner sees everything."""
    if not auth_required():
        return Quien(usuario=None, operador=False, case_ids=None)
    if _clave_operador_valida(x_operator_key):
        return Quien(usuario=None, operador=True, case_ids=None)
    usuario = require_user(auth, authorization, sesion)
    return Quien(usuario=usuario, operador=False, case_ids=frozenset(auth.case_ids_of(usuario.id)))


def _categorias() -> list[CategoriaInfo]:
    from asistente.nucleo.evaluacion.suite import cargar_config

    config = cargar_config()
    return [CategoriaInfo(clave=k, descripcion=v.descripcion, critica=v.critica) for k, v in config.categorias.items()]


def _no_encontrado() -> HTTPException:
    return HTTPException(status_code=404, detail="Caso candidato no encontrado.")


def build_router(*, get_case_store: Callable[[], SQLiteCaseStateStore]) -> APIRouter:
    router = APIRouter(tags=["evaluacion"])
    case_store_dep = Annotated[SQLiteCaseStateStore, Depends(get_case_store)]
    access_dep = Annotated[CaseAccess, Depends(get_case_access)]
    quien_dep = Annotated[Quien, Depends(get_quien)]
    guarded = [Depends(require_api_key)]

    def _feedback_store(cases: case_store_dep) -> FeedbackStore:
        return store_para(cases.db_path)

    def _candidato_store(cases: case_store_dep) -> CandidatoStore:
        return revision.candidatos_para(cases.db_path)

    feedback_store_dep = Annotated[FeedbackStore, Depends(_feedback_store)]
    candidato_store_dep = Annotated[CandidatoStore, Depends(_candidato_store)]

    def _caso_propio(case_id: str, cases: SQLiteCaseStateStore, access: CaseAccess) -> Any:
        access.check(case_id)
        case = cases.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return case

    @router.get("/evaluacion/consentimiento", response_model=ConsentimientoEvaluacion, dependencies=guarded)
    def get_consentimiento() -> ConsentimientoEvaluacion:
        return ConsentimientoEvaluacion(
            version=CONSENTIMIENTO_VERSION,
            texto=CONSENTIMIENTO_TEXTO,
            tipos=[TipoCorreccionInfo(clave=k, etiqueta=v) for k, v in ETIQUETAS_CORRECCION.items()],
            aprendizaje_version=CONSENTIMIENTO_APRENDIZAJE_VERSION,
            aprendizaje_texto=CONSENTIMIENTO_APRENDIZAJE_TEXTO,
        )

    @router.post("/evaluacion/feedback", response_model=FeedbackRespuesta, status_code=201, dependencies=guarded)
    def post_feedback(
        payload: dict[str, Any],
        cases: case_store_dep,
        access: access_dep,
        store: feedback_store_dep,
        candidatos: candidato_store_dep,
    ) -> FeedbackRespuesta:
        # Validated here (not by FastAPI) so a cross-field rule reaches the user as a plain Spanish 422.
        try:
            entrada = FeedbackEntrada.model_validate(payload)
        except ValidationError as exc:
            mensajes = "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
            raise HTTPException(status_code=422, detail=mensajes or "Valoración no válida.") from exc
        case = None
        if entrada.case_id is not None:
            case = _caso_propio(entrada.case_id, cases, access)
        elif entrada.guardar_para_evaluacion:
            raise HTTPException(
                status_code=422, detail="Para guardar la conversación para evaluación hace falta un expediente."
            )
        usuario = access.user
        try:
            registro = capturar(
                entrada,
                store,
                case=case,
                cases=cases if case is not None else None,
                user_id=usuario.id if usuario is not None else None,
                nombres_extra=[usuario.email.split("@", 1)[0]] if usuario is not None else [],
            )
        except RespuestaNoEncontradaError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        con_candidato = bool(candidatos.de_feedback(registro.id)) if case is not None else False
        if registro.guardado_para_evaluacion:
            mensaje = (
                "Gracias. Hemos guardado la conversación en tu expediente, con tus datos personales ocultos, como "
                "caso para revisar en «Revisión de casos»; puedes retirarla cuando quieras."
            )
        elif con_candidato:
            mensaje = (
                "Gracias. Como tienes activado aprender de los fallos, la conversación queda, enmascarada, como caso "
                "para revisar en «Revisión de casos»."
            )
        else:
            mensaje = "Gracias por tu valoración. No se ha guardado el contenido de la conversación."
        return FeedbackRespuesta(
            id=registro.id,
            guardado_para_evaluacion=registro.guardado_para_evaluacion,
            estado=registro.estado,
            etiquetas=registro.etiquetas,
            mensaje=mensaje,
            candidato=con_candidato,
        )

    @router.get("/evaluacion/feedback", response_model=FeedbackLista, dependencies=guarded)
    def get_feedback(
        case_id: str, cases: case_store_dep, access: access_dep, store: feedback_store_dep
    ) -> FeedbackLista:
        _caso_propio(case_id, cases, access)
        return FeedbackLista(
            case_id=case_id,
            feedback=[FeedbackDetalle(registro=r, contenido=store.contenido(r)) for r in store.de_caso(case_id)],
        )

    @router.delete("/evaluacion/feedback/{feedback_id}", status_code=204, dependencies=guarded)
    def delete_feedback(
        feedback_id: str,
        cases: case_store_dep,
        access: access_dep,
        store: feedback_store_dep,
        candidatos: candidato_store_dep,
    ) -> Response:
        registro = store.get(feedback_id)
        if registro is None or registro.case_id is None:
            raise HTTPException(status_code=404, detail="Valoración no encontrada.")
        access.check(registro.case_id)
        store.retirar_contenido(registro)
        # What was made from it goes too: the candidate and, if it was accepted, the private case.
        for candidato in candidatos.de_feedback(feedback_id):
            if candidato.caso_aceptado:
                privado.store_de(cases.db_path).borrar(candidato.caso_aceptado)
            candidatos.borrar(candidato.id)
        store.borrar(feedback_id)
        return Response(status_code=204)

    # --- standing consent: learn from detected mistakes ----------------------------------------------------

    def _estado(case_id: str, candidatos: CandidatoStore) -> EstadoAprendizaje:
        consentimiento = candidatos.consentimiento(case_id)
        pendientes = [c for c in candidatos.de_casos([case_id]) if c.estado == "pendiente"]
        return EstadoAprendizaje(
            case_id=case_id,
            activo=consentimiento is not None,
            version=CONSENTIMIENTO_APRENDIZAJE_VERSION,
            texto=CONSENTIMIENTO_APRENDIZAJE_TEXTO,
            otorgado_el=consentimiento.otorgado_el.isoformat() if consentimiento is not None else None,
            candidatos_pendientes=len(pendientes),
        )

    @router.get("/evaluacion/aprendizaje", response_model=EstadoAprendizaje, dependencies=guarded)
    def get_aprendizaje(
        case_id: str, cases: case_store_dep, access: access_dep, candidatos: candidato_store_dep
    ) -> EstadoAprendizaje:
        _caso_propio(case_id, cases, access)
        return _estado(case_id, candidatos)

    @router.put("/evaluacion/aprendizaje", response_model=EstadoAprendizaje, dependencies=guarded)
    def put_aprendizaje(
        payload: CambioAprendizaje, cases: case_store_dep, access: access_dep, candidatos: candidato_store_dep
    ) -> EstadoAprendizaje:
        _caso_propio(payload.case_id, cases, access)
        if payload.activo:
            if payload.version != CONSENTIMIENTO_APRENDIZAJE_VERSION:
                raise HTTPException(
                    status_code=422,
                    detail=f"Hace falta aceptar el texto vigente (versión {CONSENTIMIENTO_APRENDIZAJE_VERSION}).",
                )
            candidatos.dar_consentimiento(payload.case_id)
            borrados = 0
        else:
            borrados = candidatos.retirar_consentimiento(payload.case_id)
        cases.append_audit_event(
            payload.case_id,
            {
                "event_type": "evaluacion_aprendizaje",
                "activo": payload.activo,
                "version": CONSENTIMIENTO_APRENDIZAJE_VERSION,
                "candidatos_borrados": borrados,
            },
        )
        return _estado(payload.case_id, candidatos)

    # --- the review queue ----------------------------------------------------------------------------------

    def _visible(candidatos: CandidatoStore, candidato_id: str, quien: Quien) -> Candidato:
        candidato = candidatos.get(candidato_id)
        if candidato is None or not quien.ve(candidato):
            raise _no_encontrado()
        return candidato

    def _nombres(cases: SQLiteCaseStateStore, auth: AuthStore, candidato: Candidato) -> list[str]:
        case = cases.get_case(candidato.case_id) if candidato.case_id else None
        hechos = [(f.name, f.value) for f in case.facts] if case is not None else []
        extra: list[str] = []
        if candidato.user_id and auth_required():
            usuario = auth.get_user(candidato.user_id)
            if usuario is not None:
                extra.append(usuario.email.split("@", 1)[0])
        return nombres_de_perfil(hechos, extra=extra)

    @router.get("/evaluacion/revision", response_model=ColaRevision, dependencies=guarded)
    def get_revision(
        quien: quien_dep,
        cases: case_store_dep,
        candidatos: candidato_store_dep,
        estado: str = "pendiente",
    ) -> ColaRevision:
        if estado not in ("pendiente", "aceptado", "rechazado"):
            raise HTTPException(status_code=422, detail="Estado desconocido.")
        # Weak signals of the visible cases that consented: detected now (idempotent), then listed.
        detectados = 0
        for consentimiento in candidatos.consentimientos(
            sorted(quien.case_ids) if quien.case_ids is not None else None
        ):
            case = cases.get_case(consentimiento.case_id)
            if case is not None:
                detectados += len(revision.detectar_en_caso(candidatos, cases, case, user_id=case.user_id))
        visibles = candidatos.todos() if quien.case_ids is None else candidatos.de_casos(sorted(quien.case_ids))
        grupos = revision.agrupar(visibles, estado=estado)
        return ColaRevision(
            grupos=[
                GrupoResumen(
                    id=g.representante.id,
                    grupo=g.grupo,
                    pregunta=g.representante.contenido.pregunta[:300],
                    tipo_fallo=g.representante.tipo_fallo,
                    etiqueta_fallo=ETIQUETAS_FALLO[g.representante.tipo_fallo],
                    categoria_sugerida=g.representante.categoria_sugerida,
                    critica=g.representante.critica,
                    origen=g.representante.origen,
                    frecuencia=g.frecuencia,
                    prioridad=g.prioridad,
                    motivos_prioridad=g.motivos_prioridad,
                    importe_en_juego=(
                        str(g.representante.importe_en_juego) if g.representante.importe_en_juego is not None else None
                    ),
                    creado_el=g.representante.creado_el.isoformat(),
                    estado=g.estado,
                )
                for g in grupos
            ],
            operador=quien.operador,
            categorias=_categorias(),
            detectados=detectados,
        )

    @router.get("/evaluacion/revision/aceptados", response_model=ListaAceptados, dependencies=guarded)
    def get_aceptados(quien: quien_dep, cases: case_store_dep) -> ListaAceptados:
        if not privado.ruta_por_defecto(cases.db_path).exists():
            return ListaAceptados(casos=[])  # nothing accepted yet: the private file is not even created
        store = privado.store_de(cases.db_path)
        reales = store.todos() if quien.case_ids is None else store.de_casos(sorted(quien.case_ids))
        return ListaAceptados(
            casos=[
                Aceptado(
                    id=r.id,
                    categoria=r.caso.categoria,
                    descripcion=r.caso.descripcion,
                    expectativas=[e.model_dump(mode="json") for e in r.caso.expectativas],
                    avisos=r.avisos,
                    aceptado_el=r.aceptado_el.isoformat(),
                    vigente=r.vigente,
                    motivo_baja=r.motivo_baja,
                )
                for r in reales
            ]
        )

    @router.delete("/evaluacion/revision/aceptados/{caso_id}", status_code=204, dependencies=guarded)
    def delete_aceptado(
        caso_id: str, quien: quien_dep, cases: case_store_dep, candidatos: candidato_store_dep
    ) -> Response:
        if not privado.ruta_por_defecto(cases.db_path).exists():
            raise HTTPException(status_code=404, detail="Caso no encontrado.")
        store = privado.store_de(cases.db_path)
        real = store.get(caso_id)
        if real is None or not (quien.case_ids is None or real.case_id in quien.case_ids):
            raise HTTPException(status_code=404, detail="Caso no encontrado.")
        store.borrar(caso_id)
        candidatos.reabrir_aceptados([caso_id])
        return Response(status_code=204)

    @router.get("/evaluacion/revision/{candidato_id}", response_model=DetalleCandidato, dependencies=guarded)
    def get_candidato(candidato_id: str, quien: quien_dep, candidatos: candidato_store_dep) -> DetalleCandidato:
        candidato = _visible(candidatos, candidato_id, quien)
        duplicados = [c.id for c in candidatos.del_grupo(candidato.grupo) if c.id != candidato.id and quien.ve(c)]
        return DetalleCandidato(
            candidato=candidato,
            etiqueta_fallo=ETIQUETAS_FALLO[candidato.tipo_fallo],
            frecuencia=1 + len(duplicados),
            duplicados=duplicados,
            categorias=_categorias(),
        )

    @router.post("/evaluacion/revision/{candidato_id}/aceptar", response_model=Aceptado, dependencies=guarded)
    def post_aceptar(
        candidato_id: str,
        payload: dict[str, Any],
        quien: quien_dep,
        cases: case_store_dep,
        candidatos: candidato_store_dep,
        auth: Annotated[AuthStore, Depends(get_auth_store)],
    ) -> Aceptado:
        candidato = _visible(candidatos, candidato_id, quien)
        try:
            edicion = EdicionCaso.model_validate(payload)
        except ValidationError as exc:
            mensajes = "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())
            raise HTTPException(status_code=422, detail=mensajes or "Edición no válida.") from exc
        try:
            real = revision.aceptar(
                candidatos,
                candidato,
                edicion,
                revisor=quien.revisor,
                cases=cases,
                visible=quien.ve,
                nombres=_nombres(cases, auth, candidato),
            )
        except AceptacionRechazadaError as exc:
            raise HTTPException(status_code=422, detail="; ".join(exc.problemas)) from exc
        return Aceptado(
            id=real.id,
            categoria=real.caso.categoria,
            descripcion=real.caso.descripcion,
            expectativas=[e.model_dump(mode="json") for e in real.caso.expectativas],
            avisos=real.avisos,
            aceptado_el=real.aceptado_el.isoformat(),
            vigente=real.vigente,
        )

    @router.post("/evaluacion/revision/{candidato_id}/rechazar", dependencies=guarded)
    def post_rechazar(
        candidato_id: str, payload: Rechazo, quien: quien_dep, cases: case_store_dep, candidatos: candidato_store_dep
    ) -> dict[str, int]:
        candidato = _visible(candidatos, candidato_id, quien)
        try:
            cambiados = revision.rechazar(
                candidatos, candidato, payload.motivo, revisor=quien.revisor, cases=cases, visible=quien.ve
            )
        except AceptacionRechazadaError as exc:
            raise HTTPException(status_code=422, detail="; ".join(exc.problemas)) from exc
        return {"rechazados": len(cambiados)}

    return router


__all__ = ["Quien", "build_router", "get_quien", "store_para"]

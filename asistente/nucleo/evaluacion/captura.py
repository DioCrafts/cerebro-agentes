"""User feedback on an answer, and turning a reviewed one into a suite case.

What is stored, and where (privacy by design):

* **A vote alone** (👍/👎 and, for 👎, what kind of error) is a row in ``evaluacion_feedback`` — no text of
  the conversation. With a case, it is also recorded in the case's audit trail, so the account export
  shows it.
* **A case for evaluation** is stored only when the user ticks the opt-in box for THAT answer (consent
  per feedback, with the version of the text they accepted). Its content — question, answer, the user's
  correction, the tool trace and a pseudonymised snapshot of only the facts the answer used — lives in
  the user's own case audit trail (``evaluacion_valoracion`` event): it is exported with ``/me/export``,
  redacted when the user deletes one of those facts, and erased with the case or the account. The review
  row is erased with the case by a SQLite trigger on ``case_states`` (same database file).
* The user can withdraw a stored case at any time (``DELETE /evaluacion/feedback/{id}``): its content is
  removed from the audit trail.
* **With the household brain**, the stored case also carries what the brain did on that turn
  (:class:`~asistente.nucleo.cerebro.contrato.InformeCerebro`: the plan, the specialists and their modes, the
  figures each gave, the figures gate's verdict and the ids — never the text — of the memories used), the
  prompt variant in use and the model that served each purpose.
* **Every «esto está mal» becomes a CANDIDATE case** (:mod:`asistente.nucleo.evaluacion.revision`): masked
  (names, identifiers, addresses — amounts kept), deduplicated and prioritised in a review queue. Nothing
  reaches the evaluation without a human accepting it there (the owner of the case, or the operator); an
  accepted case is stored in the PRIVATE evaluation database (:mod:`asistente.nucleo.evaluacion.privado`),
  never in the repository, and leaves with the user's export and erasure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.evaluacion.cifras import salida_de_herramienta, valor_de_cifra
from asistente.nucleo.evaluacion.modelos import (
    CalculoEsperado,
    CasoEvaluacion,
    ExpCifra,
    Expediente,
    ExpModuloConsultado,
    ExpModuloNoConsultado,
    Origen,
    Rubrica,
)
from asistente.nucleo.evaluacion.redaccion import identificadores_en, seudonimizar
from asistente.nucleo.evaluacion.senales import Senal, es_repregunta, respuestas, senales_de_turno
from asistente.plataforma.migraciones import asegurar_esquema
from asistente.plataforma.migraciones.catalogo import evaluacion as _esquema

logger = logging.getLogger(__name__)

CONSENTIMIENTO_VERSION = "2026-11-18"
CONSENTIMIENTO_TEXTO = (
    "Guardar esta conversación para mejorar el asistente. Se guardan, dentro de tu expediente, tu pregunta, la "
    "respuesta, tu corrección, cómo la preparó el asistente (a qué especialistas consultó, las cifras que dio "
    "cada uno y las herramientas que usó) y solo los datos de tu expediente que usó para responder. Antes de que "
    "nadie la revise se ocultan tu nombre, correos, DNI, IBAN, tarjetas, teléfonos y direcciones; las cifras se "
    "conservan. La revisarás tú en «Revisión de casos» (o una persona del equipo) y, si sirve, se convertirá en "
    "una prueba ENMASCARADA que se guarda aparte, en privado (nunca en el código del programa), para comprobar "
    "que el error no vuelve. Puedes retirarla cuando quieras; se exporta y se borra con tu cuenta. Nada se usa "
    "sin esa revisión."
)

TipoCorreccion = Literal[
    "cifra_erronea",
    "modulo_erroneo",
    "hecho_omitido",
    "afirmacion_inventada",
    "suma_indebida",
    "memoria_erronea",
    "otro",
]
ETIQUETAS_CORRECCION: dict[str, str] = {
    "cifra_erronea": "Cifra equivocada",
    "modulo_erroneo": "Ha consultado el ámbito equivocado",
    "hecho_omitido": "Ha pasado por alto un dato",
    "afirmacion_inventada": "Afirma algo que no es cierto",
    "suma_indebida": "Ha sumado cifras que no se pueden sumar",
    "memoria_erronea": "Recuerda mal algo que le dije",
    "otro": "Otro",
}
EstadoFeedback = Literal["solo_valoracion", "pendiente", "promovido", "descartado"]

# Tools whose result depends on the case facts (tax profile): they read the whole tax profile.
_LEEN_PERFIL_FISCAL = frozenset(
    {
        "calcular_irpf",
        "comparar_declaracion_conjunta",
        "listar_beneficios_fiscales",
        "optimizar_impuestos",
        "consultar_expediente",
        "evaluar_obligaciones",
    }
)


# --------------------------------------------------------------------------- #
# Pseudonymisation (the patterns live in redaccion.py; re-exported here)
# --------------------------------------------------------------------------- #


def _seudonimizar_valor(valor: Any) -> Any:
    if isinstance(valor, str):
        return seudonimizar(valor)
    if isinstance(valor, list):
        return [_seudonimizar_valor(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _seudonimizar_valor(v) for k, v in valor.items()}
    return valor


def _valor_legible(nombre: str, valor: Any) -> Any:
    from asistente.nucleo.modulos.perfil import valor_de_perfil

    return valor_de_perfil(nombre, valor)


def hechos_usados(
    case: CaseState | None, traza: Sequence[Mapping[str, Any]], especialistas: Sequence[str] = ()
) -> list[str]:
    """Names of the case facts the answer used, judged from the tool trace (data minimisation).

    * a tax tool that reads the profile (``calcular_irpf``, ``optimizar_impuestos``, the case tools…) used
      the tax profile (every fact that is not a module's);
    * consulting a module specialist used that module's facts and the shared ones — the central agent's
      ``consultar_<x>``, a call by ``especialista_<x>`` in the trace, or a specialist the household brain reported
      from (``especialistas``, its ``InformeCerebro``);
    * a fact named as a tool argument was used.

    The model also sees every fact in its context, so a fact used only in prose is not captured: the
    operator adds it when reviewing, if it is needed to reproduce the case.
    """
    if case is None:
        return []
    from asistente.nucleo.modulos.registro import hechos_compartidos, registro

    nombres = {fact.name for fact in case.facts}
    de_modulo = {nombre: dueno.clave for nombre, (dueno, _h) in registro().hechos().items()}
    compartidos = hechos_compartidos()
    usados: set[str] = set()
    consultados = {e for e in especialistas if e and e != "fiscal"}
    consultados |= {
        str(t.get("agent")).removeprefix("especialista_")
        for t in traza
        if str(t.get("agent") or "").startswith("especialista_") and not t.get("is_error")
    }
    for modulo in consultados:
        usados |= {n for n in nombres if de_modulo.get(n) == modulo or n in compartidos}
    if "fiscal" in especialistas:
        usados |= {n for n in nombres if n not in de_modulo}
    for entrada in traza:
        herramienta = str(entrada.get("tool") or "")
        if entrada.get("is_error"):
            continue
        if herramienta in _LEEN_PERFIL_FISCAL:
            usados |= {n for n in nombres if n not in de_modulo}
        if herramienta.startswith("consultar_") and (entrada.get("agent") or "asesor") == "asesor":
            modulo = herramienta.removeprefix("consultar_")
            usados |= {n for n in nombres if de_modulo.get(n) == modulo or n in compartidos}

        def claves(nodo: Any) -> None:
            if isinstance(nodo, dict):
                for clave, valor in nodo.items():
                    if clave in nombres:
                        usados.add(clave)
                    claves(valor)
            elif isinstance(nodo, list):
                for valor in nodo:
                    claves(valor)

        claves(entrada.get("arguments") or {})
    return sorted(usados)


def instantanea_de(
    case: CaseState | None, traza: Sequence[Mapping[str, Any]], especialistas: Sequence[str] = ()
) -> dict[str, Any]:
    """Pseudonymised snapshot of the facts used: ``{tax_period, jurisdiction, hechos, sensibles}``."""
    if case is None:
        return {"hechos": {}, "sensibles": []}
    from asistente.nucleo.modulos.perfil import hecho_de_modulo

    usados = set(hechos_usados(case, traza, especialistas))
    hechos = {f.name: _seudonimizar_valor(_valor_legible(f.name, f.value)) for f in case.facts if f.name in usados}
    sensibles = sorted(n for n in hechos if (propio := hecho_de_modulo(n)) is not None and propio[1].sensible)
    return {
        "tax_period": case.tax_period,
        "jurisdiction": case.jurisdiction,
        "hechos": hechos,
        "sensibles": sensibles,
    }


# --------------------------------------------------------------------------- #
# Request model
# --------------------------------------------------------------------------- #


class FeedbackEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str | None = None
    pregunta: str = Field(min_length=1, max_length=4000)
    respuesta: str = Field(min_length=1, max_length=40000)
    valoracion: Literal["positiva", "negativa"]
    tipo: TipoCorreccion | None = None
    comentario: str | None = Field(default=None, max_length=2000)
    respuesta_correcta: str | None = Field(default=None, max_length=4000)
    cifra_correcta: str | None = Field(default=None, max_length=100)
    guardar_para_evaluacion: bool = False
    # The consent text version the user saw when ticking the box; required to store a case.
    version_consentimiento: str | None = None

    @model_validator(mode="after")
    def _coherente(self) -> FeedbackEntrada:
        if self.valoracion == "positiva" and (
            self.tipo or self.comentario or self.respuesta_correcta or self.cifra_correcta
        ):
            raise ValueError("Una valoración positiva no lleva corrección.")
        if self.guardar_para_evaluacion and self.version_consentimiento != CONSENTIMIENTO_VERSION:
            raise ValueError(
                "Para guardar el caso hace falta aceptar el texto de consentimiento vigente "
                f"(versión {CONSENTIMIENTO_VERSION})."
            )
        return self


class FeedbackRegistrado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str | None = None
    creado_el: datetime
    valoracion: Literal["positiva", "negativa"]
    tipo: TipoCorreccion | None = None
    guardado_para_evaluacion: bool = False
    estado: EstadoFeedback = "solo_valoracion"
    etiquetas: list[str] = Field(default_factory=list)
    variante: str | None = None
    revisado_el: datetime | None = None
    caso_suite: str | None = None


class RespuestaNoEncontradaError(LookupError):
    """The answer is not in the case's audit trail."""


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

_TABLA = """
    CREATE TABLE IF NOT EXISTS evaluacion_feedback (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        case_id TEXT,
        payload_json TEXT NOT NULL
    )
"""
_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS evaluacion_feedback_borra_con_el_caso
    AFTER DELETE ON case_states
    BEGIN
        DELETE FROM evaluacion_feedback WHERE case_id = OLD.case_id;
    END
"""


def preparar(conn: sqlite3.Connection) -> None:
    """Every table of the evaluation in the cases' database (migrations first; the old-code path otherwise)."""
    asegurar_esquema(conn, "evaluacion")  # migrations (asistente/plataforma)
    conn.execute(_TABLA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evaluacion_feedback_case ON evaluacion_feedback(case_id)")
    conn.execute(_esquema.TABLA_CANDIDATOS)
    conn.execute(_esquema.INDICE_CANDIDATOS_CASO)
    conn.execute(_esquema.INDICE_CANDIDATOS_GRUPO)
    conn.execute(_esquema.TABLA_APRENDIZAJE)


def preparar_disparadores(conn: sqlite3.Connection) -> bool:
    """The «erase with the case» triggers, once ``case_states`` exists. True when they are in place."""
    existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'case_states'").fetchone()
    if existe is None:
        return False
    with conn:
        conn.execute(_TRIGGER)
        conn.execute(_esquema.DISPARADOR_CANDIDATOS)
    return True


class FeedbackStore:
    """Review rows of the feedback (no conversation text). Same SQLite file as the cases.

    The content of an opted-in case is an event of the case's audit trail; this store reads and redacts it
    in place (``audit_events`` of the same file), so a single erasure of the case removes everything.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._con_trigger = False

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                preparar(conn)
            self._conn = conn
        if not self._con_trigger:
            self._con_trigger = preparar_disparadores(self._conn)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def add(self, registro: FeedbackRegistrado) -> FeedbackRegistrado:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "INSERT INTO evaluacion_feedback (id, case_id, payload_json) VALUES (?, ?, ?)",
                    (registro.id, registro.case_id, registro.model_dump_json()),
                )
        return registro

    def get(self, feedback_id: str) -> FeedbackRegistrado | None:
        with self._lock:
            fila = (
                self._get()
                .execute("SELECT payload_json FROM evaluacion_feedback WHERE id = ?", (feedback_id,))
                .fetchone()
            )
        return FeedbackRegistrado.model_validate_json(fila["payload_json"]) if fila else None

    def _lista(self, sql: str, params: tuple[Any, ...] = ()) -> list[FeedbackRegistrado]:
        with self._lock:
            filas = self._get().execute(sql, params).fetchall()
        return [FeedbackRegistrado.model_validate_json(f["payload_json"]) for f in filas]

    def de_caso(self, case_id: str) -> list[FeedbackRegistrado]:
        return self._lista("SELECT payload_json FROM evaluacion_feedback WHERE case_id = ? ORDER BY seq", (case_id,))

    def todos(self) -> list[FeedbackRegistrado]:
        return self._lista("SELECT payload_json FROM evaluacion_feedback ORDER BY seq")

    def pendientes(self) -> list[FeedbackRegistrado]:
        return [r for r in self.todos() if r.estado == "pendiente"]

    def actualizar(self, registro: FeedbackRegistrado) -> FeedbackRegistrado:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "UPDATE evaluacion_feedback SET payload_json = ? WHERE id = ?",
                    (registro.model_dump_json(), registro.id),
                )
        return registro

    def borrar(self, feedback_id: str) -> bool:
        with self._lock:
            conn = self._get()
            with conn:
                cursor = conn.execute("DELETE FROM evaluacion_feedback WHERE id = ?", (feedback_id,))
        return cursor.rowcount == 1

    # --- the content, in the case's audit trail ------------------------------------------------------

    def contenido(self, registro: FeedbackRegistrado) -> dict[str, Any] | None:
        """The stored case (question, answer, correction, trace, snapshot), or None (vote only / withdrawn)."""
        if registro.case_id is None or not registro.guardado_para_evaluacion:
            return None
        with self._lock:
            filas = (
                self._get()
                .execute("SELECT event_json FROM audit_events WHERE case_id = ? ORDER BY id", (registro.case_id,))
                .fetchall()
            )
        for fila in filas:
            evento = json.loads(fila["event_json"])
            if evento.get("event_type") == "evaluacion_valoracion" and evento.get("feedback_id") == registro.id:
                caso = evento.get("caso")
                return caso if isinstance(caso, dict) else None
        return None

    def retirar_contenido(self, registro: FeedbackRegistrado) -> int:
        """Remove the stored case from the audit trail (the vote stays). Returns the events rewritten."""
        if registro.case_id is None:
            return 0
        cambiados = 0
        with self._lock:
            conn = self._get()
            with conn:
                filas = conn.execute(
                    "SELECT id, event_json FROM audit_events WHERE case_id = ?", (registro.case_id,)
                ).fetchall()
                for fila in filas:
                    evento = json.loads(fila["event_json"])
                    if evento.get("event_type") != "evaluacion_valoracion" or evento.get("feedback_id") != registro.id:
                        continue
                    evento.pop("caso", None)
                    evento["guardado_para_evaluacion"] = False
                    evento["retirado_el"] = datetime.now(UTC).isoformat()
                    conn.execute(
                        "UPDATE audit_events SET event_json = ? WHERE id = ?",
                        (json.dumps(evento, ensure_ascii=False), fila["id"]),
                    )
                    cambiados += 1
            if cambiados:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        return cambiados

    def estadisticas(self) -> dict[str, Any]:
        """Counts only (no content), for the operator."""
        todos = self.todos()

        def contar(valores: Sequence[str | None]) -> dict[str, int]:
            salida: dict[str, int] = {}
            for valor in valores:
                clave = valor or "—"
                salida[clave] = salida.get(clave, 0) + 1
            return dict(sorted(salida.items()))

        return {
            "total": len(todos),
            "por_valoracion": contar([r.valoracion for r in todos]),
            "por_tipo": contar([r.tipo for r in todos if r.valoracion == "negativa"]),
            "por_estado": contar([r.estado for r in todos]),
            "por_etiqueta": contar([e for r in todos for e in r.etiquetas]),
        }


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #


def _turno(eventos: Sequence[dict[str, Any]], pregunta: str) -> tuple[int, dict[str, Any]]:
    turnos = respuestas(eventos)
    objetivo = " ".join(pregunta.split())
    for indice in range(len(turnos) - 1, -1, -1):
        if " ".join(str(turnos[indice].get("input") or "").split()) == objetivo:
            return indice, turnos[indice]
    raise RespuestaNoEncontradaError("No encuentro esa respuesta en el historial del expediente.")


def senales_para(eventos: Sequence[dict[str, Any]], indice: int) -> list[Senal]:
    turnos = respuestas(eventos)
    senales = senales_de_turno(turnos[indice], indice)
    if indice + 1 < len(turnos) and es_repregunta(turnos[indice], turnos[indice + 1]):
        senales.append(Senal(tipo="repregunta", turno=indice, detalle="el usuario volvió a preguntar"))
    return senales


def modelos_por_proposito(turno: Mapping[str, Any], especialistas: Sequence[str] = ()) -> dict[str, str]:
    """The model that serves each purpose of the turn, from the routing table NOW (the audit event does not say).

    Used only when the turn's event carries no ``modelos`` of its own: the result is labelled «configuración» in
    the candidate, since the routing may have changed between the answer and the feedback.
    """
    from asistente.nucleo.agent import enrutado
    from asistente.plataforma import settings

    proveedor = str(turno.get("provider") or settings.LLM_PROVIDER)
    base = str(turno.get("model") or "")
    propositos = ["agente", "verificador"]
    if isinstance(turno.get("cerebro"), dict):
        propositos += ["cerebro", "cerebro:intencion"]
    propositos += [f"especialista:{e}" for e in especialistas if e not in ("fiscal", "cerebro")]
    salida: dict[str, str] = {}
    for proposito in propositos:
        try:
            ruta = enrutado.resolver(proposito, proveedor=proveedor)
        except ValueError:  # a broken routing setting: the candidate simply lacks this line
            continue
        modelo = ruta.modelo_efectivo(base) if base else (ruta.modelo or "")
        if modelo:
            salida[proposito] = modelo
    return salida


def _del_cerebro(turno: Mapping[str, Any]) -> dict[str, Any]:
    """What the stored case keeps of the household brain's turn (InformeCerebro): plan, specialists and their
    modes, figures, the gate's verdict, the ids of the memories used — never their text."""
    from asistente.nucleo.cerebro.contrato import InformeCerebro

    bruto = turno.get("cerebro")
    if not isinstance(bruto, dict):
        return {}
    try:
        informe = InformeCerebro.model_validate(bruto)
    except ValueError:
        return {"cerebro": None, "cerebro_invalido": True}
    return {
        "cerebro": informe.model_dump(mode="json"),
        "modos_especialistas": {i.especialista: i.modo for i in informe.informes},
    }


def capturar(
    entrada: FeedbackEntrada,
    store: FeedbackStore,
    *,
    case: CaseState | None,
    cases: Any = None,
    user_id: str | None = None,
    nombres_extra: Sequence[str] = (),
) -> FeedbackRegistrado:
    """Record one feedback. ``case`` (already access-checked) and ``cases`` (the case store) go together.

    A 👎 becomes a candidate case of the review queue (:mod:`asistente.nucleo.evaluacion.revision`) when the user
    ticked the box for this answer, or when automatic learning is on for the case; ``user_id`` and
    ``nombres_extra`` (e.g. the account's e-mail) only help mask and link it.

    Raises :class:`RespuestaNoEncontradaError` when the answer is not in the case's audit trail, and
    ``ValueError`` when a case is to be stored without a case to store it in.
    """
    from asistente.nucleo.evaluacion import revision
    from asistente.nucleo.evaluacion.variantes import variante_activa

    if entrada.guardar_para_evaluacion and (case is None or cases is None):
        raise ValueError("Para guardar el caso para evaluación hace falta un expediente.")
    ahora = datetime.now(UTC)
    registro = FeedbackRegistrado(
        id=f"fb_{uuid4().hex}",
        case_id=case.case_id if case is not None else None,
        creado_el=ahora,
        valoracion=entrada.valoracion,
        tipo=entrada.tipo,
        guardado_para_evaluacion=entrada.guardar_para_evaluacion,
        estado="pendiente" if entrada.guardar_para_evaluacion else "solo_valoracion",
        variante=variante_activa().id,
    )
    if case is None or cases is None:
        return store.add(registro)

    eventos = cases.list_audit_events(case.case_id)
    indice, turno = _turno(eventos, entrada.pregunta)
    if isinstance(turno.get("variante"), str):
        registro.variante = turno["variante"]  # the variant that really answered, when the turn recorded it
    senales = senales_para(eventos, indice)
    registro.etiquetas = sorted({s.tipo for s in senales} | ({entrada.tipo} if entrada.tipo else set()))
    evento: dict[str, Any] = {
        "event_type": "evaluacion_valoracion",
        "feedback_id": registro.id,
        "valoracion": entrada.valoracion,
        "tipo": entrada.tipo,
        "guardado_para_evaluacion": entrada.guardar_para_evaluacion,
        "etiquetas": registro.etiquetas,
    }
    correccion = {
        "tipo": entrada.tipo,
        "comentario": entrada.comentario,
        "respuesta_correcta": entrada.respuesta_correcta,
        "cifra_correcta": entrada.cifra_correcta,
    }
    traza = [t for t in turno.get("tool_trace") or [] if isinstance(t, dict)]
    del_cerebro = _del_cerebro(turno)
    if entrada.guardar_para_evaluacion:
        evento["consentimiento"] = {"version": CONSENTIMIENTO_VERSION, "otorgado_el": ahora.isoformat()}
        evento["caso"] = {
            "pregunta": entrada.pregunta,
            "respuesta": entrada.respuesta,
            "modo": turno.get("mode"),
            "proveedor": turno.get("provider"),
            "modelo": turno.get("model"),
            "variante": registro.variante,
            "correccion": correccion,
            "tool_trace": traza,
            "claims": turno.get("claims") or [],
            "senales": [s.model_dump(mode="json") for s in senales],
            "instantanea": instantanea_de(case, traza, list(del_cerebro.get("modos_especialistas") or {})),
            **del_cerebro,
            "modelos": (
                turno.get("modelos")
                if isinstance(turno.get("modelos"), dict)
                else modelos_por_proposito(turno, list(del_cerebro.get("modos_especialistas") or {}))
            ),
        }
    store.add(registro)
    cases.append_audit_event(case.case_id, evento)

    # The review queue: with the box ticked, or with automatic learning on for this case.
    if entrada.valoracion == "negativa":
        candidatos = revision.candidatos_para(store.db_path)
        consentido = candidatos.consentimiento(case.case_id) is not None
        if entrada.guardar_para_evaluacion or consentido:
            try:
                contenido = revision.construir_contenido(
                    pregunta=entrada.pregunta,
                    respuesta=entrada.respuesta,
                    turno=turno,
                    case=case,
                    nombres_extra=nombres_extra,
                    correccion=correccion,
                    senales=senales,
                    variante=registro.variante,
                )
                candidatos.anadir(
                    revision.nuevo_candidato(
                        contenido,
                        tipo=revision.tipo_de_correccion(entrada.tipo),
                        origen="valoracion" if entrada.guardar_para_evaluacion else "voto",
                        case_id=case.case_id,
                        user_id=user_id or case.user_id,
                        referencia_turno=str(turno.get("event_time") or indice),
                        feedback_id=registro.id,
                    )
                )
            except Exception:  # the vote is recorded whatever happens to its candidate
                logger.exception("Could not turn a feedback into a candidate case")
        if consentido:
            revision.detectar_en_caso(candidatos, cases, case, user_id=user_id, nombres_extra=nombres_extra)
    return registro


def registrar_revision(cases: Any, registro: FeedbackRegistrado) -> None:
    """Tell the user's audit trail what the operator decided (visible in their export)."""
    if registro.case_id is None:
        return
    try:
        cases.append_audit_event(
            registro.case_id,
            {
                "event_type": "evaluacion_revision",
                "feedback_id": registro.id,
                "estado": registro.estado,
                "caso_suite": registro.caso_suite,
            },
        )
    except KeyError:  # the case was erased meanwhile: nothing to tell
        pass


# --------------------------------------------------------------------------- #
# From a reviewed feedback to a suite case (operator)
# --------------------------------------------------------------------------- #


def referencia_anonima(feedback_id: str) -> str:
    """A non-reversible reference to the feedback, for the suite file (never the case or user id)."""
    return "fb-" + hashlib.sha256(feedback_id.encode()).hexdigest()[:12]


_IMPORTE = r"-?\d{1,3}(?:\.\d{3})*,\d{2} €"


def _linea_de_cifra(salida: str, cifra: str) -> str | None:
    """A pattern reading, from the calculator output, the amount equal to the user's figure.

    It anchors on the line's label and on the position of the amount within the line (the n-th amount),
    not on the other numbers of the line, so it keeps reading the same concept if those change.
    """
    objetivo = valor_de_cifra(cifra)
    if objetivo is None:
        return None
    for linea in salida.splitlines():
        etiqueta, separador, resto = linea.partition(":")
        if not separador or not etiqueta.strip(" -*·"):
            continue
        importes = [m.group(0) for m in re.finditer(_IMPORTE, resto)]
        for posicion, importe in enumerate(importes):
            if valor_de_cifra(importe) == objetivo:
                previos = f"(?:[^\n]*?{_IMPORTE}){{{posicion}}}" if posicion else ""
                return r"^\s*" + re.escape(etiqueta.strip() + ":") + previos + rf"[^\n]*?(?P<cifra>{_IMPORTE})"
    return None


def borrador_de_caso(registro: FeedbackRegistrado, contenido: Mapping[str, Any]) -> tuple[CasoEvaluacion, list[str]]:
    """A draft suite case from a stored feedback, plus notes for the operator. Never written by itself.

    Expectations proposed: the module consulted (or, for «ámbito equivocado», that it must NOT be); and, when
    the user gave the correct figure and a calculator of the trace produces it on the snapshot, a ``cifra``
    expectation reading THAT calculator line (the user's figure only locates the line; the expected value
    is always the calculator's). Anything else becomes a rubric from the user's correction.
    """
    notas: list[str] = []
    instantanea = dict(contenido.get("instantanea") or {})
    expediente = Expediente(
        tax_period=str(instantanea.get("tax_period") or "2025"),
        jurisdiction=str(instantanea.get("jurisdiction") or "ES"),
        hechos=dict(instantanea.get("hechos") or {}),
    )
    if instantanea.get("sensibles"):
        notas.append(
            "Datos marcados como sensibles en la instantánea: "
            + ", ".join(instantanea["sensibles"])
            + ". Sustitúyelos por valores ficticios que reproduzcan el caso."
        )
    traza = [t for t in contenido.get("tool_trace") or [] if isinstance(t, dict)]
    correccion = dict(contenido.get("correccion") or {})
    tipo = correccion.get("tipo")
    modulos = sorted(
        {
            str(t["tool"]).removeprefix("consultar_")
            for t in traza
            if str(t.get("tool") or "").startswith("consultar_") and (t.get("agent") or "asesor") == "asesor"
        }
    )
    expectativas: list[Any] = []
    for modulo in modulos:
        if tipo == "modulo_erroneo":
            expectativas.append(ExpModuloNoConsultado(tipo="modulo_no_consultado", modulo=modulo))
        else:
            expectativas.append(ExpModuloConsultado(tipo="modulo_consultado", modulo=modulo))
    if tipo == "modulo_erroneo":
        notas.append("El usuario dice que el ámbito era otro: añade «modulo_consultado» con el correcto.")
    cifra_usuario = str(correccion.get("cifra_correcta") or "").strip()
    if cifra_usuario:
        encontrada = False
        for entrada in traza:
            if entrada.get("is_error") or str(entrada.get("tool") or "").startswith("consultar_"):
                continue
            herramienta = str(entrada.get("tool") or "")
            argumentos = entrada.get("arguments") if isinstance(entrada.get("arguments"), dict) else {}
            try:
                salida = salida_de_herramienta(herramienta, dict(argumentos or {}), expediente)
            except Exception:  # a tool of the trace that no longer runs on the snapshot: skip it
                continue
            patron = _linea_de_cifra(salida, cifra_usuario)
            if patron is None:
                continue
            calculo = CalculoEsperado(herramienta=herramienta, argumentos=dict(argumentos or {}), patron=patron)
            expectativas.append(ExpCifra(tipo="cifra", nombre="cifra_corregida", calculo=calculo))
            encontrada = True
            break
        if not encontrada:
            notas.append(
                f"La cifra que indica el usuario ({cifra_usuario}) no la da ninguna calculadora de la traza sobre "
                "la instantánea: no se propone como expectativa (una cifra esperada solo puede salir de una "
                "calculadora). Revisa a mano."
            )
    criterios: list[str] = []
    comentario = seudonimizar(str(correccion.get("comentario") or "")).strip()
    if comentario:
        criterios.append(f"La respuesta no incurre en el error que señaló el usuario: {comentario}")
    referencia = seudonimizar(str(correccion.get("respuesta_correcta") or "")).strip() or None
    rubrica = Rubrica(criterios=criterios, referencia=referencia) if criterios else None
    if rubrica is None and not expectativas:
        rubrica = Rubrica(
            criterios=["La respuesta corrige el error señalado por el usuario (completa este criterio)."],
            referencia=referencia,
        )
        notas.append("Sin expectativas deterministas: concreta la rúbrica o añade expectativas.")
    hoy = date.today()
    caso = CasoEvaluacion(
        id=f"regresion-{hoy.strftime('%Y%m%d')}-{referencia_anonima(registro.id)[3:9]}",
        categoria="regresion",
        descripcion=f"Corrección de usuario ({ETIQUETAS_CORRECCION.get(str(tipo), 'sin tipo')}). Revisar.",
        pregunta=seudonimizar(str(contenido.get("pregunta") or "")),
        modulos=modulos,
        expediente=expediente,
        expectativas=expectativas,
        rubrica=rubrica,
        origen=Origen(tipo="feedback", referencia=referencia_anonima(registro.id), etiquetas=list(registro.etiquetas)),
    )
    return caso, notas


def comprobar_anonimizado(caso: CasoEvaluacion) -> list[str]:
    """Identifiers still present anywhere in a case about to enter the suite (empty = none found)."""
    textos: list[tuple[str, str]] = [("pregunta", caso.pregunta), ("descripcion", caso.descripcion)]
    textos += [(f"historial[{i}]", m.get("content", "")) for i, m in enumerate(caso.historial)]
    if caso.expediente is not None:
        textos.append(("expediente", json.dumps(caso.expediente.hechos, ensure_ascii=False)))
    if caso.rubrica is not None:
        textos += [("rubrica", " ".join(caso.rubrica.criterios)), ("referencia", caso.rubrica.referencia or "")]
    for clave, pasos in (caso.guion or {}).items():
        textos += [(f"guion.{clave}", p.texto or json.dumps([h.argumentos for h in p.herramientas])) for p in pasos]
    problemas: list[str] = []
    for donde, texto in textos:
        encontrados = identificadores_en(texto)
        if encontrados:
            problemas.append(f"{donde}: parece contener {', '.join(encontrados)}")
    return problemas


__all__ = [
    "CONSENTIMIENTO_TEXTO",
    "CONSENTIMIENTO_VERSION",
    "ETIQUETAS_CORRECCION",
    "FeedbackEntrada",
    "FeedbackRegistrado",
    "FeedbackStore",
    "RespuestaNoEncontradaError",
    "borrador_de_caso",
    "capturar",
    "comprobar_anonimizado",
    "hechos_usados",
    "identificadores_en",
    "instantanea_de",
    "modelos_por_proposito",
    "preparar",
    "preparar_disparadores",
    "referencia_anonima",
    "registrar_revision",
    "senales_para",
    "seudonimizar",
]

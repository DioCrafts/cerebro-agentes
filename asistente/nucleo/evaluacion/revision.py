"""The review queue: every «esto está mal» becomes a CANDIDATE case; a human accepts, edits or rejects it.

Where candidates come from (always masked first, :mod:`asistente.nucleo.evaluacion.redaccion`):

* ``valoracion`` — a 👎 / «corregir» the user chose to store for evaluation (per-answer consent, as always);
* ``voto`` — a 👎 without that box, on a case whose owner switched on «aprender de los fallos detectados»
  (:class:`ConsentimientoAprendizaje`, per case, withdrawable);
* ``senal`` — with that same consent, the WEAK signals of the audit trail (:data:`~asistente.nucleo.evaluacion.
  senales.SENALES_DEBILES`): the figures gate still unhappy with the final answer, the verifier's rejected claims,
  the user asking the same thing again right after, an abstention. Only turns after the consent was given.

Why a queue and not automatic tests: a signal is a hint, not a verdict — the reviewer decides what the right
behaviour was, and only that becomes an expectation. Why masked before anyone reads it: the reviewer needs the
figures, not the person.

Triage (:func:`agrupar`): candidates are deduplicated by the normalised masked question + the kind of failure
(a «group», its frequency counted across occurrences), and ordered by a transparent score — critical category of
the suite first (+100; a vague signal — a repeat, an abstention — never claims it by itself), then what the user
said over what was inferred (+40 a stored correction, +30 a vote;
signals +25 gate, +20 verifier, +10 repeat, +5 abstention), frequency (+10·log2 n) and money at stake
(+6·log10(1+€), at most +30). Every term is written out in ``motivos_prioridad``.

Acceptance (:func:`aceptar`) turns the reviewer's expected behaviour into deterministic expectations (must route
to / must not route to, must cite, forbidden figures, an expected figure that ALWAYS comes from a calculator,
must abstain or ask, the figures gate clean) and refuses the case unless:

1. nothing in it still looks personal (identifiers or the profile's names);
2. every expected figure can be computed by its calculator on the snapshot;
3. the expectations FAIL on the answer that was actually given (a negative control: an expectation that the
   wrong answer passes catches nothing) — the reviewer may override this one, and the warning is kept.

The accepted case goes to the private store (:mod:`asistente.nucleo.evaluacion.privado`) and joins every later
evaluation run and promotion gate. Rejection needs a reason. Owners see and decide on their own candidates only;
the operator sees all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, Literal
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.cerebro.contrato import InformeCerebro
from asistente.nucleo.evaluacion.cifras import numero, salida_de_herramienta
from asistente.nucleo.evaluacion.modelos import (
    CalculoEsperado,
    CasoEvaluacion,
    CitaRegistrada,
    ConfigSuite,
    Ejecucion,
    ExpCifra,
    ExpCifraAtribuida,
    ExpCifraAusente,
    ExpCitas,
    Expediente,
    ExpEspecialistas,
    ExpIntencion,
    ExpModo,
    ExpModuloConsultado,
    ExpModuloNoConsultado,
    ExpPuerta,
    ExpTextoAusente,
    ExpTextoPresente,
    LlamadaRegistrada,
    Origen,
    Rubrica,
)
from asistente.nucleo.evaluacion.privado import CasoReal, store_de
from asistente.nucleo.evaluacion.redaccion import (
    CambioRedaccion,
    Enmascarador,
    identificadores_en,
    nombres_de_perfil,
    nombres_en,
)
from asistente.nucleo.evaluacion.reproduccion import guion_de_captura
from asistente.nucleo.evaluacion.senales import SENALES_DEBILES, Senal, es_repregunta, respuestas, senales_de_turno
from asistente.plataforma.sqlite import abrir_sqlite

logger = logging.getLogger(__name__)

TipoFallo = Literal[
    "cifra_erronea",
    "modulo_erroneo",
    "hecho_omitido",
    "afirmacion_inventada",
    "suma_indebida",
    "memoria_erronea",
    "otro",
    "puerta_cifras",
    "verificador",
    "repregunta",
    "abstencion",
]
OrigenCandidato = Literal["valoracion", "voto", "senal"]
EstadoCandidato = Literal["pendiente", "aceptado", "rechazado"]
Revisor = Literal["propietario", "operador"]

ETIQUETAS_FALLO: dict[str, str] = {
    "cifra_erronea": "Cifra equivocada",
    "modulo_erroneo": "Ámbito equivocado",
    "hecho_omitido": "Pasó por alto un dato",
    "afirmacion_inventada": "Afirma algo falso",
    "suma_indebida": "Suma cifras que no se suman",
    "memoria_erronea": "Recuerda mal",
    "otro": "Otro",
    "puerta_cifras": "Cifras sin origen o mal atribuidas",
    "verificador": "Afirmaciones sin respaldo",
    "repregunta": "El usuario volvió a preguntar",
    "abstencion": "No respondió",
}
_SENAL_A_FALLO: dict[str, TipoFallo] = {
    "puerta_cifras": "puerta_cifras",
    "afirmacion_rechazada": "verificador",
    "repregunta": "repregunta",
    "abstencion": "abstencion",
}
PESO_ORIGEN: dict[str, float] = {"valoracion": 40.0, "voto": 30.0}
PESO_SENAL: dict[str, float] = {"puerta_cifras": 25.0, "verificador": 20.0, "repregunta": 10.0, "abstencion": 5.0}
PESO_CRITICA = 100.0
_SIN_INFORME = "no trae el informe del cerebro"
_SENALES_ESPECIFICAS = frozenset({"puerta_cifras", "verificador"})

CONSENTIMIENTO_APRENDIZAJE_VERSION = "2026-11-18"
CONSENTIMIENTO_APRENDIZAJE_TEXTO = (
    "Aprender de los fallos que se detecten en este expediente. Cuando marques una respuesta como incorrecta, o el "
    "asistente note que algo pudo salir mal (una cifra sin origen, una afirmación que no se pudo respaldar, que "
    "vuelvas a preguntar lo mismo o que no supiera responder), esa conversación se guardará como caso candidato, con "
    "tu nombre, correos, DNI, IBAN, tarjetas, teléfonos y direcciones ocultados (las cifras se conservan). Lo "
    "revisarás tú en «Revisión de casos» (o una persona del equipo) y solo lo que se acepte se usará, en privado, "
    "para comprobar que el error no vuelve. Puedes desactivarlo cuando quieras; se exporta y se borra con tu cuenta."
)


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContenidoCandidato(_Modelo):
    """What the reviewer reads: the turn, masked (amounts kept), and what the brain did."""

    pregunta: str
    respuesta: str | None = None
    # True when the answer was rebuilt from the verifier's claims (the audit trail does not keep it).
    respuesta_parcial: bool = False
    # The user's next question (a «repregunta» candidate).
    siguiente_pregunta: str | None = None
    correccion: dict[str, str | None] = Field(default_factory=dict)
    modo: str | None = None
    cerebro: dict[str, Any] | None = None
    # Only the summary the audit event carried (ruta, tipo, especialistas, violaciones_cifras), when no full report.
    cerebro_resumen: dict[str, Any] | None = None
    modos_especialistas: dict[str, str | None] = Field(default_factory=dict)
    variante: str | None = None
    modelos: dict[str, str] = Field(default_factory=dict)
    modelos_origen: Literal["turno", "configuracion"] = "configuracion"
    senales: list[dict[str, Any]] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    citas: list[dict[str, Any]] = Field(default_factory=list)
    acciones_pendientes: list[str] = Field(default_factory=list)
    instantanea: dict[str, Any] = Field(default_factory=dict)
    # The diff: every span that was masked, with an abbreviated view of the original.
    enmascarado: list[CambioRedaccion] = Field(default_factory=list)


class Candidato(_Modelo):
    id: str
    grupo: str
    clave: str
    case_id: str | None = None
    user_id: str | None = None
    origen: OrigenCandidato
    feedback_id: str | None = None
    referencia_turno: str
    tipo_fallo: TipoFallo
    categoria_sugerida: str
    critica: bool = False
    importe_en_juego: Decimal | None = None
    prioridad: float = 0.0
    motivos_prioridad: list[str] = Field(default_factory=list)
    estado: EstadoCandidato = "pendiente"
    creado_el: datetime
    revisado_el: datetime | None = None
    revisado_por: Revisor | None = None
    motivo_rechazo: str | None = None
    caso_aceptado: str | None = None
    contenido: ContenidoCandidato


class GrupoCandidatos(_Modelo):
    """One line of the queue: a deduplicated failure, its frequency and its priority."""

    grupo: str
    representante: Candidato
    ids: list[str]
    frecuencia: int
    prioridad: float
    motivos_prioridad: list[str]
    estado: EstadoCandidato


class ConsentimientoAprendizaje(_Modelo):
    case_id: str
    version: str
    otorgado_el: datetime


# --------------------------------------------------------------------------- #
# Store (the cases' database: the rows go with the case by trigger)
# --------------------------------------------------------------------------- #


class CandidatoStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._con_disparadores = False

    def _get(self) -> sqlite3.Connection:
        from asistente.nucleo.evaluacion.captura import preparar, preparar_disparadores

        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = abrir_sqlite(self.db_path, timeout=30.0, check_same_thread=False, filas=True)
            conn.execute("PRAGMA secure_delete=ON;")
            with conn:
                preparar(conn)
            self._conn = conn
        if not self._con_disparadores:
            self._con_disparadores = preparar_disparadores(self._conn)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def anadir(self, candidato: Candidato) -> Candidato | None:
        """Store a candidate; None when the same occurrence was already stored (idempotent by ``clave``)."""
        with self._lock:
            conn = self._get()
            with conn:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO evaluacion_candidatos (id, clave, case_id, grupo, estado, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidato.id,
                        candidato.clave,
                        candidato.case_id,
                        candidato.grupo,
                        candidato.estado,
                        candidato.model_dump_json(),
                    ),
                )
        return candidato if cursor.rowcount == 1 else None

    def actualizar(self, candidato: Candidato) -> Candidato:
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "UPDATE evaluacion_candidatos SET estado = ?, payload_json = ? WHERE id = ?",
                    (candidato.estado, candidato.model_dump_json(), candidato.id),
                )
        return candidato

    def get(self, candidato_id: str) -> Candidato | None:
        with self._lock:
            fila = (
                self._get()
                .execute("SELECT payload_json FROM evaluacion_candidatos WHERE id = ?", (candidato_id,))
                .fetchone()
            )
        return Candidato.model_validate_json(fila["payload_json"]) if fila else None

    def _lista(self, sql: str, params: tuple[Any, ...] = ()) -> list[Candidato]:
        with self._lock:
            filas = self._get().execute(sql, params).fetchall()
        return [Candidato.model_validate_json(f["payload_json"]) for f in filas]

    def todos(self) -> list[Candidato]:
        return self._lista("SELECT payload_json FROM evaluacion_candidatos ORDER BY seq")

    def de_casos(self, case_ids: Sequence[str]) -> list[Candidato]:
        if not case_ids:
            return []
        marcas = ",".join("?" * len(case_ids))
        return self._lista(
            f"SELECT payload_json FROM evaluacion_candidatos WHERE case_id IN ({marcas}) ORDER BY seq",  # noqa: S608
            tuple(case_ids),
        )

    def del_grupo(self, grupo: str) -> list[Candidato]:
        return self._lista("SELECT payload_json FROM evaluacion_candidatos WHERE grupo = ? ORDER BY seq", (grupo,))

    def de_feedback(self, feedback_id: str) -> list[Candidato]:
        return [c for c in self.todos() if c.feedback_id == feedback_id]

    def borrar(self, candidato_id: str) -> bool:
        with self._lock:
            conn = self._get()
            with conn:
                cursor = conn.execute("DELETE FROM evaluacion_candidatos WHERE id = ?", (candidato_id,))
        return cursor.rowcount == 1

    def borrar_de_casos(self, case_ids: Sequence[str]) -> int:
        if not case_ids:
            return 0
        marcas = ",".join("?" * len(case_ids))
        with self._lock:
            conn = self._get()
            with conn:
                cursor = conn.execute(
                    f"DELETE FROM evaluacion_candidatos WHERE case_id IN ({marcas})",
                    tuple(case_ids),  # noqa: S608
                )
                conn.execute(f"DELETE FROM evaluacion_aprendizaje WHERE case_id IN ({marcas})", tuple(case_ids))  # noqa: S608
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        return cursor.rowcount

    def reabrir_aceptados(self, casos_reales: Sequence[str]) -> int:
        """Candidates accepted into these (now erased) real cases go back to the queue."""
        with self._lock:
            conn = self._get()
            with conn:
                return reabrir_candidatos_de(conn, casos_reales)

    # --- standing consent per case ---------------------------------------------------------------------------

    def consentimiento(self, case_id: str) -> ConsentimientoAprendizaje | None:
        with self._lock:
            fila = (
                self._get()
                .execute(
                    "SELECT case_id, version, otorgado_el FROM evaluacion_aprendizaje WHERE case_id = ?", (case_id,)
                )
                .fetchone()
            )
        if fila is None:
            return None
        return ConsentimientoAprendizaje(
            case_id=fila["case_id"], version=fila["version"], otorgado_el=datetime.fromisoformat(fila["otorgado_el"])
        )

    def dar_consentimiento(self, case_id: str) -> ConsentimientoAprendizaje:
        registro = ConsentimientoAprendizaje(
            case_id=case_id, version=CONSENTIMIENTO_APRENDIZAJE_VERSION, otorgado_el=datetime.now(UTC)
        )
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute(
                    "INSERT INTO evaluacion_aprendizaje (case_id, version, otorgado_el) VALUES (?, ?, ?) "
                    "ON CONFLICT(case_id) DO UPDATE SET version = excluded.version, otorgado_el = excluded.otorgado_el",
                    (case_id, registro.version, registro.otorgado_el.isoformat()),
                )
        return registro

    def retirar_consentimiento(self, case_id: str) -> int:
        """Withdraw it: the PENDING candidates it produced (votes and signals) are deleted too. Returns how many."""
        pendientes = [c for c in self.de_casos([case_id]) if c.estado == "pendiente" and c.origen != "valoracion"]
        with self._lock:
            conn = self._get()
            with conn:
                conn.execute("DELETE FROM evaluacion_aprendizaje WHERE case_id = ?", (case_id,))
                for c in pendientes:
                    conn.execute("DELETE FROM evaluacion_candidatos WHERE id = ?", (c.id,))
        return len(pendientes)

    def consentimientos(self, case_ids: Sequence[str] | None = None) -> list[ConsentimientoAprendizaje]:
        with self._lock:
            filas = self._get().execute("SELECT case_id, version, otorgado_el FROM evaluacion_aprendizaje").fetchall()
        todos = [
            ConsentimientoAprendizaje(
                case_id=f["case_id"], version=f["version"], otorgado_el=datetime.fromisoformat(f["otorgado_el"])
            )
            for f in filas
        ]
        return todos if case_ids is None else [c for c in todos if c.case_id in set(case_ids)]


_stores: dict[str, CandidatoStore] = {}
_stores_lock = RLock()


def candidatos_para(db_path: str) -> CandidatoStore:
    """One candidate store per cases' database file."""
    with _stores_lock:
        store = _stores.get(db_path)
        if store is None:
            store = _stores[db_path] = CandidatoStore(db_path)
        return store


# --------------------------------------------------------------------------- #
# Building a candidate: masking, money at stake, category, dedupe key
# --------------------------------------------------------------------------- #

# Keys whose values are ids, enums or figures: never masked (a specialist's key is not a person).
_CONSERVAR = frozenset(
    {
        "id",
        "especialista",
        "encargo_id",
        "estado",
        "modo",
        "tipo",
        "origen",
        "ruta",
        "fragment_id",
        "fragment_ids",
        "herramienta",
        "unidad",
        "literal",
        "cifra_id",
        "cifras",
        "solicitante",
        "destinatario",
        "conversacion_id",
        "peticion_id",
        "depende_de",
        "valor",
        "recuerdos_usados",
        "tool",
        "agent",
        "is_error",
        "verdict",
        "confianza",
        "profundidad",
        "ejercicio",
        "duracion_ms",
        "revisiones_sintesis",
        "del_usuario",
        "ignorados",
    }
)


# In the brain's report, ``nombre`` is a specialist's display name («Hipoteca»), not a person's.
_CONSERVAR_CEREBRO = _CONSERVAR | {"nombre"}


def _enmascarar_json(valor: Any, m: Enmascarador, campo: str, conservar: frozenset[str] = _CONSERVAR) -> Any:
    if isinstance(valor, str):
        return m.texto(valor, campo)
    if isinstance(valor, list):
        return [_enmascarar_json(v, m, f"{campo}[{i}]", conservar) for i, v in enumerate(valor)]
    if isinstance(valor, Mapping):
        return {
            k: (v if k in conservar else _enmascarar_json(v, m, f"{campo}.{k}", conservar)) for k, v in valor.items()
        }
    return valor


_IMPORTE_RE = re.compile(r"(-?\d[\d.,]*)\s?(?:€|euros?\b|EUR\b)", re.IGNORECASE)


def importe_en_juego(contenido: ContenidoCandidato) -> Decimal | None:
    """The largest amount in euros the turn talked about: what a wrong answer could cost (triage only)."""
    importes: list[Decimal] = []
    textos = [contenido.respuesta or "", str(contenido.correccion.get("cifra_correcta") or "")]
    for texto in textos:
        for m in _IMPORTE_RE.finditer(texto):
            valor = numero(m.group(1).strip(".,"))
            if valor is not None:
                importes.append(abs(valor))
    cerebro = contenido.cerebro or {}
    for informe in cerebro.get("informes") or []:
        for cifra in (informe or {}).get("cifras") or []:
            if str(cifra.get("unidad") or "").startswith("EUR"):
                try:
                    importes.append(abs(Decimal(str(cifra.get("valor")))))
                except Exception:  # noqa: BLE001 - a malformed figure only loses its weight in the triage
                    continue
    for violacion in (cerebro.get("puerta") or {}).get("violaciones") or []:
        for m in _IMPORTE_RE.finditer(str(violacion.get("literal") or "")):
            valor = numero(m.group(1).strip(".,"))
            if valor is not None:
                importes.append(abs(valor))
    return max(importes) if importes else None


def con_cerebro(contenido: ContenidoCandidato) -> bool:
    """The turn went through the household brain (full report or its summary)."""
    return contenido.cerebro is not None or contenido.cerebro_resumen is not None


def _ruta(contenido: ContenidoCandidato) -> str | None:
    ruta = (contenido.cerebro or contenido.cerebro_resumen or {}).get("ruta")
    return str(ruta) if ruta else None


def _con_fiscal(contenido: ContenidoCandidato) -> bool:
    if _ruta(contenido) == "paso_directo" or "fiscal" in contenido.modos_especialistas:
        return True
    from asistente.nucleo.evaluacion.cifras import herramientas_fiscales

    fiscales = herramientas_fiscales()
    return any(
        str(t.get("tool") or "") in fiscales and str(t.get("tool")) != "buscar_fuentes" for t in contenido.tool_trace
    )


def categoria_sugerida(tipo: TipoFallo, contenido: ContenidoCandidato) -> str:
    """The suite category a reviewer would most likely pick (they can change it)."""
    cerebro = con_cerebro(contenido)
    fiscal = _con_fiscal(contenido)
    if tipo == "cifra_erronea":
        if fiscal:
            return "paso_directo_fiscal" if _ruta(contenido) == "paso_directo" else "calculo_fiscal"
        return "modulo"
    if tipo in ("modulo_erroneo", "repregunta"):
        return "intencion" if cerebro else ("enrutado" if tipo == "modulo_erroneo" else "regresion")
    if tipo in ("afirmacion_inventada", "verificador"):
        return "paso_directo_fiscal" if fiscal and cerebro else "regresion"
    if tipo in ("suma_indebida", "puerta_cifras"):
        return "procedencia" if cerebro else "no_sumar"
    if tipo == "memoria_erronea":
        return "memoria" if cerebro else "regresion"
    return "regresion"


def _normalizar(texto: str) -> str:
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto.casefold()) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\[\]]+", " ", sin_acentos).split())


def huella_grupo(pregunta_enmascarada: str, tipo: str) -> str:
    """The dedupe key: the normalised masked question + the kind of failure."""
    return hashlib.sha256(f"{_normalizar(pregunta_enmascarada)}|{tipo}".encode()).hexdigest()[:20]


def _critica(categoria: str, config: ConfigSuite | None) -> bool:
    if config is None:
        from asistente.nucleo.evaluacion.suite import cargar_config

        config = cargar_config()
    cat = config.categorias.get(categoria)
    return bool(cat and cat.critica)


def prioridad_base(candidato: Candidato) -> tuple[float, list[str]]:
    """The candidate's own score (without frequency) and its explanation."""
    puntos = 0.0
    motivos: list[str] = []
    if candidato.critica:
        puntos += PESO_CRITICA
        motivos.append(f"categoría crítica «{candidato.categoria_sugerida}» (+{PESO_CRITICA:.0f})")
    if candidato.origen in PESO_ORIGEN:
        peso = PESO_ORIGEN[candidato.origen]
        puntos += peso
        motivos.append(
            ("el usuario lo corrigió" if candidato.origen == "valoracion" else "el usuario lo marcó como incorrecto")
            + f" (+{peso:.0f})"
        )
    else:
        peso = PESO_SENAL.get(candidato.tipo_fallo, 5.0)
        puntos += peso
        motivos.append(f"señal automática: {ETIQUETAS_FALLO[candidato.tipo_fallo].lower()} (+{peso:.0f})")
    if candidato.importe_en_juego is not None and candidato.importe_en_juego > 0:
        peso = min(30.0, 6.0 * math.log10(1 + float(candidato.importe_en_juego)))
        puntos += peso
        motivos.append(f"{candidato.importe_en_juego} € en juego (+{peso:.1f})")
    return round(puntos, 2), motivos


def construir_contenido(
    *,
    pregunta: str,
    respuesta: str | None,
    turno: Mapping[str, Any],
    case: Any,
    nombres_extra: Iterable[str] = (),
    correccion: Mapping[str, Any] | None = None,
    senales: Sequence[Senal] = (),
    siguiente_pregunta: str | None = None,
    variante: str | None = None,
) -> ContenidoCandidato:
    """The masked content of a candidate, from the turn's audit event (``agent_answer``) and what the user said."""
    from asistente.nucleo.evaluacion.captura import instantanea_de, modelos_por_proposito

    hechos = [(f.name, f.value) for f in case.facts] if case is not None else []
    m = Enmascarador(nombres_de_perfil(hechos, extra=nombres_extra))
    traza = [t for t in turno.get("tool_trace") or [] if isinstance(t, dict)]
    claims = [c for c in turno.get("claims") or [] if isinstance(c, dict)]
    parcial = False
    if respuesta is None:
        respuesta = turno.get("answer") if isinstance(turno.get("answer"), str) else None
    if respuesta is None and claims:
        respuesta = " ".join(str(c.get("text") or "") for c in claims).strip() or None
        parcial = respuesta is not None
    from asistente.nucleo.evaluacion.captura import informe_del_turno, resumen_del_turno

    informe = informe_del_turno(turno)
    cerebro = informe.model_dump(mode="json") if informe is not None else None
    resumen = resumen_del_turno(turno) if informe is None else None
    modos: dict[str, str | None] = (
        {i.especialista: i.modo for i in informe.informes}
        if informe is not None
        else {str(e): None for e in (resumen or {}).get("especialistas") or []}
    )
    modelos = turno.get("modelos") if isinstance(turno.get("modelos"), dict) else None
    contenido = ContenidoCandidato(
        pregunta=m.texto(pregunta, "pregunta"),
        respuesta=m.texto(respuesta, "respuesta") if respuesta else None,
        respuesta_parcial=parcial,
        siguiente_pregunta=m.texto(siguiente_pregunta, "siguiente_pregunta") if siguiente_pregunta else None,
        correccion={
            k: (m.texto(str(v), f"correccion.{k}") if isinstance(v, str) and k != "tipo" else v)
            for k, v in (correccion or {}).items()
        },
        modo=turno.get("mode") if isinstance(turno.get("mode"), str) else None,
        cerebro=_enmascarar_json(cerebro, m, "cerebro", _CONSERVAR_CEREBRO) if cerebro is not None else None,
        cerebro_resumen=resumen,
        modos_especialistas=modos,
        variante=str(turno.get("variante") or variante or "") or None,
        modelos={str(k): str(v) for k, v in modelos.items()} if modelos else modelos_por_proposito(turno, list(modos)),
        modelos_origen="turno" if modelos else "configuracion",
        senales=[s.model_dump(mode="json") for s in senales],
        tool_trace=[
            {
                k: (v if k in _CONSERVAR else _enmascarar_json(v, m, f"tool_trace[{i}].{k}"))
                for k, v in t.items()
                if k in ("tool", "agent", "arguments", "summary", "is_error")
            }
            for i, t in enumerate(traza)
        ],
        claims=[
            {k: (v if k in _CONSERVAR else _enmascarar_json(v, m, f"claims[{i}].{k}")) for k, v in c.items()}
            for i, c in enumerate(claims)
        ],
        citas=[
            {"fragment_id": str(c.get("fragment_id") or "")}
            for c in turno.get("citations") or []
            if isinstance(c, dict)
        ],
        acciones_pendientes=[
            str(a.get("herramienta")) for a in turno.get("pending_actions") or [] if isinstance(a, dict)
        ],
        # The snapshot is the user's data: every string masked (a fact's «nombre» IS a person's name).
        instantanea=m.valor(instantanea_de(case, traza, list(modos)), "instantanea"),
    )
    contenido.enmascarado = list(m.cambios)
    return contenido


def nuevo_candidato(
    contenido: ContenidoCandidato,
    *,
    tipo: TipoFallo,
    origen: OrigenCandidato,
    case_id: str | None,
    user_id: str | None,
    referencia_turno: str,
    feedback_id: str | None = None,
    config: ConfigSuite | None = None,
) -> Candidato:
    categoria = categoria_sugerida(tipo, contenido)
    # A vague signal (a repeat, an abstention) never claims a critical category by itself: only what the user
    # said, or a specific signal (the figures gate, the verifier), does.
    critica = _critica(categoria, config) and (origen != "senal" or tipo in _SENALES_ESPECIFICAS)
    clave = hashlib.sha256(f"{case_id}|{referencia_turno}|{tipo}|{origen}|{feedback_id or ''}".encode()).hexdigest()
    candidato = Candidato(
        id=f"cand_{uuid4().hex}",
        grupo=huella_grupo(contenido.pregunta, tipo),
        clave=clave,
        case_id=case_id,
        user_id=user_id,
        origen=origen,
        feedback_id=feedback_id,
        referencia_turno=referencia_turno,
        tipo_fallo=tipo,
        categoria_sugerida=categoria,
        critica=critica,
        importe_en_juego=importe_en_juego(contenido),
        creado_el=datetime.now(UTC),
        contenido=contenido,
    )
    candidato.prioridad, candidato.motivos_prioridad = prioridad_base(candidato)
    return candidato


def tipo_de_correccion(tipo: str | None) -> TipoFallo:
    return tipo if tipo in ETIQUETAS_FALLO and tipo not in _SENAL_A_FALLO.values() else "otro"  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Detection of weak signals (only with the owner's standing consent, only after it)
# --------------------------------------------------------------------------- #


def _momento(evento: Mapping[str, Any]) -> datetime | None:
    try:
        momento = datetime.fromisoformat(str(evento.get("event_time")))
    except ValueError:
        return None
    return momento if momento.tzinfo is not None else momento.replace(tzinfo=UTC)


def candidatos_de_senales(
    case: Any,
    eventos: Sequence[dict[str, Any]],
    *,
    desde: datetime,
    user_id: str | None = None,
    nombres_extra: Iterable[str] = (),
    config: ConfigSuite | None = None,
) -> list[Candidato]:
    """One candidate per (turn, weak signal) of the turns after ``desde``. Pure: nothing is stored."""
    turnos = respuestas(eventos)
    salida: list[Candidato] = []
    for indice, turno in enumerate(turnos):
        momento = _momento(turno)
        if momento is None or momento < desde:
            continue
        senales = senales_de_turno(turno, indice)
        siguiente = turnos[indice + 1] if indice + 1 < len(turnos) else None
        if siguiente is not None and es_repregunta(turno, siguiente):
            senales.append(Senal(tipo="repregunta", turno=indice, detalle="el usuario volvió a preguntar"))
        debiles = [s for s in senales if s.tipo in SENALES_DEBILES]
        for tipo_senal in sorted({s.tipo for s in debiles}):
            tipo = _SENAL_A_FALLO[tipo_senal]
            contenido = construir_contenido(
                pregunta=str(turno.get("input") or ""),
                respuesta=None,
                turno=turno,
                case=case,
                nombres_extra=nombres_extra,
                senales=[s for s in senales],
                siguiente_pregunta=str(siguiente.get("input") or "") if tipo == "repregunta" and siguiente else None,
            )
            if not contenido.pregunta.strip():
                continue
            salida.append(
                nuevo_candidato(
                    contenido,
                    tipo=tipo,
                    origen="senal",
                    case_id=getattr(case, "case_id", None),
                    user_id=user_id,
                    referencia_turno=str(turno.get("event_time") or indice),
                    config=config,
                )
            )
    return salida


def detectar_en_caso(
    store: CandidatoStore,
    cases: Any,
    case: Any,
    *,
    user_id: str | None = None,
    nombres_extra: Iterable[str] = (),
) -> list[Candidato]:
    """Store the new weak-signal candidates of a case whose owner consented (idempotent). Never raises."""
    try:
        consentimiento = store.consentimiento(case.case_id)
        if consentimiento is None:
            return []
        nuevos = candidatos_de_senales(
            case,
            cases.list_audit_events(case.case_id),
            desde=consentimiento.otorgado_el,
            user_id=user_id or getattr(case, "user_id", None),
            nombres_extra=nombres_extra,
        )
        return [c for c in (store.anadir(n) for n in nuevos) if c is not None]
    except Exception:  # a detection failure never breaks the queue (or the feedback that triggered it)
        logger.exception("Could not detect the weak signals of a case")
        return []


# --------------------------------------------------------------------------- #
# The queue: dedupe and triage
# --------------------------------------------------------------------------- #


def agrupar(candidatos: Iterable[Candidato], *, estado: EstadoCandidato | None = "pendiente") -> list[GrupoCandidatos]:
    """Candidates deduplicated into groups, highest priority first (ties: most recent first)."""
    por_grupo: dict[str, list[Candidato]] = {}
    for c in candidatos:
        if estado is not None and c.estado != estado:
            continue
        por_grupo.setdefault(c.grupo, []).append(c)
    grupos: list[GrupoCandidatos] = []
    for grupo, lista in por_grupo.items():
        representante = max(lista, key=lambda c: (c.prioridad, c.creado_el))
        frecuencia = len(lista)
        extra = 10.0 * math.log2(frecuencia) if frecuencia > 1 else 0.0
        motivos = list(representante.motivos_prioridad)
        if extra:
            motivos.append(f"ha pasado {frecuencia} veces (+{extra:.1f})")
        grupos.append(
            GrupoCandidatos(
                grupo=grupo,
                representante=representante,
                ids=[c.id for c in sorted(lista, key=lambda c: c.creado_el)],
                frecuencia=frecuencia,
                prioridad=round(representante.prioridad + extra, 2),
                motivos_prioridad=motivos,
                estado=representante.estado,
            )
        )
    return sorted(grupos, key=lambda g: (-g.prioridad, -g.representante.creado_el.timestamp()))


# --------------------------------------------------------------------------- #
# Acceptance: the reviewer's expected behaviour → deterministic expectations
# --------------------------------------------------------------------------- #


class CifraEsperadaEdicion(_Modelo):
    """The expected figure, as a calculator recipe: never a number typed by hand.

    ``patron`` may be left out: the line of the calculator's output holding ``referencia`` (the user's figure, by
    default) is located and THAT line is read on every run — the figure itself always comes from the calculator.
    """

    herramienta: str = Field(min_length=1)
    argumentos: dict[str, Any] = Field(default_factory=dict)
    patron: str | None = None
    referencia: str | None = None
    # The specialist that must be credited with the figure (brain cases: the figures gate's attribution).
    especialista: str | None = None


class ComportamientoEsperado(_Modelo):
    especialistas_obligatorios: list[str] = Field(default_factory=list)
    especialistas_prohibidos: list[str] = Field(default_factory=list)
    citas_minimas: int = Field(default=0, ge=0, le=20)
    autoridad_citas: str | None = None
    cifras_prohibidas: list[str] = Field(default_factory=list, max_length=20)
    cifra_esperada: CifraEsperadaEdicion | None = None
    debe_abstenerse: bool = False
    debe_aclarar: bool = False
    modos_permitidos: list[Literal["verified", "partially_verified", "unverified", "clarification", "abstained"]] = (
        Field(default_factory=list)
    )
    puerta_sin_violaciones: bool = False
    textos_obligatorios: list[str] = Field(default_factory=list, max_length=20)
    textos_prohibidos: list[str] = Field(default_factory=list, max_length=20)


class EdicionCaso(_Modelo):
    """What the reviewer sends to accept a candidate (everything optional but the category and the behaviour)."""

    categoria: str = Field(pattern=r"^[a-z0-9_]+$")
    descripcion: str = Field(default="", max_length=500)
    # The question as it will be asked in the evaluation (default: the masked one).
    pregunta: str | None = Field(default=None, max_length=4000)
    # The snapshot facts (default: the masked snapshot). Replace sensitive values by fictitious ones.
    hechos: dict[str, Any] | None = None
    esperado: ComportamientoEsperado = Field(default_factory=ComportamientoEsperado)
    criterios_juez: list[str] = Field(default_factory=list, max_length=10)
    # Accept even if the expectations do not fail on the captured answer (the warning is kept with the case).
    aunque_no_reproduzca: bool = False


class AceptacionRechazadaError(ValueError):
    """The case cannot be accepted as it is. ``problemas`` are Spanish, for the reviewer."""

    def __init__(self, problemas: list[str]) -> None:
        self.problemas = problemas
        super().__init__("No se puede aceptar el caso:\n- " + "\n- ".join(problemas))


_IMPORTE_LINEA = r"-?\d{1,3}(?:\.\d{3})*,\d{2} €"


def _modulos_de_contenido(contenido: ContenidoCandidato) -> list[str]:
    from asistente.nucleo.modulos.registro import registro

    claves = {
        str(t["tool"]).removeprefix("consultar_")
        for t in contenido.tool_trace
        if str(t.get("tool") or "").startswith("consultar_") and (t.get("agent") or "asesor") == "asesor"
    }
    claves |= set(contenido.modos_especialistas)
    conocidos = registro().modulos
    return sorted(c for c in claves if c and c != "fiscal" and c in conocidos)


def _calculo(edicion: CifraEsperadaEdicion, expediente: Expediente, referencia: str | None) -> CalculoEsperado:
    from asistente.nucleo.evaluacion.captura import _linea_de_cifra

    patron = edicion.patron
    if patron is None:
        if not referencia:
            raise AceptacionRechazadaError(
                ["Para la cifra esperada, indica el patrón o una cifra de referencia que localice la línea."]
            )
        try:
            salida = salida_de_herramienta(edicion.herramienta, dict(edicion.argumentos), expediente)
        except Exception as exc:  # noqa: BLE001 - reported to the reviewer
            raise AceptacionRechazadaError(
                [f"La calculadora {edicion.herramienta} no se puede ejecutar: {exc}"]
            ) from exc
        patron = _linea_de_cifra(salida, referencia)
        if patron is None:
            raise AceptacionRechazadaError(
                [
                    f"La calculadora {edicion.herramienta} no da {referencia} con esos datos: una cifra esperada solo "
                    "puede salir de una calculadora. Revisa los argumentos o los datos del expediente."
                ]
            )
    try:
        return CalculoEsperado(herramienta=edicion.herramienta, argumentos=dict(edicion.argumentos), patron=patron)
    except ValueError as exc:
        raise AceptacionRechazadaError([f"Patrón de la cifra no válido: {exc}"]) from exc


def caso_desde_candidato(candidato: Candidato, edicion: EdicionCaso, *, revisor: Revisor) -> CasoEvaluacion:
    """The suite case the reviewer described. Raises :class:`AceptacionRechazadaError` (Spanish problems)."""
    from asistente.nucleo.evaluacion.captura import referencia_anonima

    contenido = candidato.contenido
    cerebro = con_cerebro(contenido)
    e = edicion.esperado
    instantanea = contenido.instantanea or {}
    expediente = Expediente(
        tax_period=str(instantanea.get("tax_period") or "2025"),
        jurisdiction=str(instantanea.get("jurisdiction") or "ES"),
        hechos=dict(edicion.hechos if edicion.hechos is not None else instantanea.get("hechos") or {}),
    )
    expectativas: list[Any] = []
    if e.especialistas_obligatorios or e.especialistas_prohibidos:
        if cerebro:
            expectativas.append(
                ExpEspecialistas(
                    tipo="especialistas",
                    incluye=list(e.especialistas_obligatorios),
                    excluye=list(e.especialistas_prohibidos),
                    fuente="informes",
                )
            )
        else:
            expectativas += [
                ExpModuloConsultado(tipo="modulo_consultado", modulo=x) for x in e.especialistas_obligatorios
            ]
            expectativas += [
                ExpModuloNoConsultado(tipo="modulo_no_consultado", modulo=x) for x in e.especialistas_prohibidos
            ]
    if e.citas_minimas:
        expectativas.append(ExpCitas(tipo="citas", minimo=e.citas_minimas, autoridad=e.autoridad_citas))
    expectativas += [
        ExpCifraAusente(tipo="cifra_ausente", valor=v, motivo="cifra equivocada que dio el asistente")
        for v in e.cifras_prohibidas
    ]
    if e.cifra_esperada is not None:
        referencia = e.cifra_esperada.referencia or contenido.correccion.get("cifra_correcta")
        calculo = _calculo(e.cifra_esperada, expediente, referencia)
        expectativas.append(ExpCifra(tipo="cifra", nombre="cifra_esperada", calculo=calculo))
        if e.cifra_esperada.especialista and cerebro:
            expectativas.append(
                ExpCifraAtribuida(
                    tipo="cifra_atribuida", nombre="cifra_esperada", especialista=e.cifra_esperada.especialista
                )
            )
    if e.debe_abstenerse:
        expectativas.append(ExpModo(tipo="modo", permitidos=["abstained"]))
    if e.debe_aclarar:
        if cerebro:
            expectativas.append(ExpIntencion(tipo="intencion", aclaracion=True))
        else:
            expectativas.append(ExpModo(tipo="modo", permitidos=["clarification"]))
    if e.modos_permitidos:
        expectativas.append(ExpModo(tipo="modo", permitidos=list(e.modos_permitidos)))
    if e.puerta_sin_violaciones and cerebro:
        expectativas.append(ExpPuerta(tipo="puerta", sin_violaciones=True))
    try:
        expectativas += [ExpTextoPresente(tipo="texto_presente", patron=t) for t in e.textos_obligatorios]
        expectativas += [ExpTextoAusente(tipo="texto_ausente", patron=t) for t in e.textos_prohibidos]
    except ValueError as exc:
        raise AceptacionRechazadaError([str(exc)]) from exc
    referencia_juez = str(contenido.correccion.get("respuesta_correcta") or "").strip() or None
    rubrica = (
        Rubrica(criterios=list(edicion.criterios_juez), referencia=referencia_juez) if edicion.criterios_juez else None
    )
    if not expectativas and rubrica is None:
        raise AceptacionRechazadaError(["Indica al menos un comportamiento esperado (o un criterio para el juez)."])
    hoy = date.today()
    try:
        return CasoEvaluacion(
            id=f"real-{hoy.strftime('%Y%m%d')}-{referencia_anonima(candidato.id)[3:11]}",
            categoria=edicion.categoria,
            descripcion=edicion.descripcion or f"{ETIQUETAS_FALLO[candidato.tipo_fallo]} (caso real revisado).",
            pregunta=edicion.pregunta or contenido.pregunta,
            modulos=_modulos_de_contenido(contenido),
            expediente=expediente,
            expectativas=expectativas,
            rubrica=rubrica,
            requiere=["cerebro"] if cerebro else [],
            origen=Origen(
                tipo="senal" if candidato.origen == "senal" else "feedback",
                referencia=referencia_anonima(candidato.id),
                etiquetas=[candidato.tipo_fallo],
                revisado_por=revisor,
                revisado_el=hoy,
            ),
        )
    except ValueError as exc:
        raise AceptacionRechazadaError([str(exc)]) from exc


def _argumentos(entrada: Mapping[str, Any]) -> dict[str, Any]:
    bruto = entrada.get("arguments")
    return dict(bruto) if isinstance(bruto, dict) else {}


def ejecucion_capturada(contenido: ContenidoCandidato) -> Ejecucion:
    """The run that actually happened, rebuilt from the candidate (for the negative control)."""
    cerebro = None
    if contenido.cerebro is not None:
        try:
            cerebro = InformeCerebro.model_validate(contenido.cerebro)
        except ValueError:
            cerebro = None
    return Ejecucion(
        respuesta=contenido.respuesta or "",
        modo=contenido.modo or "unverified",
        herramientas=[
            LlamadaRegistrada(
                herramienta=str(t.get("tool") or ""),
                agente=str(t.get("agent") or "asesor"),
                argumentos=_argumentos(t),
                resumen=str(t.get("summary") or ""),
                es_error=bool(t.get("is_error")),
            )
            for t in contenido.tool_trace
        ],
        citas=[CitaRegistrada(fragment_id=str(c.get("fragment_id") or "")) for c in contenido.citas],
        acciones_pendientes=list(contenido.acciones_pendientes),
        cerebro=cerebro,
    )


def problemas_de_anonimato(caso: CasoEvaluacion, nombres: Sequence[str]) -> list[str]:
    from asistente.nucleo.evaluacion.captura import comprobar_anonimizado

    problemas = comprobar_anonimizado(caso)
    texto = json.dumps(caso.model_dump(mode="json", exclude={"origen"}), ensure_ascii=False)
    presentes = nombres_en(texto, nombres)
    if presentes:
        problemas.append(f"contiene {len(presentes)} nombre(s) del perfil del usuario")
    return problemas


def aceptar(
    store: CandidatoStore,
    candidato: Candidato,
    edicion: EdicionCaso,
    *,
    revisor: Revisor,
    cases: Any = None,
    visible: Callable[[Candidato], bool] = lambda _c: True,
    nombres: Sequence[str] = (),
    config: ConfigSuite | None = None,
) -> CasoReal:
    """Accept a candidate (and its visible duplicates) as a private regression case.

    Raises :class:`AceptacionRechazadaError` with every problem when it cannot be accepted as it is.
    """
    from asistente.nucleo.evaluacion.suite import cargar_config, cifras_esperadas, comprobar, validar

    candidato = store.get(candidato.id) or candidato  # its current state, not the caller's copy
    if candidato.estado != "pendiente":
        raise AceptacionRechazadaError([f"El caso candidato ya está «{candidato.estado}»."])
    config = config or cargar_config()
    if edicion.categoria not in config.categorias:
        raise AceptacionRechazadaError([f"Categoría desconocida «{edicion.categoria}»."])
    caso = caso_desde_candidato(candidato, edicion, revisor=revisor)
    problemas = problemas_de_anonimato(caso, nombres)
    if problemas:
        raise AceptacionRechazadaError(["Parece contener datos personales: " + "; ".join(problemas)])
    validacion = validar(casos=[caso])
    if not validacion.ok:
        raise AceptacionRechazadaError(validacion.errores)
    avisos: list[str] = []
    fallos = comprobar(caso, ejecucion_capturada(candidato.contenido), cifras_esperadas(caso))
    # A brain expectation «fails» on a capture that only kept the brain's summary because the report is missing,
    # not because the mistake is caught: that does not count as reproducing it.
    fallos = [f for f in fallos if _SIN_INFORME not in f]
    if candidato.contenido.cerebro is None and candidato.contenido.cerebro_resumen is not None:
        avisos.append(
            "La auditoría solo guardó un resumen del cerebro: sus comprobaciones no se han podido contrastar con lo "
            "que pasó."
        )
    if not fallos:
        aviso = (
            "Con estas expectativas, la respuesta que se dio aprobaría: no capturan el error. Concreta el "
            "comportamiento esperado (o acéptalo igualmente si la respuesta original no se guardó completa)."
        )
        if not edicion.aunque_no_reproduzca:
            raise AceptacionRechazadaError([aviso])
        avisos.append(aviso)
    if candidato.contenido.respuesta_parcial or candidato.contenido.respuesta is None:
        avisos.append("La respuesta original no está completa en la auditoría: el control negativo es parcial.")
    privado = store_de(store.db_path)
    real = CasoReal(
        id=caso.id,
        case_id=candidato.case_id,
        user_id=candidato.user_id,
        candidato_id=candidato.id,
        grupo=candidato.grupo,
        feedback_id=candidato.feedback_id,
        caso=caso,
        aceptado_el=datetime.now(UTC),
        aceptado_por=revisor,
        avisos=avisos,
        guion_capturado=guion_de_captura(candidato.contenido),
    )
    if privado.get(real.id) is not None:
        raise AceptacionRechazadaError([f"Ya hay un caso aceptado con id «{real.id}»."])
    privado.guardar(real)
    ahora = datetime.now(UTC)
    for c in store.del_grupo(candidato.grupo):
        if c.estado != "pendiente" or not (c.id == candidato.id or visible(c)):
            continue
        c.estado, c.revisado_el, c.revisado_por, c.caso_aceptado = "aceptado", ahora, revisor, real.id
        store.actualizar(c)
        _contar_al_usuario(cases, c, {"estado": "aceptado", "caso_real": real.id})
        _marcar_feedback(store.db_path, c, "promovido", real.id)
    return real


def rechazar(
    store: CandidatoStore,
    candidato: Candidato,
    motivo: str,
    *,
    revisor: Revisor,
    cases: Any = None,
    visible: Callable[[Candidato], bool] = lambda _c: True,
) -> list[Candidato]:
    """Reject a candidate and its visible duplicates, with the reviewer's reason. Returns those changed."""
    motivo = " ".join(motivo.split())
    if not motivo:
        raise AceptacionRechazadaError(["Indica por qué se rechaza."])
    candidato = store.get(candidato.id) or candidato  # its current state, not the caller's copy
    if candidato.estado != "pendiente":
        raise AceptacionRechazadaError([f"El caso candidato ya está «{candidato.estado}»."])
    ahora = datetime.now(UTC)
    cambiados: list[Candidato] = []
    for c in store.del_grupo(candidato.grupo):
        if c.estado != "pendiente" or not (c.id == candidato.id or visible(c)):
            continue
        c.estado, c.revisado_el, c.revisado_por, c.motivo_rechazo = "rechazado", ahora, revisor, motivo[:500]
        store.actualizar(c)
        _contar_al_usuario(cases, c, {"estado": "rechazado"})
        _marcar_feedback(store.db_path, c, "descartado", None)
        cambiados.append(c)
    return cambiados


def _contar_al_usuario(cases: Any, candidato: Candidato, datos: dict[str, Any]) -> None:
    """Tell the user's audit trail what was decided on their candidate (visible in their export)."""
    if cases is None or candidato.case_id is None:
        return
    try:
        cases.append_audit_event(
            candidato.case_id, {"event_type": "evaluacion_revision", "candidato_id": candidato.id, **datos}
        )
    except KeyError:  # the case was erased meanwhile: nothing to tell
        pass


def _marcar_feedback(db_path: str, candidato: Candidato, estado: str, caso: str | None) -> None:
    if candidato.feedback_id is None:
        return
    from asistente.nucleo.evaluacion.captura import FeedbackStore

    feedback = FeedbackStore(db_path)
    try:
        registro = feedback.get(candidato.feedback_id)
        if registro is not None and registro.estado == "pendiente":
            registro.estado = estado  # type: ignore[assignment]
            registro.revisado_el = datetime.now(UTC)
            registro.caso_suite = caso
            feedback.actualizar(registro)
    finally:
        feedback.close()


# --------------------------------------------------------------------------- #
# Erasure, redaction, export (the case store's hooks and the account)
# --------------------------------------------------------------------------- #


def _tabla(conn: sqlite3.Connection, nombre: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (nombre,)).fetchone() is not None
    )


def reabrir_candidatos_de(conn: sqlite3.Connection, casos_reales: Sequence[str]) -> int:
    """Candidates (of other users) accepted into a real case that was just erased go back to the queue."""
    if not casos_reales or not _tabla(conn, "evaluacion_candidatos"):
        return 0
    cambiados = 0
    filas = conn.execute("SELECT id, payload_json FROM evaluacion_candidatos WHERE estado = 'aceptado'").fetchall()
    for fila in filas:
        candidato = Candidato.model_validate_json(fila[1])
        if candidato.caso_aceptado in casos_reales:
            candidato.estado, candidato.caso_aceptado, candidato.revisado_el, candidato.revisado_por = (
                "pendiente",
                None,
                None,
                None,
            )
            conn.execute(
                "UPDATE evaluacion_candidatos SET estado = ?, payload_json = ? WHERE id = ?",
                (candidato.estado, candidato.model_dump_json(), candidato.id),
            )
            cambiados += 1
    return cambiados


def _ruta_de(conn: sqlite3.Connection) -> str:
    fila = conn.execute("PRAGMA database_list").fetchone()
    return str(fila[2] or "") if fila is not None else ""


def al_guardar_caso(conn: sqlite3.Connection, case: Any) -> None:
    """``save_case`` hook: a fact the user deleted is redacted from the candidates and the accepted cases.

    Never blocks the save (a failure is logged): the next save retries the redaction.
    """
    try:
        actuales = {f.name for f in case.facts}
        if _tabla(conn, "evaluacion_candidatos"):
            from asistente.nucleo.decision.audit import redactar_hecho

            filas = conn.execute(
                "SELECT id, payload_json FROM evaluacion_candidatos WHERE case_id = ?", (case.case_id,)
            ).fetchall()
            for fila in filas:
                candidato = Candidato.model_validate_json(fila[1])
                hechos = dict((candidato.contenido.instantanea or {}).get("hechos") or {})
                sobran = [n for n in hechos if n not in actuales]
                if not sobran:
                    continue
                datos: Any = candidato.contenido.model_dump(mode="json")
                for nombre in sobran:
                    datos, _ = redactar_hecho(datos, nombre)
                candidato.contenido = ContenidoCandidato.model_validate(datos)
                conn.execute(
                    "UPDATE evaluacion_candidatos SET payload_json = ? WHERE id = ?",
                    (candidato.model_dump_json(), candidato.id),
                )
        from asistente.nucleo.evaluacion.privado import redactar_hechos_borrados

        ruta = _ruta_de(conn)
        if ruta:  # an in-memory cases' database has no private file next to it
            redactar_hechos_borrados(ruta, case.case_id, actuales)
    except Exception:
        logger.exception("Could not redact a deleted fact from the evaluation candidates")


def al_borrar_caso(conn: sqlite3.Connection, case_id: str) -> None:
    """``delete_case`` hook: the candidates, the consent and the accepted cases go with the case.

    An error aborts the erasure (the case is still listed, the request can be retried)."""
    from asistente.nucleo.evaluacion import privado

    if _tabla(conn, "evaluacion_candidatos"):
        conn.execute("DELETE FROM evaluacion_candidatos WHERE case_id = ?", (case_id,))
    if _tabla(conn, "evaluacion_aprendizaje"):
        conn.execute("DELETE FROM evaluacion_aprendizaje WHERE case_id = ?", (case_id,))
    privado.al_borrar_caso(conn, case_id)


def instalar() -> None:
    """Hook the redaction and the erasure into the case store (idempotent; done when this module is imported)."""
    from asistente.nucleo.decision import state_store_sqlite as almacen

    if al_guardar_caso not in almacen.AL_GUARDAR:
        almacen.AL_GUARDAR.append(al_guardar_caso)
    if al_borrar_caso not in almacen.AL_BORRAR:
        almacen.AL_BORRAR.append(al_borrar_caso)


def exportar_usuario(db_path: str, *, user_id: str | None, case_ids: Sequence[str]) -> dict[str, Any]:
    """Everything the learning loop keeps about a user (for ``/me/export``)."""
    from asistente.nucleo.evaluacion import privado

    store = candidatos_para(db_path)
    return {
        "aprendizaje_automatico": [c.model_dump(mode="json") for c in store.consentimientos(list(case_ids))],
        "candidatos": [c.model_dump(mode="json") for c in store.de_casos(list(case_ids))],
        "casos_aceptados": privado.exportar(db_path, user_id=user_id, case_ids=list(case_ids)),
    }


def borrar_de_usuario(db_path: str, *, user_id: str | None, case_ids: Sequence[str]) -> dict[str, int]:
    """Erase everything the learning loop keeps about a user (account deletion)."""
    from asistente.nucleo.evaluacion import privado

    store = candidatos_para(db_path)
    aceptados = privado.borrar_de_usuario(db_path, user_id=user_id, case_ids=list(case_ids))
    candidatos = store.borrar_de_casos(list(case_ids))
    reabiertos = store.reabrir_aceptados(aceptados)
    return {"candidatos": candidatos, "casos_aceptados": len(aceptados), "reabiertos": reabiertos}


instalar()

__all__ = [
    "CONSENTIMIENTO_APRENDIZAJE_TEXTO",
    "CONSENTIMIENTO_APRENDIZAJE_VERSION",
    "ETIQUETAS_FALLO",
    "PESO_CRITICA",
    "PESO_ORIGEN",
    "PESO_SENAL",
    "AceptacionRechazadaError",
    "Candidato",
    "CandidatoStore",
    "CifraEsperadaEdicion",
    "ComportamientoEsperado",
    "ConsentimientoAprendizaje",
    "ContenidoCandidato",
    "EdicionCaso",
    "GrupoCandidatos",
    "Revisor",
    "TipoFallo",
    "aceptar",
    "agrupar",
    "al_borrar_caso",
    "al_guardar_caso",
    "borrar_de_usuario",
    "candidatos_de_senales",
    "candidatos_para",
    "caso_desde_candidato",
    "categoria_sugerida",
    "construir_contenido",
    "detectar_en_caso",
    "ejecucion_capturada",
    "exportar_usuario",
    "huella_grupo",
    "identificadores_en",
    "importe_en_juego",
    "instalar",
    "nuevo_candidato",
    "prioridad_base",
    "problemas_de_anonimato",
    "reabrir_candidatos_de",
    "rechazar",
    "tipo_de_correccion",
]

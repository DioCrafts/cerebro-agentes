"""The household brain: understand → consult (in parallel) → write ONE answer → check it → hand it over.

:class:`Cerebro` has the advisor's ``run`` signature (plus ``conversacion_id``) and returns the same
``AgentResult``, with ``cerebro`` = :class:`~asistente.nucleo.cerebro.contrato.InformeCerebro`. One turn:

1. **Memory** — the conversation memory's block for this question (``ProveedorMemoria.recuperar``; never raises).
2. **Understanding** — ``cerebro.intencion``: rules first, the small model only when they are unsure; low
   confidence becomes ONE clarifying question.
3. **Fast paths** (no extra model call):

   * a clarification, or a domain whose module is switched off → answered directly, no model;
   * ``charla`` / ``memoria`` / a task with no domain → the brain alone (one call, its own tools);
   * ONE specialist and it is the tax one → **paso directo**: the advisor's own verified answer IS the answer
     (mode, citations, claims, team report unchanged; only the brain's report is attached).

4. **Collaboration** — otherwise the encargos go to the collaboration runtime (``cerebro.colaboracion``), which
   runs them as a DAG in parallel and lets a specialist ask another for a datum through the brain.
5. **Synthesis** — the brain (purpose ``cerebro``) writes one answer from the reports, passed as DATA with their
   ``[F#]`` and their figures table. It may consult again (``consultar_especialistas``) and use its own tools
   (tasks, research, planner, decisions).
6. **Gates**, on every draft, before the user sees it:

   * the **figures gate** (``revisar_cifras``): every number comes from a specialist's tool or the user's words, no
     figure is a sum/difference of different specialists' figures that no tool combined, attribution is right.
     WHY: the synthesis is the one place where a model holds figures from several calculators at once, so it is
     where an «obvious» total or a mis-attributed figure would appear — code checks it, not the prompt;
   * the **verifier** over every claim citing a fragment (the advisor's ``Verifier``): the synthesis may restate
     a legal rule, and a restated rule must still say what its source says.

   A failing draft goes back with a precise list of problems, at most ``CEREBRO_MAX_REVISIONES`` times. If it still
   fails, the brain never shows a confident wrong figure: the offending figures are withdrawn from the text (with
   a warning), or — when the draft as a whole cannot be backed — the specialists' own reports are shown as they
   are. The final mode is never better than the worst specialist's.

Every model call goes through a metered provider with its purpose; worker threads (the runtime's) copy the
context. A failing optional part (memory, the intent model, a store) degrades; it never fails the turn.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import logging
import re
import time
from typing import Any

from asistente.conocimiento.llm.security import neutralizar_inyeccion
from asistente.nucleo.agent import enrutado
from asistente.nucleo.agent.agent import (
    TRUNCATED_WARNING,
    AgentMode,
    AgentResult,
    TaxAgent,
    ToolTraceEntry,
    _doctrine_issues,
)
from asistente.nucleo.agent.evidence import EvidenceLedger, extract_cited_ids
from asistente.nucleo.agent.procedencia import mensajes_del_usuario
from asistente.nucleo.agent.providers.base import LLMProvider, ProviderError, ToolResult
from asistente.nucleo.agent.tools import ExtraTool, ToolOutcome
from asistente.nucleo.agent.verifier import ClaimCheck, ResponseType, VerificationReport, Verifier
from asistente.nucleo.cerebro.contrato import (
    CLAVE_CEREBRO,
    CLAVE_FISCAL,
    EVENTO_ESPECIALISTA,
    EVENTO_PLAN,
    EVENTO_PUERTA,
    EVENTO_SINTESIS,
    AccionesDelTurno,
    BloqueMemoria,
    Cifra,
    ContextoEncargo,
    ContextoTurno,
    Encargo,
    Especialista,
    ExpedienteCompartido,
    InformeCerebro,
    InformeEspecialista,
    Intencion,
    MemoriaNula,
    PeticionDato,
    ProveedorMemoria,
    RespuestaPeticion,
    ResultadoPuerta,
    ResumenInforme,
    RutaRespuesta,
    Violacion,
)
from asistente.nucleo.cerebro.fiscal import NOMBRE_FISCAL, PROPOSITO_FISCAL, EspecialistaFiscal
from asistente.nucleo.cerebro.herramientas import (
    CONSULTAR,
    encargos_de_llamada,
    herramientas_del_cerebro,
    spec_consultar,
)
from asistente.nucleo.cerebro.intencion import Comprension, Entendimiento, FichaEspecialista, es_paso_directo
from asistente.nucleo.cerebro.prompts import (
    ADDENDUM_ACCION,
    ADDENDUM_CHARLA,
    ADDENDUM_MEMORIA,
    CEREBRO_SYSTEM_PROMPT,
    FORZAR_RESPUESTA,
    REVISION_SINTESIS,
    SINTESIS_SYSTEM_PROMPT,
    prompt,
)
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.modulos.contrato import Modulo
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

# Extra model turns granted to act on a failed gate (consult again + rewrite).
_PASOS_POR_REVISION = 3
_RANGO = {
    AgentMode.VERIFIED: 4,
    AgentMode.PARTIALLY_VERIFIED: 3,
    AgentMode.UNVERIFIED: 2,
    AgentMode.CLARIFICATION: 1,
    AgentMode.ABSTAINED: 0,
}
MARCA_RETIRADA = "[cifra retirada: no sale de ningún cálculo]"
AVISO_PUERTA_CAIDA = "No se pudo comprobar que las cifras de la respuesta salgan de los cálculos; revísalas."
REFUSAL = "El modelo ha declinado responder a esta consulta."

FabricaEspecialistas = Callable[[Sequence[Modulo]], Mapping[str, Especialista]]


def _peor(*modos: AgentMode | None) -> AgentMode:
    presentes = [m for m in modos if m is not None]
    return min(presentes, key=lambda m: _RANGO[m]) if presentes else AgentMode.UNVERIFIED


def tope_de_informes(informes: Sequence[InformeEspecialista]) -> AgentMode | None:
    """The best mode the final answer may have given the specialists' reports.

    A verified specialist does not limit it; a partially verified or unverified one caps it at its own mode. A
    specialist that could not answer, asked the user for a datum or abstained leaves a part of the question
    unanswered: the answer is at most partially verified (and says which part is missing).
    """
    tope: AgentMode | None = None
    for informe in informes:
        modo: AgentMode | None = None
        if informe.estado != "ok":
            modo = AgentMode.PARTIALLY_VERIFIED
        elif informe.modo:
            try:
                propio = AgentMode(informe.modo)
            except ValueError:
                propio = AgentMode.UNVERIFIED
            modo = (
                AgentMode.PARTIALLY_VERIFIED
                if propio in (AgentMode.CLARIFICATION, AgentMode.ABSTAINED)
                else (None if propio is AgentMode.VERIFIED else propio)
            )
        if modo is not None:
            tope = modo if tope is None else _peor(tope, modo)
    return tope


def _resumen(informe: InformeEspecialista) -> ResumenInforme:
    return ResumenInforme(
        encargo_id=informe.encargo_id,
        especialista=informe.especialista,
        nombre=informe.nombre,
        estado=informe.estado,
        modo=informe.modo,
        cifras=list(informe.cifras),
        fragment_ids=list(informe.fragment_ids),
        duracion_ms=informe.duracion_ms,
    )


def _sumar(destino: dict[str, int], uso: Mapping[str, int]) -> None:
    for clave, valor in uso.items():
        if isinstance(valor, int):
            destino[clave] = destino.get(clave, 0) + valor


def _traza(informes: Sequence[InformeEspecialista]) -> list[ToolTraceEntry]:
    entradas: list[ToolTraceEntry] = []
    for informe in informes:
        for crudo in informe.traza:
            try:
                datos = dict(crudo)
                datos.setdefault("agent", f"especialista_{informe.especialista}")
                entradas.append(ToolTraceEntry.model_validate(datos))
            except Exception:  # a malformed entry is left out of the audit trail, never breaks the turn
                logger.debug("trace entry of %s skipped", informe.especialista)
    return entradas


# --------------------------------------------------------------------------------------------------------------
# The collaboration runtime (agent D), with a minimal sequential fallback
# --------------------------------------------------------------------------------------------------------------


class _PizarraLocal:
    """The fallback switchboard: requests between specialists are answered «sin dato» (no runtime, no requests)."""

    def __init__(self) -> None:
        self._n = 0

    def pedir(self, solicitante: str, destinatario: str, dato: str, motivo: str = "") -> RespuestaPeticion:
        self._n += 1
        return RespuestaPeticion(peticion_id=f"p{min(self._n, 99)}", estado="sin_dato")


class _DespachadorLocal:
    """Runs a plan in dependency order, one encargo at a time, when the collaboration runtime is unavailable."""

    def __init__(self, especialistas: Mapping[str, Especialista], *, max_paralelo: int, emit: Callable[..., None]):
        self._especialistas = especialistas
        self._emit = emit
        self._pizarra = _PizarraLocal()
        self._hechos: dict[str, InformeEspecialista] = {}

    def ejecutar(self, encargos: Sequence[Encargo], contexto: ContextoTurno) -> list[InformeEspecialista]:
        pendientes = list(encargos)
        salida: list[InformeEspecialista] = []
        while pendientes:
            listo = next(e for e in pendientes if all(d in self._hechos for d in e.depende_de))
            pendientes.remove(listo)
            especialista = self._especialistas[listo.especialista]
            self._emit(
                EVENTO_ESPECIALISTA,
                clave=listo.especialista,
                nombre=especialista.nombre,
                encargo_id=listo.id,
                estado="start",
            )
            try:
                informe = especialista.atender(
                    ContextoEncargo(
                        turno=contexto,
                        encargo=listo,
                        pizarra=self._pizarra,
                        previos=[self._hechos[d] for d in listo.depende_de],
                    )
                )
            except Exception:
                logger.exception("specialist %s failed", listo.especialista)
                informe = InformeEspecialista(
                    encargo_id=listo.id, especialista=listo.especialista, nombre=especialista.nombre, estado="error"
                )
            self._hechos[listo.id] = informe
            salida.append(informe)
            self._emit(
                EVENTO_ESPECIALISTA,
                clave=listo.especialista,
                nombre=especialista.nombre,
                encargo_id=listo.id,
                estado="done",
            )
        return salida


def _runtime() -> Any | None:
    try:
        from asistente.nucleo.cerebro import colaboracion
    except Exception:
        logger.exception("collaboration runtime unavailable")
        return None
    return colaboracion


# Where each piece of the collaboration runtime may live: the package's exports first, then its modules.
_SUBMODULOS = ("puerta", "despachador", "pizarra", "material", "sintesis", "especialista", "especialistas")


def de_la_colaboracion(nombre: str) -> Any | None:
    """A public name of the collaboration runtime (``cerebro.colaboracion``), or None when it is not there."""
    colaboracion = _runtime()
    if colaboracion is None:
        return None
    valor = getattr(colaboracion, nombre, None)
    if valor is not None:
        return valor
    import importlib

    for submodulo in _SUBMODULOS:
        try:
            modulo = importlib.import_module(f"{colaboracion.__name__}.{submodulo}")
        except ImportError:
            continue
        except Exception:
            logger.exception("collaboration module %s failed to import", submodulo)
            continue
        valor = getattr(modulo, nombre, None)
        if valor is not None:
            return valor
    return None


def _despachador(especialistas: Mapping[str, Especialista], *, max_paralelo: int, emit: Callable[..., None]) -> Any:
    clase = de_la_colaboracion("Despachador")
    if clase is None:
        logger.warning("no Despachador in the collaboration runtime: running the plan sequentially")
        return _DespachadorLocal(especialistas, max_paralelo=max_paralelo, emit=emit)
    return clase(especialistas, max_paralelo=max_paralelo, emit=emit)


def puerta_de_cifras(
    respuesta: str, informes: Sequence[InformeEspecialista], *, ledger: EvidenceLedger, textos_usuario: Sequence[str]
) -> ResultadoPuerta | None:
    """The collaboration runtime's figures gate; None when it is not available (logged: the turn goes on)."""
    revisar = de_la_colaboracion("revisar_cifras")
    if revisar is None:
        logger.warning("figures gate unavailable: answer not checked for figures")
        return None
    try:
        resultado = revisar(respuesta, list(informes), ledger=ledger, textos_usuario=list(textos_usuario))
    except Exception:
        logger.exception("figures gate failed")
        return None
    return resultado if isinstance(resultado, ResultadoPuerta) else None


def _tabla(informe: InformeEspecialista) -> str:
    if not informe.cifras:
        return "(sin cifras)"
    filas = ["| id | concepto | valor | fragmento |", "|---|---|---|---|"]
    for c in informe.cifras:
        valor = c.literal or f"{c.valor}{' ' + c.unidad if c.unidad else ''}"
        filas.append(f"| {c.id} | {c.concepto} | {valor} | {c.fragment_id or '—'} |")
    return "\n".join(filas)


def material_local(informes: Sequence[InformeEspecialista], ledger: EvidenceLedger) -> str:
    """The reports as DATA blocks for the synthesis: text, citable fragments and the figures table."""
    bloques: list[str] = ["<informes_de_los_especialistas>"]
    for informe in informes:
        fragmentos = [ledger.get(i) for i in informe.fragment_ids]
        citables = "; ".join(f"[{f.fragment_id}] {f.title}" for f in fragmentos if f is not None) or "ninguno"
        texto = neutralizar_inyeccion(informe.texto) if informe.texto.strip() else "(sin informe)"
        avisos = f"\nAvisos: {' | '.join(informe.avisos)}" if informe.avisos else ""
        bloques.append(
            f'<informe especialista="{informe.especialista}" nombre="{informe.nombre}" encargo="{informe.encargo_id}" '
            f'estado="{informe.estado}"{f" modo={chr(34)}{informe.modo}{chr(34)}" if informe.modo else ""}>\n'
            f"{texto}\nFragmentos citables: {citables}\nCifras:\n{_tabla(informe)}{avisos}\n</informe>"
        )
    bloques.append("</informes_de_los_especialistas>")
    return "\n\n".join(bloques)


def material(informes: Sequence[InformeEspecialista], ledger: EvidenceLedger) -> str:
    """The runtime's synthesis material when it offers one, else :func:`material_local`."""
    construir = de_la_colaboracion("material_de_sintesis")
    if construir is not None:
        try:
            texto = construir(list(informes), ledger=ledger)
            if isinstance(texto, str) and texto.strip():
                return texto
        except TypeError:
            try:
                texto = construir(list(informes))
                if isinstance(texto, str) and texto.strip():
                    return texto
            except Exception:
                logger.exception("synthesis material failed: using the brain's own")
        except Exception:
            logger.exception("synthesis material failed: using the brain's own")
    return material_local(informes, ledger)


# --------------------------------------------------------------------------------------------------------------
# One turn
# --------------------------------------------------------------------------------------------------------------


@dataclass
class _Turno:
    """The brain's per-turn state."""

    consulta: str
    historial: list[dict[str, Any]]
    contexto: ContextoTurno
    especialistas: dict[str, Especialista]
    modulos: list[Modulo]
    comprension: Comprension
    inicio: float
    conversacion_id: str | None
    informes: list[InformeEspecialista] = field(default_factory=list)
    despachador: Any = None
    replanes: int = 0
    traza: list[ToolTraceEntry] = field(default_factory=list)
    uso: dict[str, int] = field(default_factory=dict)
    # Figures of the brain's own tools (the planner…): a report of the brain itself for the figures gate.
    cifras_propias: list[Cifra] = field(default_factory=list)
    # The final draft was cut by max_tokens: the answer is capped at partially verified and says so.
    truncada: bool = False

    def textos_usuario(self) -> list[str]:
        """The user's own words for the figures gate: this conversation's messages and, from the conversation memory,
        the literal quote each remembered item rests on (and the user's own correction of it). A figure the user gave
        in an earlier conversation is theirs, not an invention."""
        textos = list(self.contexto.mensajes_usuario)
        for recuerdo in self.contexto.memoria.recuerdos:
            textos.append(recuerdo.origen.cita)
            if recuerdo.corregido:
                textos.append(recuerdo.texto)
        return textos

    def para_puerta(self) -> list[InformeEspecialista]:
        """The reports whose figures the answer may use: the specialists' and, if any, the brain's own tools'."""
        if not self.cifras_propias:
            return list(self.informes)
        usados = [int(c.id[1:]) for i in self.informes for c in i.cifras if re.fullmatch(r"C\d+", c.id)]
        base = max(usados, default=0)
        cifras = [c.model_copy(update={"id": f"C{base + n}"}) for n, c in enumerate(self.cifras_propias[:60], start=1)]
        propio = InformeEspecialista(
            encargo_id=CLAVE_CEREBRO,
            especialista=CLAVE_CEREBRO,
            nombre="Asistente",
            estado="ok",
            cifras=cifras,
            fragment_ids=list(dict.fromkeys(c.fragment_id for c in cifras if c.fragment_id)),
        )
        return [*self.informes, propio]

    @property
    def intencion(self) -> Intencion:
        return self.comprension.intencion

    def siguiente_id(self) -> int:
        usados = [int(i.encargo_id[1:]) for i in self.informes if re.fullmatch(r"e\d+", i.encargo_id)]
        usados += [int(e.id[1:]) for e in self.intencion.encargos]
        return max(usados, default=0) + 1


class Cerebro:
    """The household brain. Build it with :func:`asistente.nucleo.cerebro.construir.construir_cerebro`."""

    ES_CEREBRO = True

    def __init__(
        self,
        *,
        asesor: TaxAgent,
        proveedor: LLMProvider,
        verificador: Verifier | None = None,
        memoria: ProveedorMemoria | None = None,
        fabrica: FabricaEspecialistas | None = None,
        proveedores: Mapping[str, LLMProvider] | None = None,
        max_pasos: int | None = None,
        max_revisiones: int | None = None,
        max_paralelo: int | None = None,
        max_replanes: int | None = None,
        umbral: float | None = None,
        modelo_intencion: bool | None = None,
        hoy: Callable[[], date] | None = None,
    ) -> None:
        self._asesor = asesor
        self._proveedor = proveedor
        self._proveedores = dict(proveedores or {})
        self._verificador = verificador or Verifier(self._para("verificador"))
        self._memoria: ProveedorMemoria = memoria or MemoriaNula()
        self._fabrica = fabrica
        self._max_pasos = max_pasos if max_pasos is not None else int(getattr(settings, "CEREBRO_MAX_PASOS", 6))
        self._max_revisiones = (
            max_revisiones if max_revisiones is not None else int(getattr(settings, "CEREBRO_MAX_REVISIONES", 2))
        )
        self._max_paralelo = (
            max_paralelo if max_paralelo is not None else int(getattr(settings, "CEREBRO_MAX_PARALELO", 4))
        )
        self._max_replanes = (
            max_replanes if max_replanes is not None else int(getattr(settings, "CEREBRO_MAX_REPLANES", 2))
        )
        self._umbral = umbral if umbral is not None else float(getattr(settings, "CEREBRO_UMBRAL_CONFIANZA", 0.45))
        self._modelo_intencion = (
            modelo_intencion
            if modelo_intencion is not None
            else bool(getattr(settings, "CEREBRO_INTENCION_MODELO", True))
        )
        self._hoy = hoy
        # Compatibility with callers that reach into the advisor (cli/eval_agent.py): the tax advisor's parts.
        self._provider = asesor._provider
        self._searcher = asesor._searcher
        self._verifier = asesor._verifier

    @property
    def asesor(self) -> TaxAgent:
        return self._asesor

    @property
    def memoria(self) -> ProveedorMemoria:
        """The conversation memory service (the API hands it each finished turn: ``registrar_turno``)."""
        return self._memoria

    def modelos_del_turno(self, resultado: AgentResult) -> dict[str, str]:
        """The model serving each purpose this turn used (for the audit trail and the evaluation), read from the
        brain's own providers (the same routing the turn just ran on; an escalation retry is not reflected).

        Purposes: ``cerebro:intencion`` when the small model wrote the plan, ``cerebro`` when the brain wrote with a
        model, ``agente`` + ``verificador`` when the tax specialist ran, ``especialista:<clave>`` per household
        report, and ``verificador`` when the final answer was verified.
        """
        informe = resultado.cerebro
        if informe is None:
            return {}
        propositos: list[str] = []
        if informe.intencion.origen in ("modelo", "mixto"):
            propositos.append("cerebro:intencion")
        if informe.ruta != "paso_directo" and resultado.steps > 0:
            propositos.append("cerebro")
        claves = list(dict.fromkeys(i.especialista for i in informe.informes))
        if CLAVE_FISCAL in claves:
            propositos.append(PROPOSITO_FISCAL)
        propositos += [f"especialista:{c}" for c in claves if c not in (CLAVE_FISCAL, CLAVE_CEREBRO)]
        modelos: dict[str, str] = {}
        for proposito in propositos:
            try:
                modelo = getattr(self._para(proposito), "model", None)
            except Exception:  # a broken routing setting: the audit simply lacks this line
                continue
            if isinstance(modelo, str) and modelo:
                modelos[proposito] = modelo
        verificadores = [self._asesor._verifier] if CLAVE_FISCAL in claves else []
        if informe.ruta != "paso_directo" and resultado.claims:
            verificadores.append(self._verificador)
        for verificador in verificadores:
            modelo = getattr(getattr(verificador, "_provider", None), "model", None)
            if isinstance(modelo, str) and modelo:
                modelos.setdefault("verificador", modelo)
        return modelos

    # -- providers -------------------------------------------------------------------------------------------

    def _para(self, proposito: str) -> LLMProvider:
        """The provider of one purpose: an explicit one (tests, a pinned model) or the metered base re-purposed."""
        if proposito in self._proveedores:
            return self._proveedores[proposito]
        if proposito == PROPOSITO_FISCAL:
            # The tax specialist runs on the advisor's own provider (its model, its metering purpose, its routing).
            return self._asesor._provider
        for patron, explicito in self._proveedores.items():
            if patron.endswith("*") and proposito.startswith(patron[:-1]):
                return explicito
        repropuesto: LLMProvider = enrutado.para(self._proveedor, proposito)
        return repropuesto

    # -- the turn --------------------------------------------------------------------------------------------

    def run(
        self,
        query: str,
        *,
        chat_history: list[dict[str, Any]] | None = None,
        case: CaseState | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        modulos: Sequence[Modulo] | None = None,
        conversacion_id: str | None = None,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Answer ``query`` for the household. Raises ``ProviderError`` (``PresupuestoAgotadoError`` included) only
        when nothing useful could be produced — exactly when the advisor alone would have raised.

        ``user_id``: the signed-in account (the API passes it); the conversation memory is read for it, the same owner
        the finished turn is stored under. Without it, the case's owner."""
        if kwargs:  # the advisor's team internals (ledger, briefing, critic): they are the advisor's business
            return self._asesor.run(
                query, chat_history=chat_history, case=case, on_event=on_event, modulos=modulos, **kwargs
            )

        def emit(event_type: str, **data: Any) -> None:
            if on_event is None:
                return
            try:
                on_event({"type": event_type, **data})
            except Exception:
                logger.exception("brain progress callback failed")

        inicio = time.monotonic()
        historial = [m for m in chat_history or [] if isinstance(m, dict)]
        memoria = self._recuperar(case, query, conversacion_id, user_id)
        contexto = ContextoTurno(
            expediente=ExpedienteCompartido(case),
            ledger=EvidenceLedger(),
            hoy=self._hoy() if self._hoy is not None else _hoy(),
            emit=emit,
            proveedor_para=self._para,
            mensajes_usuario=mensajes_del_usuario(query, historial),
            memoria=memoria,
            acciones=AccionesDelTurno(),
            chat_history=historial,
        )
        lista_modulos = list(modulos or [])
        especialistas, por_modulo = self._especialistas(query, historial, lista_modulos)
        fichas = [FichaEspecialista.de(e, por_modulo.get(c)) for c, e in especialistas.items()]
        comprension = Entendimiento(
            fichas,
            proveedor=self._para("cerebro:intencion") if self._modelo_intencion else None,
            umbral=self._umbral,
            usar_modelo=self._modelo_intencion,
        ).entender(query, chat_history=historial, case=case)
        turno = _Turno(
            consulta=query,
            historial=historial,
            contexto=contexto,
            especialistas=especialistas,
            modulos=lista_modulos,
            comprension=comprension,
            inicio=inicio,
            conversacion_id=conversacion_id,
        )
        intencion = comprension.intencion
        emit(EVENTO_PLAN, intencion=intencion.model_dump(mode="json"))

        if intencion.aclaracion:
            return self._directa(turno, intencion.aclaracion, AgentMode.CLARIFICATION)
        if intencion.tipo == "fuera_de_ambito":
            return self._directa(turno, self._fuera_de_ambito(turno), AgentMode.ABSTAINED)
        if es_paso_directo(intencion) and CLAVE_FISCAL in especialistas:
            return self._paso_directo(turno)
        if intencion.encargos:
            turno.despachador = _despachador(especialistas, max_paralelo=self._max_paralelo, emit=emit)
            turno.informes = list(turno.despachador.ejecutar(list(intencion.encargos), contexto))
            self._sin_presupuesto(turno.informes, getattr(turno.despachador, "presupuesto_agotado", None))
        return self._sintesis(turno)

    # -- pieces ------------------------------------------------------------------------------------------------

    def _recuperar(
        self, case: CaseState | None, consulta: str, conversacion_id: str | None, user_id: str | None = None
    ) -> BloqueMemoria:
        try:
            bloque = self._memoria.recuperar(
                case_id=case.case_id if case is not None else None,
                user_id=user_id or (case.user_id if case is not None else None),
                consulta=consulta,
                conversacion_id=conversacion_id,
                limite=int(getattr(settings, "CEREBRO_MEMORIA_LIMITE", 8)),
            )
        except Exception:  # the memory is optional: never fails the turn
            logger.exception("conversation memory unavailable this turn")
            return BloqueMemoria()
        return bloque if isinstance(bloque, BloqueMemoria) else BloqueMemoria()

    def _especialistas(
        self, consulta: str, historial: list[dict[str, Any]], modulos: list[Modulo]
    ) -> tuple[dict[str, Especialista], dict[str, Modulo]]:
        """This turn's specialists: the tax advisor always, plus one per active household module with a specialist."""
        from asistente.nucleo.modulos.ganchos import tiene_especialista

        por_modulo = {m.clave: m for m in modulos}
        hogar = [m for m in modulos if m.clave != CLAVE_FISCAL and tiene_especialista(m)]
        de_hogar: dict[str, Especialista] = {}
        if hogar:
            fabrica = self._fabrica
            if fabrica is None:
                from asistente.nucleo.cerebro.construir import especialistas_del_hogar

                fabrica = especialistas_del_hogar
            try:
                de_hogar = {c: e for c, e in fabrica(hogar).items() if c != CLAVE_FISCAL}
            except Exception:
                logger.exception("household specialists unavailable this turn")
        fiscal_modulo = por_modulo.get(CLAVE_FISCAL)
        fiscal = EspecialistaFiscal(
            self._asesor,
            consulta=consulta,
            chat_history=historial,
            modulos=modulos or None,
            otros={c: e.nombre for c, e in de_hogar.items()},
            nombre=str(getattr(fiscal_modulo, "nombre", NOMBRE_FISCAL) or NOMBRE_FISCAL),
        )
        return {CLAVE_FISCAL: fiscal, **de_hogar}, por_modulo

    @staticmethod
    def _sin_presupuesto(informes: Sequence[InformeEspecialista], error: Exception | None = None) -> None:
        """Nothing answered and the budget ran out: the user gets the budget message (429), as with the advisor."""
        if not informes or any(i.estado == "ok" for i in informes):
            return
        from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

        if isinstance(error, PresupuestoAgotadoError):
            raise error
        if all(i.estado == "presupuesto" for i in informes):
            aviso = next((a for i in informes for a in i.avisos if a), "Se ha alcanzado el límite de uso.")
            raise PresupuestoAgotadoError(aviso, limite="llamadas_por_pregunta")

    def _informe_cerebro(
        self,
        turno: _Turno,
        ruta: RutaRespuesta,
        *,
        puerta: ResultadoPuerta | None = None,
        revisiones: int = 0,
        recuerdos: Sequence[str] = (),
    ) -> InformeCerebro:
        peticiones: dict[str, PeticionDato] = {}
        for informe in turno.informes:
            for peticion in informe.peticiones:
                peticiones.setdefault(peticion.id, peticion)
        for peticion in getattr(turno.despachador, "peticiones", None) or []:
            if isinstance(peticion, PeticionDato):
                peticiones.setdefault(peticion.id, peticion)
        return InformeCerebro(
            intencion=turno.intencion,
            ruta=ruta,
            informes=[_resumen(i) for i in turno.informes],
            peticiones=list(peticiones.values()),
            puerta=puerta,
            recuerdos_usados=list(recuerdos),
            conversacion_id=turno.conversacion_id,
            revisiones_sintesis=revisiones,
            duracion_ms=int((time.monotonic() - turno.inicio) * 1000),
        )

    def _recuerdos_usados(self, turno: _Turno, respuesta: str) -> list[str]:
        """The remembered items behind the answer: those it names by id, or all of them on a memory question."""
        ids = turno.contexto.memoria.ids()
        nombrados = [i for i in ids if i and i in respuesta]
        if nombrados:
            return nombrados
        if turno.intencion.tipo == "memoria" or turno.intencion.necesita_memoria:
            return list(ids)
        return []

    def _directa(self, turno: _Turno, respuesta: str, modo: AgentMode) -> AgentResult:
        """An answer the brain gives without any model call (a clarification, a module that is switched off)."""
        return AgentResult(
            answer=respuesta,
            mode=modo,
            provider=self._proveedor.name,
            model=self._proveedor.model,
            cerebro=self._informe_cerebro(turno, "directa"),
        )

    def _fuera_de_ambito(self, turno: _Turno) -> str:
        nombres = [e.nombre for e in turno.especialistas.values()]
        resumen = turno.intencion.resumen
        if "no está activado" in resumen:
            return f"{resumen}. Puedes activarlo en «Módulos» y volver a preguntármelo."
        return (
            "Eso queda fuera de lo que llevo: soy el asistente de los papeles y el dinero de tu casa. "
            f"Puedo ayudarte con {', '.join(nombres[:-1]) + ' y ' + nombres[-1] if len(nombres) > 1 else nombres[0]}."
        )

    def _paso_directo(self, turno: _Turno) -> AgentResult:
        """The tax advisor's own verified answer IS the answer (mode, citations, claims unchanged)."""
        fiscal = turno.especialistas[CLAVE_FISCAL]
        assert isinstance(fiscal, EspecialistaFiscal)
        encargo = turno.intencion.encargos[0]
        emit = turno.contexto.emit
        emit(EVENTO_ESPECIALISTA, clave=CLAVE_FISCAL, nombre=fiscal.nombre, encargo_id=encargo.id, estado="start")
        contexto = ContextoEncargo(turno=turno.contexto, encargo=encargo, pizarra=_PizarraLocal())
        resultado, informe = fiscal.resolver(contexto, directo=True)
        emit(EVENTO_ESPECIALISTA, clave=CLAVE_FISCAL, nombre=fiscal.nombre, encargo_id=encargo.id, estado="done")
        assert resultado is not None  # directo: a failure propagated
        turno.informes = [informe]
        # Informational only (never changes the advisor's answer): which figure came from where, for the UI.
        puerta = puerta_de_cifras(
            resultado.answer,
            turno.informes,
            ledger=turno.contexto.ledger,
            textos_usuario=turno.textos_usuario(),
        )
        resultado.cerebro = self._informe_cerebro(
            turno, "paso_directo", puerta=puerta, recuerdos=self._recuerdos_usados(turno, resultado.answer)
        )
        return resultado

    # -- synthesis ---------------------------------------------------------------------------------------------

    def _consultar(self, turno: _Turno) -> ExtraTool | None:
        """``consultar_especialistas`` (replanning), bounded by ``CEREBRO_MAX_REPLANES`` per turn."""
        if not turno.especialistas or self._max_replanes <= 0:
            return None
        activos = list(turno.especialistas)
        spec = spec_consultar({c: (e.nombre, e.descripcion) for c, e in turno.especialistas.items()})

        def consultar(argumentos: dict[str, Any]) -> ToolOutcome:
            if turno.replanes >= self._max_replanes:
                return ToolOutcome(
                    content="Ya no puedes consultar más en esta pregunta: responde con los informes que tienes.",
                    summary="límite de consultas",
                    is_error=True,
                )
            encargos = encargos_de_llamada(argumentos, activos, primero=turno.siguiente_id())
            if isinstance(encargos, str):
                return ToolOutcome(content=encargos, summary=encargos[:120], is_error=True)
            turno.replanes += 1
            plan = Intencion(
                tipo="cruzada" if len({e.especialista for e in encargos}) > 1 else "ambito",
                encargos=encargos,
                confianza=1.0,
                origen="modelo",
                resumen="Consulta adicional durante la síntesis",
            )
            turno.contexto.emit(EVENTO_PLAN, intencion=plan.model_dump(mode="json"), replan=True)
            if turno.despachador is None:
                turno.despachador = _despachador(
                    turno.especialistas, max_paralelo=self._max_paralelo, emit=turno.contexto.emit
                )
            nuevos = list(turno.despachador.ejecutar(encargos, turno.contexto))
            turno.informes.extend(nuevos)
            return ToolOutcome(
                content=material(nuevos, turno.contexto.ledger),
                summary=f"{len(nuevos)} informe(s): {', '.join(i.especialista for i in nuevos)}",
                is_error=not any(i.estado == "ok" for i in nuevos),
            )

        return spec, consultar

    def _sistema(self, turno: _Turno, addendum: str) -> str:
        lista = "\n".join(
            f"- {e.nombre} (`{c}`): {' '.join(e.descripcion.split())[:300]}" for c, e in turno.especialistas.items()
        )
        sistema = prompt("cerebro", CEREBRO_SYSTEM_PROMPT).replace("{especialistas}", lista)
        tipo = turno.intencion.tipo
        if turno.informes or turno.intencion.encargos:
            sistema += prompt("cerebro_sintesis", SINTESIS_SYSTEM_PROMPT)
        elif tipo == "charla":
            sistema += ADDENDUM_CHARLA
        elif tipo == "memoria":
            sistema += ADDENDUM_MEMORIA
        elif tipo == "accion":
            sistema += ADDENDUM_ACCION
        return sistema + addendum

    def _mensaje(self, turno: _Turno, contexto_herramientas: str) -> str:
        case = turno.contexto.expediente.case
        lineas = [f"Fecha de hoy: {turno.contexto.hoy.isoformat()}"]
        if case is not None:
            lineas.append(f"Ejercicio del expediente: {case.tax_period}")
        partes = ["<contexto>\n" + "\n".join(lineas) + "\n</contexto>"]
        memoria = turno.contexto.memoria.como_contexto()
        if memoria:
            partes.append(memoria)
        if contexto_herramientas:
            partes.append(contexto_herramientas)
        if turno.informes:
            partes.append(material(turno.informes, turno.contexto.ledger))
        partes.append(turno.consulta)
        return "\n\n".join(partes)

    def _sintesis(self, turno: _Turno) -> AgentResult:
        contexto = turno.contexto
        emit = contexto.emit
        ruta: RutaRespuesta = "sintesis" if turno.informes else "directa"
        herramientas = herramientas_del_cerebro(
            proveedor=self._para("cerebro"),
            expediente=contexto.expediente,
            ledger=contexto.ledger,
            consulta=turno.consulta,
            chat_history=turno.historial,
            modulos=turno.modulos,
            consultar=self._consultar(turno),
            intencion=turno.intencion,
        )
        charla = turno.intencion.tipo == "charla" and not turno.informes
        specs = [] if charla else herramientas.caja.specs()
        if turno.informes:
            emit(EVENTO_SINTESIS, especialistas=[i.especialista for i in turno.informes])
        proveedor = self._para("cerebro")
        try:
            conversacion = proveedor.start(
                system=self._sistema(turno, "" if charla else herramientas.addendum),
                tools=specs,
                history=turno.historial,  # normalised by the provider
                user_message=self._mensaje(turno, herramientas.contexto),
            )
        except ProviderError as exc:
            return self._sin_sintesis(turno, exc, proveedor)

        pasos = 0
        revisiones = 0
        presupuesto = self._max_pasos
        puerta: ResultadoPuerta | None = None
        while True:
            permitir = bool(specs) and pasos < presupuesto - 1
            emit("thinking", step=pasos + 1)
            try:
                turno_modelo = conversacion.step(
                    allow_tools=permitir,
                    on_text=(lambda chunk: emit("text_delta", text=chunk)),
                )
            except ProviderError as exc:
                return self._sin_sintesis(turno, exc, proveedor, pasos=pasos, revisiones=revisiones)
            pasos += 1
            _sumar(turno.uso, turno_modelo.usage)
            if turno_modelo.stop_reason == "refusal":
                return self._final(
                    turno,
                    ruta,
                    REFUSAL,
                    AgentMode.ABSTAINED,
                    proveedor,
                    pasos,
                    revisiones,
                    puerta,
                    avisos=["Petición declinada por el modelo."],
                )
            if turno_modelo.tool_calls and turno_modelo.stop_reason == "max_tokens":
                return self._por_partes(turno, ruta, proveedor, pasos, revisiones, puerta, "La respuesta se truncó.")
            if turno_modelo.tool_calls:
                resultados: list[ToolResult] = []
                for llamada in turno_modelo.tool_calls:
                    # A replan is shown as its plan (EVENTO_PLAN); the brain's other tools as the advisor's always were.
                    visible = llamada.name != CONSULTAR
                    if permitir:
                        if visible:
                            emit("tool_call", tool=llamada.name, arguments=llamada.arguments)
                        salida = herramientas.caja.execute(llamada)
                    else:
                        salida = ToolOutcome(
                            content="Sin pasos disponibles; responde con lo que ya tienes.",
                            summary="sin pasos",
                            is_error=True,
                        )
                    if visible:
                        emit("tool_result", tool=llamada.name, summary=salida.summary, is_error=salida.is_error)
                    resultados.append(ToolResult(call_id=llamada.id, content=salida.content, is_error=salida.is_error))
                    if not salida.is_error and llamada.name != CONSULTAR:
                        turno.cifras_propias += cifras_de_herramienta(llamada.name, salida.content, contexto.ledger)
                    turno.traza.append(
                        ToolTraceEntry(
                            step=pasos,
                            tool=llamada.name,
                            arguments=llamada.arguments,
                            summary=salida.summary,
                            is_error=salida.is_error,
                            agent=CLAVE_CEREBRO,
                        )
                    )
                conversacion.add_tool_results(resultados)
                if turno.informes and ruta == "directa":
                    ruta = "sintesis"
                if pasos >= presupuesto:
                    return self._por_partes(
                        turno, ruta, proveedor, pasos, revisiones, puerta, "Se agotaron los pasos de la síntesis."
                    )
                if pasos == presupuesto - 1:
                    conversacion.add_user_message(FORZAR_RESPUESTA)
                continue

            respuesta = turno_modelo.text.strip()
            turno.truncada = bool(respuesta) and turno_modelo.stop_reason == "max_tokens"
            if not respuesta:
                return self._por_partes(
                    turno, ruta, proveedor, pasos, revisiones, puerta, "El modelo no devolvió texto."
                )

            # Gate 1: figures (deterministic, free): checked first, so a draft with an invented figure is rewritten
            # before a verifier call is spent on it.
            puerta = puerta_de_cifras(
                respuesta, turno.para_puerta(), ledger=contexto.ledger, textos_usuario=turno.textos_usuario()
            )
            violaciones = list(puerta.violaciones) if puerta is not None else []
            emit(EVENTO_PUERTA, violaciones=len(violaciones))
            citados = extract_cited_ids(respuesta)
            invalidos = [i for i in citados if contexto.ledger.get(i) is None]
            if (violaciones or invalidos) and revisiones < self._max_revisiones:
                revisiones += 1
                emit("revising", problems=len(violaciones) + len(invalidos))
                presupuesto = pasos + _PASOS_POR_REVISION
                conversacion.add_user_message(_revision(puerta, invalidos, [], []))
                continue
            # Gate 2: every claim citing a fragment, by the independent verifier.
            fragmentos = [f for f in (contexto.ledger.get(i) for i in citados) if f is not None]
            informe_verificador: VerificationReport | None = None
            if fragmentos:
                emit("verifying", citations=len(fragmentos))
                informe_verificador = self._verificador.verify(
                    question=turno.consulta, answer=respuesta, fragments=fragmentos
                )
            problemas: list[ClaimCheck] = []
            doctrina: list[str] = []
            if informe_verificador is not None and informe_verificador.error is None:
                if informe_verificador.response_type is ResponseType.ANSWER:
                    problemas = informe_verificador.problems
                    conflictos, avisos_doctrina = _doctrine_issues(fragmentos, informe_verificador)
                    doctrina = conflictos if avisos_doctrina else []
            if (problemas or doctrina) and revisiones < self._max_revisiones:
                revisiones += 1
                emit("revising", problems=len(problemas) + len(doctrina))
                presupuesto = pasos + _PASOS_POR_REVISION
                conversacion.add_user_message(_revision(puerta, invalidos, problemas, doctrina))
                continue
            return self._veredicto(
                turno, ruta, respuesta, proveedor, pasos, revisiones, puerta, informe_verificador, fragmentos, invalidos
            )

    def _veredicto(
        self,
        turno: _Turno,
        ruta: RutaRespuesta,
        respuesta: str,
        proveedor: LLMProvider,
        pasos: int,
        revisiones: int,
        puerta: ResultadoPuerta | None,
        informe: VerificationReport | None,
        fragmentos: list[Any],
        invalidos: list[str],
    ) -> AgentResult:
        avisos: list[str] = []
        violaciones = list(puerta.violaciones) if puerta is not None else []
        for fragment_id in invalidos:
            # A citation of evidence the turn does not hold is never shown as if it were a source.
            respuesta = re.sub(rf"\[\s*{re.escape(fragment_id)}\s*\]", "", respuesta)
        if violaciones:
            # Still failing after the revisions: never show a confident figure without an origin.
            respuesta = retirar_cifras(respuesta, violaciones)
            avisos.append(
                "He retirado de la respuesta "
                + ("una cifra" if len(violaciones) == 1 else f"{len(violaciones)} cifras")
                + " que no salía"
                + ("" if len(violaciones) == 1 else "n")
                + " de ningún cálculo de los especialistas ni de tus datos: "
                + "; ".join(f"«{v.literal}» ({v.detalle})" for v in violaciones[:5])
            )
        if informe is None:
            campos: dict[str, Any] = {"answer": respuesta, "mode": AgentMode.UNVERIFIED, "warnings": []}
            if invalidos:
                campos["warnings"].append(f"La respuesta cita fuentes inexistentes: {', '.join(invalidos)}.")
            if turno.informes and turno.intencion.tipo not in ("charla", "memoria"):
                campos["warnings"].append(
                    "La respuesta no cita los cálculos o las fuentes de los que sale; trátala como orientativa."
                )
        else:
            campos = TaxAgent._verdict(respuesta, informe, fragmentos, invalidos)
            if campos["mode"] is AgentMode.ABSTAINED and campos.get("raw_answer") and turno.informes:
                # Nothing of the synthesis survived the verifier: the specialists' own reports are still sound.
                return self._por_partes(
                    turno,
                    ruta,
                    proveedor,
                    pasos,
                    revisiones,
                    puerta,
                    "Ninguna afirmación de la respuesta conjunta se pudo comprobar en sus fuentes.",
                )
        modo: AgentMode = campos.pop("mode")
        texto: str = campos.pop("answer")
        todos_los_avisos = [*avisos, *campos.pop("warnings", [])]
        if violaciones:
            modo = _peor(modo, AgentMode.PARTIALLY_VERIFIED)
        if puerta is None and turno.informes and modo not in (AgentMode.CLARIFICATION, AgentMode.ABSTAINED):
            # The figures gate did not run (unavailable or crashed): it never fails open.
            modo = _peor(modo, AgentMode.PARTIALLY_VERIFIED)
            todos_los_avisos.append(AVISO_PUERTA_CAIDA)
        if turno.truncada and modo not in (AgentMode.CLARIFICATION, AgentMode.ABSTAINED):
            modo = _peor(modo, AgentMode.PARTIALLY_VERIFIED)
            todos_los_avisos.append(TRUNCATED_WARNING)
        return self._final(
            turno, ruta, texto, modo, proveedor, pasos, revisiones, puerta, avisos=todos_los_avisos, **campos
        )

    def _avisos_de_informes(self, turno: _Turno) -> list[str]:
        avisos: list[str] = []
        motivos = {
            "sin_respuesta": "no ha encontrado base para responder",
            "error": "no ha podido responder",
            "denegado": "no tenía permiso para esta consulta",
            "presupuesto": "no ha podido responder: se alcanzó el límite de uso",
        }
        for informe in turno.informes:
            if informe.estado != "ok":
                avisos.append(f"{informe.nombre}: {motivos.get(informe.estado, informe.estado)}; esa parte falta.")
            for aviso in informe.avisos:
                if informe.estado == "ok" and aviso:
                    avisos.append(f"{informe.nombre}: {aviso}")
        return list(dict.fromkeys(avisos))

    def _final(
        self,
        turno: _Turno,
        ruta: RutaRespuesta,
        respuesta: str,
        modo: AgentMode,
        proveedor: LLMProvider,
        pasos: int,
        revisiones: int,
        puerta: ResultadoPuerta | None,
        *,
        avisos: Sequence[str] = (),
        **campos: Any,
    ) -> AgentResult:
        tope = tope_de_informes(turno.informes)
        if tope is not None and modo not in (AgentMode.CLARIFICATION, AgentMode.ABSTAINED):
            modo = _peor(modo, tope)
        uso = dict(turno.uso)
        for informe in turno.informes:
            _sumar(uso, informe.uso)
        acciones = turno.contexto.acciones.todas()
        expediente = turno.contexto.expediente
        equipo = None
        fiscal = turno.especialistas.get(CLAVE_FISCAL)
        if isinstance(fiscal, EspecialistaFiscal):
            for informe in turno.informes:
                resultado = fiscal.resultado(informe.encargo_id) if informe.especialista == CLAVE_FISCAL else None
                if resultado is not None and resultado.team is not None:
                    equipo = resultado.team
        return AgentResult(
            answer=respuesta,
            mode=modo,
            warnings=list(dict.fromkeys([*avisos, *self._avisos_de_informes(turno)])),
            team=equipo,
            tool_trace=[*turno.traza, *_traza(turno.informes)],
            steps=pasos,
            revisions=revisiones,
            provider=proveedor.name,
            model=proveedor.model,
            usage=uso,
            updated_case=expediente.case if expediente.cambiado else None,
            acciones_propuestas=list(acciones),
            cerebro=self._informe_cerebro(
                turno, ruta, puerta=puerta, revisiones=revisiones, recuerdos=self._recuerdos_usados(turno, respuesta)
            ),
            **campos,
        )

    def _por_partes(
        self,
        turno: _Turno,
        ruta: RutaRespuesta,
        proveedor: LLMProvider,
        pasos: int,
        revisiones: int,
        puerta: ResultadoPuerta | None,
        motivo: str,
    ) -> AgentResult:
        """The honest fallback: each specialist's own report as it is, its figures from its own tools."""
        utiles = [i for i in turno.informes if i.estado == "ok" and i.texto.strip()]
        if not utiles:
            return self._final(
                turno,
                ruta,
                "No he podido preparar una respuesta fiable a esta consulta. Inténtalo de nuevo en unos minutos o "
                "pregúntamelo por partes.",
                AgentMode.ABSTAINED,
                proveedor,
                pasos,
                revisiones,
                puerta,
                avisos=[motivo],
            )
        partes = [
            "No he podido redactar una respuesta conjunta que pase mis controles, así que te dejo lo que ha dicho "
            "cada especialista, tal cual:"
        ]
        partes += [f"**{i.nombre}**\n{i.texto.strip()}" for i in utiles]
        respuesta = "\n\n".join(partes)
        citados = extract_cited_ids(respuesta)
        fragmentos = [f for f in (turno.contexto.ledger.get(i) for i in citados) if f is not None]
        citas = TaxAgent._verdict(respuesta, VerificationReport(), fragmentos, [])["citations"] if fragmentos else []
        claims: list[ClaimCheck] = []
        fiscal = turno.especialistas.get(CLAVE_FISCAL)
        if isinstance(fiscal, EspecialistaFiscal):
            for informe in utiles:
                resultado = fiscal.resultado(informe.encargo_id) if informe.especialista == CLAVE_FISCAL else None
                if resultado is not None:
                    claims += resultado.claims
        return self._final(
            turno,
            ruta,
            respuesta,
            AgentMode.PARTIALLY_VERIFIED,
            proveedor,
            pasos,
            revisiones,
            puerta,
            avisos=[f"{motivo} Te muestro los informes de cada especialista por separado."],
            citations=citas,
            claims=claims,
        )

    def _sin_sintesis(
        self, turno: _Turno, exc: ProviderError, proveedor: LLMProvider, *, pasos: int = 0, revisiones: int = 0
    ) -> AgentResult:
        """The synthesis model failed: with reports in hand they are shown as they are; without any, it raises
        (the API answers 503 / 429 exactly as it did for the advisor)."""
        if not any(i.estado == "ok" and i.texto.strip() for i in turno.informes):
            raise exc
        from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

        motivo = (
            "Se alcanzó el límite de uso antes de redactar la respuesta conjunta."
            if isinstance(exc, PresupuestoAgotadoError)
            else "El modelo no respondió al redactar la respuesta conjunta."
        )
        return self._por_partes(turno, "sintesis", proveedor, pasos, revisiones, None, motivo)


def _revision(
    puerta: ResultadoPuerta | None,
    invalidos: Sequence[str],
    problemas: Sequence[ClaimCheck],
    doctrina: Sequence[str],
) -> str:
    """The precise revision message: the figures gate's own (what to fix, per kind) plus the verifier's claims."""
    partes: list[str] = []
    if puerta is not None and puerta.violaciones:
        mensaje = de_la_colaboracion("mensaje_de_revision")
        texto = ""
        if callable(mensaje):
            try:
                texto = str(mensaje(puerta) or "")
            except Exception:
                logger.exception("figures gate revision message failed")
        partes.append(texto or "\n".join(f"- Cifra «{v.literal}»: {v.detalle}" for v in puerta.violaciones))
    lineas: list[str] = []
    if invalidos and not (puerta is not None and any(v.tipo == "cita_inexistente" for v in puerta.violaciones)):
        lineas.append(f"- Identificadores citados que no existen: {', '.join(invalidos)}.")
    for issue in doctrina:
        lineas.append(f"- Vigencia: {issue} Dilo o apóyate en la resolución posterior.")
    for claim in problemas:
        citado = ", ".join(claim.fragment_ids) or "sin cita"
        lineas.append(f"- «{claim.text}» ({citado}) → {claim.verdict.value}: {claim.reason or 'sin respaldo'}")
    if lineas:
        partes.append("Un verificador independiente ha comparado tu respuesta con sus fuentes:\n" + "\n".join(lineas))
    return REVISION_SINTESIS.format(problemas="\n\n".join(partes))


def cifras_de_herramienta(nombre: str, contenido: str, ledger: EvidenceLedger) -> list[Cifra]:
    """The figures one of the brain's OWN tools produced (the planner…), so the gate accepts them as tool figures.

    Read with the runtime's extractor from the ledger fragment the tool registered (its ``[F#]`` leads the
    content), or from the content itself. Ids are provisional; :meth:`_Turno.para_puerta` renumbers them.
    """
    try:
        from asistente.nucleo.cerebro.colaboracion.cifras import extraer_cifras
    except Exception:
        return []
    m = re.match(r"\s*\[(F\d+)\]", contenido or "")
    fragmento = ledger.get(m.group(1)) if m else None
    texto = fragmento.text if fragmento is not None else (contenido or "")
    try:
        return list(
            extraer_cifras(
                texto,
                especialista=CLAVE_CEREBRO,
                fragment_id=fragmento.fragment_id if fragmento is not None else None,
                herramienta=nombre[:80],
            )
        )
    except Exception:
        logger.exception("figures of the brain tool %s unreadable", nombre)
        return []


def retirar_cifras(respuesta: str, violaciones: Sequence[Violacion]) -> str:
    """``respuesta`` with every offending figure replaced by :data:`MARCA_RETIRADA` (citations excluded)."""
    texto = respuesta
    for violacion in violaciones:
        if violacion.tipo == "cita_inexistente" or not violacion.literal.strip():
            continue
        # Only standalone occurrences: retiring «43» must not touch «143,00 €» or a «Norma 43» elsewhere.
        literal = re.escape(violacion.literal.strip())
        texto = re.sub(rf"(?<![\w.,]){literal}(?!\w|[.,]\d)", MARCA_RETIRADA, texto)
    return texto


def _hoy() -> date:
    from asistente.nucleo.proactive.jobs import hoy_en_espana

    return hoy_en_espana()


__all__ = [
    "MARCA_RETIRADA",
    "Cerebro",
    "FabricaEspecialistas",
    "material",
    "material_local",
    "cifras_de_herramienta",
    "de_la_colaboracion",
    "puerta_de_cifras",
    "retirar_cifras",
    "tope_de_informes",
]

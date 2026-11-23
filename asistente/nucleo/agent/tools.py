"""Tools the agent can call, and their execution against local state.

Tools are deliberately few and typed. Everything that must be *exact* — what
the law says, what the user declared, which rules fire — lives behind a tool,
so the model orchestrates and reasons but never has to recall a figure.

This file holds only the CORE tools: the corpus search, the doctrine validity graph, the case (read it,
record facts with their provenance, evaluate the rules) and the household planner. Domain tools come from the
modules through the registry (``modulos/contrato.py``): a module flagged ``herramientas_directas`` (the tax
module) has its read-only tools offered right after the corpus search and run with the run's evidence ledger;
the others are reached through their ``consultar_<clave>`` specialist (``agent/especialistas.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from asistente.conocimiento.doctrine.citas import extract_citations, identify_document
from asistente.conocimiento.doctrine.grafo import DoctrineGraph
from asistente.nucleo.agent.evidence import SOURCE_TYPES, EvidenceLedger, Fragment
from asistente.nucleo.agent.procedencia import AVISO_SIN_RESPALDO, sin_respaldo
from asistente.nucleo.agent.providers.base import ToolCall, ToolSpec
from asistente.nucleo.agent.search import CorpusSearcher, SearchFilters, normalize_date
from asistente.nucleo.decision.fact_extractor import EXPECTED_FACT_TYPE, KNOWN_FACT_NAMES, _coerce_value
from asistente.nucleo.decision.interpreter import _merge_facts
from asistente.nucleo.decision.rules_engine import evaluate_rules
from asistente.nucleo.decision.schemas import CaseState, Fact
from asistente.nucleo.modulos.contrato import ContextoModulo, ResultadoHerramienta
from asistente.nucleo.planificador.agente import PLANIFICAR_TOOL, planificar_para_agente
from asistente.nucleo.proactive.jobs import hoy_en_espana

if TYPE_CHECKING:
    from asistente.nucleo.modulos.contrato import HerramientaModulo, Modulo

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 10

SEARCH_TOOL = ToolSpec(
    name="buscar_fuentes",
    description=(
        "Busca en el corpus fiscal español (legislación consolidada del BOE, consultas vinculantes de la DGT, "
        "resoluciones del TEAC, jurisprudencia y contenido de la AEAT) y devuelve fragmentos literales, cada uno con "
        "un identificador citable [F#], su autoridad, fecha y si es vinculante. Úsala antes de afirmar cualquier "
        "norma, cifra, plazo o criterio. La búsqueda es semántica: escribe la consulta como una frase descriptiva "
        "con los términos técnicos de la norma (p. ej. 'exención por reinversión en vivienda habitual artículo 38 "
        "LIRPF requisitos y plazo'), no como palabras sueltas. Usa `tipo_fuente` para pedir expresamente la ley, la "
        "jurisprudencia o la doctrina administrativa sobre un punto ya localizado."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "consulta": {"type": "string", "description": "Qué buscar, en español, con terminología técnica."},
            "tipo_fuente": {
                "type": "string",
                "enum": list(SOURCE_TYPES),
                "description": (
                    "Restringe a un tipo: 'normativa' (leyes y reglamentos, BOE y boletines autonómicos), "
                    "'jurisprudencia' (TS, TC), 'doctrina_administrativa' (DGT, TEAC) o 'aeat' (manuales y páginas "
                    "informativas). Omítelo para buscar en todo."
                ),
            },
            "tributo": {
                "type": "string",
                "description": "Tributo al que acotar si procede: IRPF, IVA, ISD, ITP_AJD, IS, IP, IIVTNU…",
            },
            "ambito": {
                "type": "string",
                "description": (
                    "'estatal', o 'autonomico:<comunidad>' para normativa propia de una comunidad autónoma "
                    "(la estatal se incluye siempre)."
                ),
            },
            "solo_vinculante": {
                "type": "boolean",
                "description": "true para devolver solo doctrina marcada como vinculante.",
            },
            "desde_fecha": {
                "type": "string",
                "description": (
                    "AAAA o AAAA-MM-DD. Descarta consultas, resoluciones y sentencias anteriores a esa fecha; úsalo "
                    "cuando una reforma o una sentencia posterior haya podido dejar superada la doctrina antigua. "
                    "La legislación consolidada (sin fecha de resolución) se mantiene siempre."
                ),
            },
            "max_resultados": {"type": "integer", "description": f"Entre 1 y {MAX_SEARCH_RESULTS}. Por defecto 6."},
        },
        "required": ["consulta"],
    },
)

READ_CASE_TOOL = ToolSpec(
    name="consultar_expediente",
    description=(
        "Devuelve el expediente fiscal del usuario: ejercicio, hechos ya conocidos (residencia, tipo de renta, "
        "importes, actividad…), datos que faltan y obligaciones candidatas detectadas. Consúltalo antes de "
        "preguntar al usuario algo que quizá ya haya dicho."
    ),
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
)

RECORD_FACTS_TOOL = ToolSpec(
    name="registrar_hechos",
    # The core's own sentences. The toolbox appends the modules' ``ayuda_registrar_hechos`` (the shapes of the
    # facts they know): see :func:`registrar_hechos_spec`.
    description=(
        "Guarda en el expediente hechos que el usuario ha declarado expresamente en esta conversación (o que ha "
        "corregido). No registres inferencias ni supuestos tuyos. Se comprueba contra los mensajes del usuario: "
        "cada cifra tiene que ser una que él haya escrito, y un sí/no o un texto (y los JSON de abajo) necesitan "
        "`cita_del_usuario` con sus palabras literales que lo dicen; lo que salga de una fuente, un documento o un "
        "módulo se rechaza (pregúntaselo)."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hechos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "nombre": {"type": "string", "enum": list(KNOWN_FACT_NAMES)},
                        "valor": {"type": ["string", "number", "boolean"]},
                        "cita_del_usuario": {"type": "string"},
                    },
                    "required": ["nombre", "valor"],
                },
            }
        },
        "required": ["hechos"],
    },
)

EVALUATE_RULES_TOOL = ToolSpec(
    name="evaluar_obligaciones",
    description=(
        "Ejecuta el catálogo determinista de reglas sobre el expediente y devuelve las obligaciones tributarias "
        "candidatas, los hechos que faltan para confirmarlas y las fuentes normativas de cada regla. El catálogo es "
        "parcial: que una obligación no aparezca no significa que no exista."
    ),
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
)


VALIDITY_TOOL = ToolSpec(
    name="consultar_vigencia",
    description=(
        "Consulta el grafo de doctrina para saber si una consulta de la DGT, una resolución del TEAC o una "
        "sentencia sigue vigente: qué resoluciones posteriores la confirman, matizan, superan o contradicen. "
        "Úsala antes de apoyarte en doctrina de hace años o cuando dos fuentes parezcan discrepar. El resultado es "
        "un indicio construido a partir de las citas entre resoluciones, no una fuente: para afirmar que algo está "
        "superado, busca y cita la resolución posterior con buscar_fuentes."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "referencia": {
                "type": "string",
                "description": (
                    "La resolución tal como se cita: 'V1234-19', 'resolución TEAC 00/1234/2017', "
                    "'STS de 3 de junio de 2020 (rec. 1234/2019)', 'STC 182/2021'."
                ),
            }
        },
        "required": ["referencia"],
    },
)

_RELACION_VERBO = {
    "cita": "la cita",
    "confirma": "la confirma",
    "matiza": "la matiza",
    "supera": "la supera",
    "contradice": "la contradice",
}


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    summary: str
    is_error: bool = False


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(content=message, summary=message[:120], is_error=True)


def registrar_hechos_spec(ayudas: Iterable[str]) -> ToolSpec:
    """``registrar_hechos`` with the modules' help on the facts they know appended to the core's sentences."""
    partes = [RECORD_FACTS_TOOL.description, *(texto.strip() for texto in ayudas if texto and texto.strip())]
    return ToolSpec(
        name=RECORD_FACTS_TOOL.name, description=" ".join(partes), input_schema=RECORD_FACTS_TOOL.input_schema
    )


def modulos_del_nucleo() -> list[Modulo]:
    """The modules the operator has on, in registry order: where direct tools and fact helpers come from."""
    from asistente.nucleo.modulos.registro import modulos_activos

    return modulos_activos()


_CASE_ONLY_TOOLS = frozenset({READ_CASE_TOOL.name, RECORD_FACTS_TOOL.name, EVALUATE_RULES_TOOL.name})


# A tool the core adds to a run (the household modules' specialists): its spec and its handler.
ExtraTool = tuple[ToolSpec, Callable[[dict[str, Any]], ToolOutcome]]


class Toolbox:
    """Holds the per-run state the tools read and mutate."""

    def __init__(
        self,
        *,
        searcher: CorpusSearcher,
        ledger: EvidenceLedger,
        case: CaseState | None = None,
        default_k: int = 6,
        graph: DoctrineGraph | None = None,
        allowed: frozenset[str] | None = None,
        extra: Sequence[ExtraTool] = (),
        mensajes_usuario: Sequence[str] | None = None,
        modulos: Sequence[Modulo] | None = None,
    ) -> None:
        # Subagents get a subset of the tools (a researcher must not edit the case).
        self._allowed = allowed
        # The user's own messages of this conversation: what registrar_hechos may record (agent/procedencia.py).
        # None (a toolbox outside a conversation) records nothing.
        self.mensajes_usuario = (
            [m for m in mensajes_usuario if isinstance(m, str)] if mensajes_usuario is not None else None
        )
        # Tools added by the core for this run (``consultar_<modulo>``…), after the direct and case ones.
        self._extra = tuple(extra)
        self._graph = graph
        self._searcher = searcher
        self.ledger = ledger
        self.case = case
        self.case_changed = False
        self._default_k = default_k
        # The modules this toolbox draws on (default: every module the operator has on, from the registry).
        fuentes = tuple(modulos) if modulos is not None else tuple(modulos_del_nucleo())
        # Read-only tools a module declares direct (``GanchosAgente.directas``), offered right after the corpus search
        # (a tool that prepares or acts never runs without the specialist's confirmation flow: not offered here).
        self._directas: dict[str, tuple[Modulo, HerramientaModulo]] = {}
        # Shared facts whose shape a module knows: name -> validate and return the canonical text to store.
        self._estructurados: dict[str, Callable[[Any], str]] = {}
        from asistente.nucleo.modulos.ganchos import ganchos_de

        agentes = [(modulo, ganchos_de(modulo).agente) for modulo in fuentes]
        for modulo, agente in agentes:
            directas = set(agente.directas)
            for herramienta in modulo.herramientas:
                if herramienta.spec.name in directas and herramienta.efecto == "lectura":
                    self._directas.setdefault(herramienta.spec.name, (modulo, herramienta))
            estructurados: Mapping[str, Callable[[Any], str]] = agente.hechos_estructurados or {}
            for nombre, normalizar in estructurados.items():
                self._estructurados.setdefault(nombre, normalizar)
        self._record_spec = registrar_hechos_spec(agente.ayuda_registrar_hechos for _modulo, agente in agentes)

    def specs(self) -> list[ToolSpec]:
        specs = [SEARCH_TOOL, *(herramienta.spec for _modulo, herramienta in self._directas.values())]
        if self._graph is not None:
            specs.append(VALIDITY_TOOL)
        if self.case is not None:
            specs.extend([READ_CASE_TOOL, self._record_spec, EVALUATE_RULES_TOOL])
            # The household planner crosses modules: offered with a case when household modules are on.
            if any(spec.name.startswith("consultar_") for spec, _handler in self._extra):
                specs.append(PLANIFICAR_TOOL)
        specs.extend(spec for spec, _handler in self._extra)
        if self._allowed is not None:
            specs = [spec for spec in specs if spec.name in self._allowed]
        return specs

    def execute(self, call: ToolCall) -> ToolOutcome:
        if call.arguments_error:
            return _error(f"No se pudo interpretar la llamada: {call.arguments_error}. Reenvíala con JSON válido.")
        handlers: dict[str, Callable[[dict[str, Any]], ToolOutcome]] = {
            SEARCH_TOOL.name: self._search,
            PLANIFICAR_TOOL.name: lambda a: ToolOutcome(*planificar_para_agente(a, case=self.case, ledger=self.ledger)),
            VALIDITY_TOOL.name: self._validity,
            READ_CASE_TOOL.name: self._read_case,
            RECORD_FACTS_TOOL.name: self._record_facts,
            EVALUATE_RULES_TOOL.name: self._evaluate_rules,
        }
        for nombre, (modulo, herramienta) in self._directas.items():
            handlers.setdefault(nombre, self._directa(modulo, herramienta))
        handlers.update({spec.name: handler for spec, handler in self._extra if spec.name not in handlers})
        handler = handlers.get(call.name)
        unavailable = (
            (call.name in _CASE_ONLY_TOOLS and self.case is None)
            or (self._allowed is not None and call.name not in self._allowed)
        ) or (call.name == VALIDITY_TOOL.name and self._graph is None)
        if handler is None or unavailable:
            return _error(f"Herramienta no disponible: {call.name}.")
        try:
            return handler(call.arguments)
        except Exception as exc:  # a tool failure is reported to the model, never crashes the turn
            logger.exception("tool %s failed", call.name)
            return _error(f"La herramienta {call.name} falló: {exc}")

    # ------------------------------------------------------------------ #

    def _directa(self, modulo: Modulo, herramienta: HerramientaModulo) -> Callable[[dict[str, Any]], ToolOutcome]:
        """A direct module tool: it registers its own fragments in the run's ledger; its text goes as it is."""

        def ejecutar(arguments: dict[str, Any]) -> ToolOutcome:
            from asistente.nucleo.modulos.perfil import perfil_de

            contexto = ContextoModulo(
                perfil=perfil_de(self.case),
                hoy=hoy_en_espana(),
                case_id=self.case.case_id if self.case is not None else None,
                evidencia=self.ledger,
                caso=self.case,
            )
            resultado = herramienta.ejecutar(arguments, contexto)
            if not isinstance(resultado, ResultadoHerramienta):
                return _error(f"La herramienta {herramienta.spec.name} no devolvió un resultado válido.")
            contenido = resultado.contenido
            if resultado.citas:
                contenido += "\nFuentes: " + "; ".join(resultado.citas)
            return ToolOutcome(content=contenido, summary=resultado.resumen, is_error=resultado.es_error)

        return ejecutar

    def _search(self, arguments: dict[str, Any]) -> ToolOutcome:
        query = arguments.get("consulta")
        if not isinstance(query, str) or not query.strip():
            return _error("Falta 'consulta' (texto no vacío).")
        source_type = arguments.get("tipo_fuente")
        if source_type is not None and source_type not in SOURCE_TYPES:
            return _error(f"'tipo_fuente' debe ser uno de: {', '.join(SOURCE_TYPES)}.")
        raw_k = arguments.get("max_resultados", self._default_k)
        k = raw_k if isinstance(raw_k, int) and not isinstance(raw_k, bool) else self._default_k
        k = max(1, min(MAX_SEARCH_RESULTS, k))

        def _text(key: str) -> str | None:
            value = arguments.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None

        date_from = _text("desde_fecha")
        if date_from is not None and normalize_date(date_from) is None:
            return _error("'desde_fecha' debe tener formato AAAA o AAAA-MM-DD.")
        filters = SearchFilters(
            date_from=date_from,
            source_type=source_type,
            tributo=_text("tributo"),
            jurisdiction_scope=_text("ambito"),
            binding_only=arguments.get("solo_vinculante") is True,
        )
        docs = self._searcher.search(query.strip(), k=k, filters=filters)
        if not docs:
            hint = " Prueba sin filtros o con otra formulación." if not filters.is_empty() else ""
            return ToolOutcome(
                content=f"Sin resultados para esta búsqueda.{hint}",
                summary="0 fragmentos",
            )
        fragments = [
            self.ledger.register(text=doc.texto or "", metadata=self._with_validity(dict(doc.metadatos or {})))
            for doc in docs
        ]
        # A repeated search can return chunks already registered; show each once.
        unique = list({fragment.fragment_id: fragment for fragment in fragments}.values())
        body = "\n\n".join(fragment.render() for fragment in unique)
        alerts = self._validity_alerts(unique)
        if alerts:
            body += "\n\n" + alerts
        return ToolOutcome(
            content=body,
            summary=f"{len(unique)} fragmentos: {', '.join(f.fragment_id for f in unique)}",
        )

    # ------------------------------------------------------------------ #
    # Doctrine validity
    # ------------------------------------------------------------------ #

    def _with_validity(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Stamp a retrieved chunk with its document's validity status."""
        if self._graph is None:
            return metadata
        doc = identify_document(metadata)
        if doc is None:
            return metadata
        status = self._graph.vigencia(doc.clave)
        if status.estado != "desconocido":
            metadata["_doc_key"] = status.clave
            metadata["_vigencia_estado"] = status.estado
            metadata["_vigencia_resumen"] = status.resumen
        return metadata

    def _validity_alerts(self, fragments: list[Fragment]) -> str:
        """Warn the model about superseded/disputed doctrine among the results,
        and about results that disagree with each other."""
        if self._graph is None:
            return ""
        lines: list[str] = []
        for fragment in fragments:
            if fragment.vigencia in ("superada", "discutida", "matizada"):
                lines.append(f"- [{fragment.fragment_id}] {fragment.title}: {fragment.vigencia_resumen}")
        by_key = {fragment.doc_key: fragment for fragment in fragments if fragment.doc_key}
        for key, fragment in by_key.items():
            for arista in self._graph.salientes(key):
                other = by_key.get(arista.destino)
                if other is not None and arista.relacion in ("supera", "contradice", "matiza"):
                    lines.append(
                        f"- [{fragment.fragment_id}] {_RELACION_VERBO[arista.relacion].replace('la ', '')} a "
                        f"[{other.fragment_id}] (según el grafo de citas)."
                    )
        if not lines:
            return ""
        return (
            "ALERTAS DE VIGENCIA (indicios del grafo de citas entre resoluciones; compruébalos leyendo la resolución "
            "posterior antes de afirmarlos):\n" + "\n".join(dict.fromkeys(lines))
        )

    def _validity(self, arguments: dict[str, Any]) -> ToolOutcome:
        assert self._graph is not None
        reference = arguments.get("referencia")
        if not isinstance(reference, str) or not reference.strip():
            return _error("Falta 'referencia' (la resolución tal como se cita).")
        citas = extract_citations(reference)
        if not citas:
            return _error(
                "No reconozco esa referencia. Usa el número de consulta (V1234-19), el RG del TEAC (00/1234/2017), "
                "o 'STS … (rec. 1234/2019)' / 'STC 182/2021'."
            )
        blocks: list[str] = []
        for cita in citas[:3]:
            nodo = self._graph.get(cita.clave)
            if nodo is None:
                blocks.append(
                    f"{cita.texto}: no consta en el grafo (ni está en el corpus ni la cita ningún documento)."
                )
                continue
            status = self._graph.vigencia(nodo.clave)
            lines = [
                f"{self._graph.etiqueta(nodo)} — estado: {status.estado.upper()}. {status.resumen}"
                + (
                    ""
                    if nodo.en_corpus
                    else " (No está en el corpus: solo se conoce por las citas de otros documentos.)"
                )
            ]
            for arista in self._graph.entrantes(nodo.clave):
                otro = self._graph.get(arista.origen)
                if otro is not None:
                    evidencia = f" — «{arista.evidencia}»" if arista.evidencia and arista.relacion != "cita" else ""
                    lines.append(f"  · {self._graph.etiqueta(otro)} {_RELACION_VERBO[arista.relacion]}{evidencia}")
            for arista in self._graph.salientes(nodo.clave):
                otro = self._graph.get(arista.destino)
                if otro is not None and arista.relacion != "cita":
                    lines.append(f"  · {arista.relacion} a {self._graph.etiqueta(otro)}")
            blocks.append("\n".join(lines))
        blocks.append(
            "Esto es un indicio, no una fuente citable: para afirmar ante el usuario que un criterio está superado o "
            "discutido, localiza la resolución posterior con buscar_fuentes y cítala."
        )
        return ToolOutcome(content="\n\n".join(blocks), summary=f"vigencia de {len(citas[:3])} referencia(s)")

    def _case_snapshot(self) -> dict[str, Any]:
        case = self.case
        assert case is not None
        return {
            "ejercicio": case.tax_period,
            "jurisdiccion": case.jurisdiction,
            "hechos": {fact.name: fact.value for fact in case.facts},
            "datos_pendientes": [missing.fact_name for missing in case.missing_facts],
            "obligaciones_candidatas": [
                {
                    "id": obligation.obligation_id,
                    "titulo": obligation.title,
                    "estado": obligation.status.value,
                    "faltan_para_confirmar": obligation.blocking_missing_facts,
                }
                for obligation in case.obligation_candidates
            ],
        }

    def _read_case(self, arguments: dict[str, Any]) -> ToolOutcome:
        del arguments
        snapshot = self._case_snapshot()
        return ToolOutcome(
            content=json.dumps(snapshot, ensure_ascii=False, default=str),
            summary=f"{len(snapshot['hechos'])} hechos conocidos",
        )

    def _record_facts(self, arguments: dict[str, Any]) -> ToolOutcome:
        assert self.case is not None
        raw_facts = arguments.get("hechos")
        if not isinstance(raw_facts, list) or not raw_facts:
            return _error("Falta 'hechos' (lista no vacía de {nombre, valor}).")

        accepted: list[Fact] = []
        rejected: list[str] = []
        sin_palabras = False
        for item in raw_facts:
            name = item.get("nombre") if isinstance(item, dict) else None
            value = item.get("valor") if isinstance(item, dict) else None
            expected = EXPECTED_FACT_TYPE.get(name) if isinstance(name, str) else None
            if not isinstance(name, str) or expected is None:
                rejected.append(f"{name!r}: nombre de hecho desconocido")
                continue
            normalizar = self._estructurados.get(name)
            if normalizar is not None:
                # Structured fact (its shape is known by a module: the tax module's children's detail, spouse's
                # figures, optimiser's preferences): validated and stored as canonical JSON text.
                try:
                    value = normalizar(value)
                except ValueError as exc:
                    rejected.append(f"{name}: {exc}")
                    continue
            coerced = _coerce_value(
                expected=expected,
                value_string=value if isinstance(value, str) else None,
                value_number=value if isinstance(value, int | float) and not isinstance(value, bool) else None,
                value_boolean=value if isinstance(value, bool) else None,
            )
            if coerced is None:
                rejected.append(f"{name}: valor {value!r} no es de tipo {expected.value}")
                continue
            # Only what the user said (docs/seguridad.md, S-16): text the model read cannot write the case.
            motivo = sin_respaldo(
                coerced,
                expected,
                item.get("cita_del_usuario"),
                self.mensajes_usuario,
                detalle=normalizar is not None,
            )
            if motivo is not None:
                rejected.append(f"{name}: no se ha guardado, {motivo}")
                sin_palabras = True
                continue
            accepted.append(
                Fact(
                    fact_id=f"fact_{name}",
                    name=name,
                    value=coerced,
                    value_type=expected,
                    source="user_statement_via_agent",
                    confidence=0.9,
                )
            )

        if accepted:
            merged = _merge_facts(accepted, self.case)
            known = {fact.name for fact in merged}
            update: dict[str, Any] = {
                "facts": merged,
                # CaseState forbids a fact being both known and missing.
                "missing_facts": [m for m in self.case.missing_facts if m.fact_name not in known],
                "updated_at": datetime.now(UTC),
            }
            period = next((fact for fact in accepted if fact.name == "periodo_fiscal"), None)
            if period is not None:
                update["tax_period"] = str(period.value)
            self.case = self.case.model_copy(update=update)
            self.case_changed = True

        lines = [f"Registrados: {', '.join(f'{fact.name}={fact.value}' for fact in accepted) or 'ninguno'}."]
        if rejected:
            lines.append("Rechazados: " + "; ".join(rejected) + ".")
        if sin_palabras:
            lines.append(AVISO_SIN_RESPALDO)
        return ToolOutcome(
            content=" ".join(lines),
            summary=f"{len(accepted)} registrados, {len(rejected)} rechazados",
            is_error=not accepted,
        )

    def _evaluate_rules(self, arguments: dict[str, Any]) -> ToolOutcome:
        del arguments
        assert self.case is not None
        result = evaluate_rules(case_state=self.case, recent_facts=[])
        if list(result.candidate_obligations) != list(self.case.obligation_candidates):
            self.case = self.case.model_copy(
                update={"obligation_candidates": result.candidate_obligations, "updated_at": datetime.now(UTC)}
            )
            self.case_changed = True
        payload = {
            "ejercicio_evaluado": result.fiscal_year,
            "obligaciones_candidatas": [
                {
                    "id": obligation.obligation_id,
                    "titulo": obligation.title,
                    "descripcion": obligation.description,
                    "riesgo": obligation.risk_level.value,
                    "fuentes_de_la_regla": [f"{ref.title} ({ref.locator})" for ref in obligation.evidence_refs],
                }
                for obligation in result.candidate_obligations
            ],
            "reglas_no_activadas": [
                {"regla": trace.rule_id, "hechos_que_faltan": trace.missing_facts}
                for trace in result.rule_traces
                if not trace.matched
            ],
            "aviso": "Catálogo de reglas parcial: la ausencia de una obligación no implica que no exista.",
        }
        return ToolOutcome(
            content=json.dumps(payload, ensure_ascii=False, default=str),
            summary=f"{len(result.candidate_obligations)} obligaciones candidatas",
        )

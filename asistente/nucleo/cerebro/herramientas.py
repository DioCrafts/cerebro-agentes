"""The brain's own tools, and the toolbox that runs them.

Moved up from the tax advisor (which now runs without them, see ``cerebro/fiscal.py``), because they cross domains:

* ``consultar_especialistas`` — several encargos at once to the collaboration runtime (replanning: the brain asks
  again, or asks a specialist it did not plan for). Validated like a plan (active specialists only, dependencies
  inside the call, no cycles) and bounded per turn (``CEREBRO_MAX_REPLANES``);
* the long-running tasks (``asistente/nucleo/tareas``: ``crear_tarea``, ``avanzar_tarea``…), with a case;
* research with sources (``investigar``), when the operator enables it;
* the household planner (``planificar_hogar``): the ONE tool that combines figures of several domains (IRPF,
  mortgage, benefits) — the brain must never do that itself;
* exact calculations over the user's own figures (``calculo_importes``, ``calculo_prestamo``);
* the decisions memory (``registrar_decision``, ``leer_preferencias``) and its context block, with a case.

Every tool keeps its own deterministic gate (the user's literal words for a decision or a task, the planner's
calculators, the research's privacy check); this module only wires them to the turn's shared case and ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from asistente.nucleo.agent.providers.base import ToolCall, ToolSpec
from asistente.nucleo.agent.tools import ExtraTool, ToolOutcome
from asistente.nucleo.cerebro.contrato import CLAVE_FISCAL, MAX_ENCARGOS, Encargo, ExpedienteCompartido, Intencion

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.agent.evidence import EvidenceLedger
    from asistente.nucleo.agent.providers.base import LLMProvider
    from asistente.nucleo.modulos.contrato import Modulo

logger = logging.getLogger(__name__)

CONSULTAR = "consultar_especialistas"


def _error(mensaje: str) -> ToolOutcome:
    return ToolOutcome(content=mensaje, summary=mensaje[:120], is_error=True)


def spec_consultar(especialistas: Mapping[str, tuple[str, str]]) -> ToolSpec:
    """``consultar_especialistas`` for these active specialists (key → (name, description))."""
    lista = "\n".join(f"- `{c}` — {n}: {' '.join(d.split())[:300]}" for c, (n, d) in especialistas.items())
    return ToolSpec(
        name=CONSULTAR,
        description=(
            "Consulta a uno o varios especialistas del hogar A LA VEZ (en paralelo, salvo que uno dependa de otro). "
            "Cada encargo es una pregunta AUTOSUFICIENTE (el especialista no ve la conversación: incluye los datos "
            "del usuario que necesite). Devuelve sus informes con fragmentos [F#] citables y la tabla de sus cifras. "
            "Úsala cuando falte información de un ámbito; no repitas encargos ya respondidos. Especialistas:\n" + lista
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "encargos": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_ENCARGOS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "description": "Identificador local («a», «b»…) para depende_de."},
                            "especialista": {"type": "string", "enum": sorted(especialistas)},
                            "pregunta": {"type": "string"},
                            "depende_de": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Ids locales de encargos de ESTA llamada cuyo informe necesita antes.",
                            },
                        },
                        "required": ["especialista", "pregunta"],
                    },
                }
            },
            "required": ["encargos"],
        },
    )


def encargos_de_llamada(argumentos: Mapping[str, Any], activos: Sequence[str], *, primero: int) -> list[Encargo] | str:
    """The encargos of one ``consultar_especialistas`` call with turn-unique ids (``e<primero>``…), or why not.

    Dependencies may only point at encargos of the same call (earlier reports are already in the brain's hands);
    the plan is validated with ``contrato.Intencion`` (unique ids, no cycles).
    """
    crudos = argumentos.get("encargos")
    if not isinstance(crudos, list) or not crudos:
        return "Falta 'encargos' (lista no vacía de {especialista, pregunta})."
    if len(crudos) > MAX_ENCARGOS:
        return f"Como mucho {MAX_ENCARGOS} encargos por llamada."
    locales: dict[str, str] = {}
    borradores: list[tuple[dict[str, Any], str]] = []
    for i, crudo in enumerate(crudos):
        if not isinstance(crudo, Mapping):
            return "Cada encargo es un objeto {especialista, pregunta}."
        clave = str(crudo.get("especialista") or "").strip()
        pregunta = str(crudo.get("pregunta") or "").strip()
        if clave not in activos:
            return f"No hay ningún especialista activo «{clave}». Disponibles: {', '.join(activos)}."
        if not pregunta:
            return f"El encargo para «{clave}» no tiene pregunta."
        global_id = f"e{primero + i}"
        if primero + i > 99:
            return "Se ha alcanzado el máximo de encargos de esta consulta."
        local = str(crudo.get("id") or global_id).strip()
        locales.setdefault(local, global_id)
        borradores.append((dict(crudo), global_id))
    encargos: list[dict[str, Any]] = []
    for crudo, global_id in borradores:
        deps = []
        for dep in crudo.get("depende_de") or []:
            if str(dep) not in locales:
                return f"«depende_de» solo puede nombrar encargos de esta misma llamada (no existe «{dep}»)."
            deps.append(locales[str(dep)])
        encargos.append(
            {
                "id": global_id,
                "especialista": str(crudo["especialista"]).strip(),
                "pregunta": str(crudo["pregunta"]).strip()[:2000],
                "depende_de": list(dict.fromkeys(deps)),
                "motivo": "Consulta del cerebro durante la síntesis",
            }
        )
    try:
        plan = Intencion(tipo="cruzada", encargos=encargos, confianza=1.0, origen="modelo")
    except ValidationError as exc:
        return f"Plan no válido: {exc.errors()[0].get('msg', 'error')}."
    return list(plan.encargos)


class CajaCerebro:
    """The brain's tool set for one turn. Duck-types the advisor's ``Toolbox`` where its helpers need it (the
    decisions memory reads ``.case``; the conversation loop calls ``specs`` and ``execute``)."""

    def __init__(self, expediente: ExpedienteCompartido, herramientas: Sequence[ExtraTool] = ()) -> None:
        self._expediente = expediente
        self._herramientas: dict[str, ExtraTool] = {}
        for spec, handler in herramientas:
            self._herramientas.setdefault(spec.name, (spec, handler))

    @property
    def case(self) -> Any:
        return self._expediente.case

    def anadir(self, herramientas: Sequence[ExtraTool]) -> None:
        for spec, handler in herramientas:
            self._herramientas.setdefault(spec.name, (spec, handler))

    def specs(self) -> list[ToolSpec]:
        return [spec for spec, _handler in self._herramientas.values()]

    def nombres(self) -> list[str]:
        return list(self._herramientas)

    def execute(self, call: ToolCall) -> ToolOutcome:
        if call.arguments_error:
            return _error(f"No se pudo interpretar la llamada: {call.arguments_error}. Reenvíala con JSON válido.")
        par = self._herramientas.get(call.name)
        if par is None:
            return _error(f"Herramienta no disponible: {call.name}.")
        try:
            resultado = par[1](call.arguments)
        except Exception as exc:  # a tool failure is reported to the model, never crashes the turn
            from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

            if isinstance(exc, PresupuestoAgotadoError):
                raise
            logger.exception("brain tool %s failed", call.name)
            return _error(f"La herramienta {call.name} falló: {type(exc).__name__}.")
        if isinstance(resultado, ToolOutcome):
            return resultado
        return _error(f"La herramienta {call.name} no devolvió un resultado válido.")


@dataclass
class HerramientasCerebro:
    """What the brain's tools add to one turn: the toolbox, the prompt paragraphs and the context blocks."""

    caja: CajaCerebro
    addendum: str = ""
    contexto: str = ""
    nombres: list[str] = field(default_factory=list)


def herramientas_del_cerebro(
    *,
    proveedor: LLMProvider | None,
    expediente: ExpedienteCompartido,
    ledger: EvidenceLedger,
    consulta: str,
    chat_history: Sequence[dict[str, Any]] | None,
    modulos: Sequence[Modulo] | None,
    consultar: ExtraTool | None = None,
    planificador: bool = True,
    intencion: Intencion | None = None,
) -> HerramientasCerebro:
    """The brain's tools for one turn. Each part is optional and fails alone (logged): a broken store never breaks
    the user's turn, the brain simply works without that tool.

    ``intencion``: the turn's plan, when the caller has it (the council's detector counts several specialists as a
    signal of a hard decision)."""
    caja = CajaCerebro(expediente, [consultar] if consultar is not None else [])
    addenda: list[str] = []
    contextos: list[str] = []
    case = expediente.case
    hogar = [m for m in modulos or () if getattr(m, "clave", CLAVE_FISCAL) != CLAVE_FISCAL]

    def intentar(nombre: str, construir: Callable[[], None]) -> None:
        try:
            construir()
        except Exception:
            logger.exception("brain tool %s unavailable this turn", nombre)

    def memoria() -> None:
        from asistente.nucleo.memoria.agente import MemoriaAgente
        from asistente.nucleo.memoria.prompt import MEMORIA_ADDENDUM

        decisiones = MemoriaAgente.para(case, query=consulta, chat_history=chat_history)
        if decisiones is None:
            return
        decisiones.vincular(caja)
        caja.anadir(decisiones.herramientas())
        addenda.append(MEMORIA_ADDENDUM)
        bloque = decisiones.contexto()
        if bloque:
            contextos.append(bloque)

    def tareas() -> None:
        from asistente.nucleo.tareas.herramientas import TareasAgente

        agente = TareasAgente.para(case, query=consulta, chat_history=chat_history, modulos=modulos or ())
        if agente is None:
            return
        agente.vincular(caja)
        caja.anadir(agente.herramientas())
        addenda.append(agente.addendum())

    def investigacion() -> None:
        from asistente.capacidades.investigacion.herramienta import InvestigacionAgente

        agente = InvestigacionAgente.para(proveedor, case, query=consulta, chat_history=chat_history)
        if agente is None:
            return
        caja.anadir(agente.herramientas())
        addenda.append(agente.addendum())

    def planificar() -> None:
        if case is None or not hogar or not planificador:
            return
        from asistente.nucleo.agent.prompts import PLANIFICADOR_ADDENDUM
        from asistente.nucleo.planificador.agente import PLANIFICAR_TOOL, planificar_para_agente

        def ejecutar(argumentos: dict[str, Any]) -> ToolOutcome:
            contenido, resumen, es_error = planificar_para_agente(argumentos, case=expediente.case, ledger=ledger)
            return ToolOutcome(content=contenido, summary=resumen, is_error=es_error)

        caja.anadir([(PLANIFICAR_TOOL, ejecutar)])
        addenda.append(PLANIFICADOR_ADDENDUM)

    def consejo() -> None:
        # The household council (asistente/nucleo/consejo): offered only when the deterministic detector finds a hard
        # decision (or the user asks for it). The tool never runs the council: it proposes it with its cost estimate
        # and the user confirms it in the chat's card.
        from asistente.nucleo.consejo.servicio import herramienta_convocar

        herramienta = herramienta_convocar(
            case=case, consulta=consulta, intencion=intencion, chat_history=chat_history, modulos=modulos
        )
        if herramienta is None:
            return
        caja.anadir(herramienta.herramientas)
        addenda.append(herramienta.addendum)

    def calculo() -> None:
        # Exact arithmetic over the user's own figures (amounts, fixed-rate loans): its output is a CALCULADORA
        # fragment whose figures the gate accepts as tool figures (``cerebro.cifras_de_herramienta``).
        from asistente.nucleo.calculo.herramientas import ADENDA_CEREBRO, herramientas_para_cerebro

        caja.anadir(herramientas_para_cerebro(ledger))
        addenda.append(ADENDA_CEREBRO)

    intentar("calculo", calculo)
    intentar("memoria", memoria)
    intentar("tareas", tareas)
    intentar("investigacion", investigacion)
    intentar("planificador", planificar)
    intentar("consejo", consejo)
    return HerramientasCerebro(
        caja=caja, addendum="".join(addenda), contexto="\n\n".join(contextos), nombres=caja.nombres()
    )


__all__ = [
    "CONSULTAR",
    "CajaCerebro",
    "HerramientasCerebro",
    "encargos_de_llamada",
    "herramientas_del_cerebro",
    "spec_consultar",
]

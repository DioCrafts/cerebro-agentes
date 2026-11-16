"""The decisions in the conversation: two tools for the central agent and a compact context block.

* ``registrar_decision`` records a decision the user has just EXPRESSED in the chat. It is refused unless
  ``cita_del_usuario`` (and ``razon``, when given) are the user's own words: after normalising accents,
  case and punctuation they must appear in one of the user's messages of this conversation (the question or
  the user turns of the history). The model cannot store a decision it inferred — the check is
  deterministic, not a promise of the prompt (which forbids inferring too).
* ``leer_preferencias`` returns every decision of the case with its state, so the agent can say why it did
  not propose something.
* :meth:`MemoriaAgente.contexto` is the block of decisions that still matter (in force, or that may come
  back once), added to the first user message; :func:`bloque_para_especialista` is the same restricted to
  one module, for that module's specialist (it never sees another module's decisions).

The decisions live in the cases' database (``settings.DECISION_STATE_DB_PATH``); reading never creates it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from asistente.nucleo.agent.providers.base import ToolSpec
from asistente.nucleo.agent.tools import ExtraTool, ToolOutcome
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.ambitos import CATALOGO, etiqueta
from asistente.nucleo.memoria.modelo import (
    TIPOS,
    Decision,
    Evaluacion,
    ambito_valido,
    descripcion_corta,
    evaluar,
    fecha_es,
    modulo_de_ambito,
    normalizar_texto,
)
from asistente.nucleo.memoria.prompt import ESPECIALISTA_MEMORIA
from asistente.nucleo.memoria.servicio import DecisionInvalidaError, decisiones_del_caso, describir, registrar
from asistente.plataforma import settings

MAX_LINEAS_CONTEXTO = 15
# A quote shorter than this (once normalised) says too little to be a decision («no», «vale»).
MIN_CITA = 8
_ESTADO_TEXTO = {
    "vigente": "vigente",
    "reabrible": "se puede reabrir",
    "caducada": "caducada",
    "reabierta": "ya se volvió a proponer",
    "sustituida": "sustituida",
}

REGISTRAR_DECISION = ToolSpec(
    name="registrar_decision",
    description=(
        "Guarda una decisión o preferencia que el usuario acaba de expresar en esta conversación, para no volver a "
        "proponerle lo que rechazó y no preguntarle dos veces. SOLO lo que el usuario haya dicho de forma expresa, "
        "nunca algo que deduzcas: `cita_del_usuario` son sus palabras literales y la herramienta comprueba que "
        "aparecen en sus mensajes. Ámbitos conocidos: "
        + "; ".join(f"`{a}` ({e})" for a, e in CATALOGO.items())
        + ". Se admiten ámbitos más concretos (`luz:cambio_tarifa:<oferta>`)."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ambito": {
                "type": "string",
                "description": "Módulo y palanca: «fiscal:plan_pensiones», «luz:cambio_tarifa»…",
            },
            "tipo": {
                "type": "string",
                "enum": list(TIPOS),
                "description": "rechazo (no quiere que se lo propongan), restriccion (no puede), preferencia "
                "(prefiere), aceptacion (ya lo hizo o lo acepta).",
            },
            "cita_del_usuario": {
                "type": "string",
                "description": "Las palabras literales del usuario en las que expresa la decisión.",
            },
            "razon": {
                "type": "string",
                "description": "Opcional: su motivo, también con sus palabras literales. Por defecto, la cita.",
            },
            "caducidad": {
                "type": "string",
                "description": "Opcional, AAAA-MM-DD: hasta cuándo vale, si el usuario lo dijo. Por defecto: 12 meses "
                "para rechazos y restricciones, 6 para aceptaciones, sin límite para preferencias.",
            },
            "reabrir_si": {
                "type": "array",
                "maxItems": 5,
                "description": "Opcional: cuándo merece la pena volver a proponerlo, si el usuario lo dijo (p. ej. "
                "«hasta que acabe la permanencia en marzo de 2027»).",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tipo": {"type": "string", "enum": ["fecha", "hecho", "mejora"]},
                        "fecha": {"type": "string", "description": "AAAA-MM-DD (tipo fecha)."},
                        "hecho": {"type": "string", "description": "Nombre del dato del expediente (tipo hecho)."},
                        "importe_rechazado": {"type": "number", "description": "Importe de lo rechazado (mejora)."},
                        "factor": {"type": "number", "description": "Cuántas veces mejor (mejora; por defecto 2)."},
                        "descripcion": {"type": "string", "description": "La condición, en palabras del usuario."},
                    },
                    "required": ["tipo"],
                },
            },
        },
        "required": ["ambito", "tipo", "cita_del_usuario"],
    },
)

LEER_PREFERENCIAS = ToolSpec(
    name="leer_preferencias",
    description=(
        "Devuelve las decisiones y preferencias del usuario guardadas en su expediente (rechazos, restricciones, "
        "preferencias, cosas que ya hizo), con su estado: vigente, se puede reabrir (y por qué), caducada… y cuántas "
        "veces se ha dejado de avisarle por cada una. Úsala cuando pregunte por qué no le avisas o no le propones "
        "algo, o antes de proponer algo que quizá ya rechazó."
    ),
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
)


def _error(mensaje: str) -> ToolOutcome:
    return ToolOutcome(content=mensaje, summary=mensaje[:120], is_error=True)


def _ruta() -> str:
    return settings.DECISION_STATE_DB_PATH


def _hechos(case: CaseState | None) -> dict[str, Any] | None:
    return {f.name: f.value for f in case.facts} if case is not None else None


def _lineas(
    decisiones: Sequence[Decision], *, hoy: date, hechos: dict[str, Any] | None, modulo: str | None = None
) -> list[str]:
    lineas: list[str] = []
    for decision in decisiones:
        if modulo is not None and decision.modulo != modulo:
            continue
        evaluacion = evaluar(decision, hoy=hoy, hechos=hechos)
        if evaluacion.estado in ("sustituida", "reabierta"):
            continue
        estado = _ESTADO_TEXTO[evaluacion.estado]
        if evaluacion.motivo and evaluacion.estado in ("reabrible", "caducada"):
            estado += f": {evaluacion.motivo}"
        linea = f"- [{decision.tipo} · {estado}] `{decision.ambito}` — " + descripcion_corta(
            decision, etiqueta(decision.ambito)
        )
        lineas.append(linea)
        if len(lineas) >= MAX_LINEAS_CONTEXTO:
            break
    return lineas


def bloque_para_especialista(case: CaseState | None, modulo: str) -> str:
    """The decisions of ONE module that still matter, for its specialist ("" when none)."""
    if case is None:
        return ""
    lineas = _lineas(decisiones_del_caso(_ruta(), case.case_id), hoy=reloj.hoy(), hechos=_hechos(case), modulo=modulo)
    return ESPECIALISTA_MEMORIA.format(lineas="\n".join(lineas)) if lineas else ""


def _en_palabras_del_usuario(texto: str, mensajes: Sequence[str]) -> bool:
    buscado = normalizar_texto(texto)
    return len(buscado) >= MIN_CITA and any(buscado in normalizar_texto(m) for m in mensajes)


def _fecha(valor: Any, campo: str) -> date | None:
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valor))
    except ValueError as exc:
        raise DecisionInvalidaError(f"«{campo}» debe ser una fecha AAAA-MM-DD.") from exc


def _decimal(valor: Any, campo: str) -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation as exc:
        raise DecisionInvalidaError(f"«{campo}» debe ser un número.") from exc


class MemoriaAgente:
    """The decisions of one run of the central agent (only with a case)."""

    def __init__(self, case: CaseState, mensajes_usuario: Sequence[str]) -> None:
        self._case = case
        self._mensajes = [m for m in mensajes_usuario if isinstance(m, str) and m.strip()]
        self._caso_actual: Callable[[], CaseState | None] = lambda: self._case

    @classmethod
    def para(
        cls, case: CaseState | None, *, query: str, chat_history: Sequence[dict[str, Any]] | None = None
    ) -> MemoriaAgente | None:
        """None without a case: the tools need a case to keep the decisions in."""
        if case is None:
            return None
        mensajes = [query] + [
            str(m.get("content") or "") for m in chat_history or () if isinstance(m, dict) and m.get("role") == "user"
        ]
        return cls(case, mensajes)

    def vincular(self, toolbox: Any) -> None:
        """Read the case from the advisor's toolbox (facts recorded during the run count)."""
        self._caso_actual = lambda: getattr(toolbox, "case", None) or self._case

    def herramientas(self) -> list[ExtraTool]:
        return [(REGISTRAR_DECISION, self._registrar), (LEER_PREFERENCIAS, self._leer)]

    def contexto(self) -> str:
        """The block for the first user message ("" when there is nothing to say)."""
        case = self._caso_actual() or self._case
        lineas = _lineas(decisiones_del_caso(_ruta(), case.case_id), hoy=reloj.hoy(), hechos=_hechos(case))
        if not lineas:
            return ""
        return (
            "<preferencias_del_usuario>\nDecisiones que el usuario ha tomado (sus palabras; detalle con "
            "`leer_preferencias`):\n" + "\n".join(lineas) + "\n</preferencias_del_usuario>"
        )

    # ------------------------------------------------------------------ #

    def _leer(self, arguments: dict[str, Any]) -> ToolOutcome:
        del arguments
        case = self._caso_actual() or self._case
        hoy = reloj.hoy()
        hechos = _hechos(case)
        decisiones = decisiones_del_caso(_ruta(), case.case_id)
        salida = []
        for decision in decisiones:
            evaluacion: Evaluacion = evaluar(decision, hoy=hoy, hechos=hechos)
            datos = describir(decision, evaluacion)
            datos.pop("user_id", None)
            datos.pop("case_id", None)
            salida.append(datos)
        return ToolOutcome(
            content=json.dumps({"hoy": hoy.isoformat(), "decisiones": salida}, ensure_ascii=False, default=str),
            summary=f"{len(salida)} decisión(es) guardada(s)",
        )

    def _registrar(self, arguments: dict[str, Any]) -> ToolOutcome:
        case = self._caso_actual() or self._case
        ambito = str(arguments.get("ambito") or "").strip()
        tipo = arguments.get("tipo")
        cita = str(arguments.get("cita_del_usuario") or "").strip()
        razon = str(arguments.get("razon") or "").strip()
        if tipo not in TIPOS:
            return _error(f"«tipo» debe ser uno de: {', '.join(TIPOS)}. No se ha guardado nada.")
        if not ambito_valido(ambito):
            return _error(
                "«ambito» no es válido: usa «módulo:palanca», p. ej. «fiscal:plan_pensiones». No se ha guardado nada."
            )
        modulo = modulo_de_ambito(ambito)
        if (
            modulo != "fiscal"
            and not any(a.split(":", 1)[0] == modulo for a in CATALOGO)
            and not _modulo_registrado(modulo)
        ):
            return _error(f"No hay ningún módulo «{modulo}». No se ha guardado nada.")
        if not _en_palabras_del_usuario(cita, self._mensajes):
            return _error(
                "No se ha guardado: `cita_del_usuario` debe reproducir literalmente lo que el usuario ha escrito en "
                "esta conversación. Solo se registran decisiones que el usuario exprese; no las deduzcas. Si crees "
                "que quiere decidir algo, pregúntaselo."
            )
        if razon and not _en_palabras_del_usuario(razon, self._mensajes):
            return _error(
                "No se ha guardado: `razon` debe ser también palabras literales del usuario (o déjala vacía y se "
                "usará la cita)."
            )
        try:
            condiciones = [
                {
                    "tipo": c.get("tipo"),
                    "fecha": _fecha(c.get("fecha"), "fecha"),
                    "hecho": c.get("hecho"),
                    "importe_referencia": _decimal(c.get("importe_rechazado"), "importe_rechazado"),
                    "factor": _decimal(c.get("factor"), "factor"),
                    "descripcion": str(c.get("descripcion") or "")[:300],
                }
                for c in arguments.get("reabrir_si") or []
                if isinstance(c, dict)
            ]
            caducidad = _fecha(arguments.get("caducidad"), "caducidad")
            resultado = registrar(
                _ruta(),
                case_id=case.case_id,
                user_id=case.user_id,
                ambito=ambito,
                tipo=tipo,
                razon=razon or cita,
                fuente="chat",
                caducidad=caducidad if caducidad is not None else "por_defecto",
                condiciones=[{k: v for k, v in c.items() if v not in (None, "")} for c in condiciones],
                hechos=_hechos(case),
            )
        except DecisionInvalidaError as exc:
            return _error(f"No se ha guardado: {exc}")
        decision = resultado.decision
        partes = [f"Guardado: {descripcion_corta(decision, etiqueta(decision.ambito))}."]
        if decision.caducidad is not None:
            partes.append(f"Se tendrá en cuenta hasta el {fecha_es(decision.caducidad)}.")
        if resultado.avisos_retirados:
            partes.append(f"Se han quitado {len(resultado.avisos_retirados)} aviso(s) de su bandeja que lo proponían.")
        if resultado.plan_retirado:
            partes.append("El plan de fin de ejercicio se recalculará con esta decisión.")
        partes.append("El usuario puede verlo y cambiarlo en «Lo que sé de tus preferencias».")
        return ToolOutcome(content=" ".join(partes), summary=f"decisión guardada: {decision.ambito} ({decision.tipo})")


def _modulo_registrado(clave: str) -> bool:
    try:
        from asistente.nucleo.modulos.registro import registro

        return clave in registro().modulos
    except Exception:
        return False


__all__ = ["LEER_PREFERENCIAS", "REGISTRAR_DECISION", "MemoriaAgente", "bloque_para_especialista"]

"""The central agent's task tools (core): propose, look, accept, confirm a step, pause, cancel.

Offered only with a case (like the decision memory's tools). The agent may PROPOSE a task; everything that
commits the user is checked deterministically against the conversation, like ``aceptar_propuesta``:

* ``crear_tarea`` stores a PROPOSAL (nothing runs). ``objetivo`` must be the user's own words. A custom plan
  goes through the gate (:mod:`~asistente.nucleo.tareas.validacion`); its errors go back to the model to fix.
* ``avanzar_tarea`` accepts a proposal only when the assistant's previous message showed the task (its title
  or its reference) and ``cita_del_usuario`` is the user's literal words in the CURRENT message, with a «sí»
  and no negation. On a task in progress it just advances it (nothing to confirm).
* ``confirmar_paso`` closes a step that waits for the user only when the previous assistant message named
  that step (its title) and the user's current message says yes in their words; skipping needs the user's
  literal words too. A pending ACTION is never confirmed here: only its own button runs it.
* ``pausar_tarea`` / ``cancelar_tarea`` need the user's literal words in the current message; a cancellation
  reason (their words) is recorded in the decision memory.

The tools write to the cases' database (``settings.DECISION_STATE_DB_PATH``). The engine runs here without
specialist consultations (those need a worker with a model).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
import json
from typing import Any

from asistente.nucleo.agent.providers.base import ToolSpec
from asistente.nucleo.agent.tools import ExtraTool, ToolOutcome
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.memoria.modelo import normalizar_texto
from asistente.nucleo.modulos.propuestas_chat import AFIRMACIONES, NEGACIONES
from asistente.nucleo.tareas import servicio
from asistente.nucleo.tareas.modelo import TIPOS_PASO, Tarea
from asistente.nucleo.tareas.motor import Motor
from asistente.nucleo.tareas.plantillas import catalogo
from asistente.nucleo.tareas.prompt import TAREAS_ADDENDUM
from asistente.nucleo.tareas.validacion import PlanInvalidoError
from asistente.plataforma import settings

MIN_CITA = 2
# Besides the general «yes» words: a step the user says is done.
HECHO = frozenset(
    {"hecho", "hecha", "listo", "lista", "terminado", "terminada", "acabado", "acabada", "ya", "empieza", "empezamos"}
)
_COMPUESTAS = ("de acuerdo", "por supuesto", "ya lo tengo", "ya esta")


def _error(mensaje: str) -> ToolOutcome:
    return ToolOutcome(content=mensaje, summary=mensaje[:120], is_error=True)


_REF_TAREA = {"type": "string", "description": "El id de la tarea (tar_…) o su referencia (T-XXXX)."}
_CITA = {"type": "string", "description": "Las palabras literales del usuario en su mensaje actual."}

CREAR_TAREA = ToolSpec(
    name="crear_tarea",
    description=(
        "Propone una tarea de varios pasos y días para un objetivo del usuario. Queda como PROPUESTA y no empieza "
        "hasta que el usuario la acepte (después, `avanzar_tarea`). Usa una plantilla (`plantilla`) o, si ninguna "
        "sirve, un plan propio (`titulo` y `pasos`) con pasos de estos tipos: " + ", ".join(TIPOS_PASO) + ". "
        "`calculo` ejecuta una herramienta de módulo que solo lee (con sus argumentos válidos); `accion`, una que "
        "actúa (quedará pendiente de que el usuario la confirme con su botón); `consulta`, al especialista de un "
        "módulo; `capacidad`, correo / navegador / documentos / investigacion (solo webs oficiales); `espera`, una "
        "fecha o un evento; `confirmacion_usuario`, algo que hace el usuario; `nota`, un dato. Sin enlaces ni "
        "código en los textos. Si el plan no es válido, la herramienta dice por qué; corrígelo."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "objetivo": {"type": "string", "description": "El objetivo con las palabras literales del usuario."},
            "plantilla": {"type": "string", "description": "Id de la plantilla (ver `estado_tareas`)."},
            "parametros": {"type": "object", "description": "Parámetros de la plantilla (p. ej. «ejercicio»)."},
            "titulo": {"type": "string", "description": "Título corto de un plan propio."},
            "pasos": {
                "type": "array",
                "maxItems": 15,
                "description": "Plan propio: pasos {id, titulo, descripcion, tipo, depende_de, opcional, "
                "herramienta, argumentos, modulo, pregunta, capacidad, operacion, parametros, espera}.",
                "items": {"type": "object"},
            },
        },
        "required": ["objetivo"],
    },
)

ESTADO_TAREAS = ToolSpec(
    name="estado_tareas",
    description=(
        "Las tareas del usuario con su estado, sus pasos, lo que cada una espera (de él, una fecha, un aviso) y los "
        "resultados de los pasos hechos; y las plantillas disponibles. Úsala antes de hablar de una tarea."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"tarea": {**_REF_TAREA, "description": "Opcional: solo esta tarea, con todo su detalle."}},
    },
)

AVANZAR_TAREA = ToolSpec(
    name="avanzar_tarea",
    description=(
        "Empieza una tarea PROPUESTA cuando el usuario ha dicho que sí en su mensaje actual (después de que le "
        "mostraras la tarea con su título), o hace avanzar una tarea en marcha o en pausa que el usuario quiere "
        "seguir. Comprueba que `cita_del_usuario` son sus palabras literales."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"tarea": _REF_TAREA, "cita_del_usuario": _CITA},
        "required": ["tarea", "cita_del_usuario"],
    },
)

CONFIRMAR_PASO = ToolSpec(
    name="confirmar_paso",
    description=(
        "Da por hecho (`decision: hecho`) o salta (`decision: omitir`) un paso de una tarea que espera al usuario, "
        "solo cuando él lo dice en su mensaje actual y tu mensaje anterior nombraba ese paso por su título. Con "
        "`fecha` (AAAA-MM-DD) guarda la fecha que él diga (p. ej. la de su cita). Las acciones pendientes no se "
        "confirman aquí: tienen su propio botón."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tarea": _REF_TAREA,
            "paso": {"type": "string", "description": "El id del paso."},
            "decision": {"type": "string", "enum": ["hecho", "omitir"]},
            "cita_del_usuario": _CITA,
            "fecha": {"type": "string", "description": "Opcional, AAAA-MM-DD, si el usuario la dijo."},
        },
        "required": ["tarea", "paso", "decision", "cita_del_usuario"],
    },
)

PAUSAR_TAREA = ToolSpec(
    name="pausar_tarea",
    description="Pausa una tarea porque el usuario lo pide en su mensaje actual (sus palabras literales).",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"tarea": _REF_TAREA, "cita_del_usuario": _CITA},
        "required": ["tarea", "cita_del_usuario"],
    },
)

CANCELAR_TAREA = ToolSpec(
    name="cancelar_tarea",
    description=(
        "Cancela una tarea porque el usuario lo pide en su mensaje actual (sus palabras literales). Sus acciones "
        "pendientes se rechazan. Si da un motivo, pásalo con sus palabras en `motivo`: se recordará para no "
        "volver a proponérselo."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tarea": _REF_TAREA,
            "cita_del_usuario": _CITA,
            "motivo": {"type": "string", "description": "Opcional: su motivo, con sus palabras literales."},
        },
        "required": ["tarea", "cita_del_usuario"],
    },
)


def _en(texto: str, mensaje: str) -> bool:
    buscado = normalizar_texto(texto)
    return len(buscado) >= MIN_CITA and buscado in normalizar_texto(mensaje)


def motivo_de_no_confirmacion(cita: str, mensaje: str) -> str | None:
    """None when ``cita`` is a «yes» in the user's own words in ``mensaje``; else why not (Spanish)."""
    if not _en(cita, mensaje):
        return "`cita_del_usuario` debe reproducir literalmente lo que el usuario ha escrito en su mensaje actual"
    palabras = set(normalizar_texto(cita).split())
    if palabras & NEGACIONES:
        return "su respuesta lleva una negación: pregúntale con claridad"
    plano = normalizar_texto(cita)
    if not (palabras & (AFIRMACIONES | HECHO) or any(c in plano for c in _COMPUESTAS)):
        return "sus palabras no dicen que sí («sí», «vale», «hecho», «adelante»…): pregúntaselo"
    return None


def _texto(mensaje: Any) -> str:
    contenido = mensaje.get("content") if isinstance(mensaje, dict) else None
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return " ".join(str(b.get("text") or "") for b in contenido if isinstance(b, dict))
    return ""


class TareasAgente:
    """The task tools of one run of the central agent (only with a case)."""

    def __init__(
        self,
        case: CaseState,
        *,
        query: str,
        usuario: Sequence[str],
        asistente: Sequence[str],
        motor_factory: Callable[[], Motor] | None = None,
        hoy: Callable[[], date] | None = None,
    ) -> None:
        self._case = case
        self._query = query
        self._usuario = [m for m in usuario if m.strip()]
        self._asistente = [m for m in asistente if m.strip()]
        self._motor_factory = motor_factory
        self._hoy = hoy
        self._motor: Motor | None = None

    @classmethod
    def para(
        cls,
        case: CaseState | None,
        *,
        query: str,
        chat_history: Sequence[dict[str, Any]] | None = None,
        modulos: Sequence[Any] | None = None,
    ) -> TareasAgente | None:
        """None without a case. With ``modulos`` given (the agent's hook), only when a household module is on or
        the case already has tasks: without them the advisor keeps exactly its earlier prompt and tools."""
        if case is None:
            return None
        if modulos is not None and not any(getattr(m, "clave", "fiscal") != "fiscal" for m in modulos):
            if not _tiene_tareas(case.case_id):
                return None
        historial = [m for m in chat_history or () if isinstance(m, dict)]
        usuario = [query] + [_texto(m) for m in historial if m.get("role") == "user"]
        asistente = [_texto(m) for m in historial if m.get("role") == "assistant"]
        return cls(case, query=query, usuario=usuario, asistente=asistente)

    def herramientas(self) -> list[ExtraTool]:
        return [
            (CREAR_TAREA, self._crear),
            (ESTADO_TAREAS, self._estado),
            (AVANZAR_TAREA, self._avanzar),
            (CONFIRMAR_PASO, self._confirmar),
            (PAUSAR_TAREA, self._pausar),
            (CANCELAR_TAREA, self._cancelar),
        ]

    def addendum(self) -> str:
        return TAREAS_ADDENDUM

    def vincular(self, toolbox: Any) -> None:
        """Nothing to share with the toolbox: the tasks are read from and written to the database."""
        del toolbox

    # ------------------------------------------------------------------ helpers

    def motor(self) -> Motor:
        if self._motor is None:
            if self._motor_factory is not None:
                self._motor = self._motor_factory()
            else:
                self._motor = Motor(settings.DECISION_STATE_DB_PATH, ejecutar_consultas=False, hoy=self._hoy)
        return self._motor

    def _dia(self) -> date:
        return self.motor().hoy()

    def _tarea(self, referencia: Any) -> Tarea | None:
        ref = str(referencia or "").strip()
        for tarea in self.motor().store.list(self._case.case_id):
            if ref in (tarea.id, tarea.ref) or ref.upper() == tarea.ref:
                return tarea
        return None

    def _ultimo_asistente(self) -> str:
        return self._asistente[-1] if self._asistente else ""

    def _activos(self) -> list[str]:
        return [m.clave for m in self.motor().modulos_de(self._case.case_id)]

    # ------------------------------------------------------------------ tools

    def _crear(self, args: dict[str, Any]) -> ToolOutcome:
        objetivo = str(args.get("objetivo") or "").strip()
        if not any(_en(objetivo, m) for m in self._usuario) or len(normalizar_texto(objetivo)) < 8:
            return _error(
                "No se ha creado: `objetivo` debe reproducir literalmente lo que el usuario ha pedido en esta "
                "conversación (sus palabras)."
            )
        plantilla_id = args.get("plantilla")
        pasos = args.get("pasos")
        if bool(plantilla_id) == bool(pasos):
            return _error("Indica una `plantilla` o un plan propio (`titulo` y `pasos`), no las dos cosas ni ninguna.")
        store = self.motor().store
        try:
            if plantilla_id:
                parametros = args.get("parametros") or {}
                if not isinstance(parametros, dict):
                    return _error("`parametros` debe ser un objeto.")
                tarea, avisos = servicio.proponer_desde_plantilla(
                    store,
                    case_id=self._case.case_id,
                    user_id=self._case.user_id,
                    plantilla_id=str(plantilla_id),
                    objetivo=objetivo,
                    hoy=self._dia(),
                    parametros=parametros,
                    modulos_activos=self._activos(),
                )
            else:
                titulo = str(args.get("titulo") or "").strip()
                if not titulo:
                    return _error("Un plan propio necesita `titulo`.")
                tarea, avisos = servicio.proponer_plan(
                    store,
                    case_id=self._case.case_id,
                    user_id=self._case.user_id,
                    titulo=titulo[:120],
                    objetivo=objetivo,
                    pasos=pasos if isinstance(pasos, list) else [],
                    modulos_activos=self._activos(),
                )
        except PlanInvalidoError as exc:
            return _error("No se ha creado la tarea; el plan no es válido: " + "; ".join(exc.errores[:8]))
        except ValueError as exc:
            return _error(f"No se ha creado la tarea: {exc}")
        lineas = [
            f"{i}. {p.titulo} ({p.tipo}{', opcional' if p.opcional else ''})" for i, p in enumerate(tarea.pasos, 1)
        ]
        partes = [
            f"PROPUESTA guardada, NO ha empezado: «{tarea.titulo}» ({tarea.ref}).",
            "Pasos:\n" + "\n".join(lineas),
        ]
        if avisos:
            partes.append("Notas: " + " ".join(avisos))
        retenida = self._decision_previa(tarea)
        if retenida:
            partes.append(retenida)
        partes.append(
            f"Muéstrale la tarea con su título «{tarea.titulo}» y sus pasos y pregúntale si la empezamos. Solo si "
            "dice que sí en su próximo mensaje, llama a `avanzar_tarea` con sus palabras."
        )
        return ToolOutcome(content="\n".join(partes), summary=f"tarea propuesta: {tarea.ref}")

    def _decision_previa(self, tarea: Tarea) -> str:
        """A «no» of the user to this kind of task still in force (it is still created: the user asked now)."""
        from asistente.nucleo.memoria.modelo import BLOQUEAN_AVISOS, cubre, evaluar, fecha_es
        from asistente.nucleo.memoria.servicio import decisiones_del_caso

        if not tarea.ambito_decision:
            return ""
        try:
            decisiones = decisiones_del_caso(settings.DECISION_STATE_DB_PATH, self._case.case_id)
        except Exception:
            return ""
        for decision in decisiones:
            if decision.tipo in BLOQUEAN_AVISOS and cubre(decision.ambito, tarea.ambito_decision):
                if evaluar(decision, hoy=self._dia(), hechos=None).vigente:
                    return (
                        f"Ojo: el {fecha_es(decision.fecha.date())} el usuario dijo «{decision.razon}» sobre esto. "
                        "Recuérdaselo antes de empezar."
                    )
        return ""

    def _estado(self, args: dict[str, Any]) -> ToolOutcome:
        store = self.motor().store
        if args.get("tarea"):
            tarea = self._tarea(args["tarea"])
            if tarea is None:
                return _error("No hay ninguna tarea con esa referencia en este expediente.")
            tarea = self.motor().avanzar(tarea.id) or tarea
            detalle = servicio.resumen(tarea)
            detalle["pasos"] = [
                {
                    "id": p.id,
                    "titulo": p.titulo,
                    "tipo": p.tipo,
                    "estado": p.estado,
                    "resultado": p.resultado.resumen[:1500] if p.resultado else None,
                    "evidencias": p.resultado.evidencias[:10] if p.resultado else [],
                    "fecha": p.resultado.fecha.isoformat() if p.resultado and p.resultado.fecha else None,
                    "error": p.error,
                }
                for p in tarea.pasos
            ]
            return ToolOutcome(
                content=json.dumps(detalle, ensure_ascii=False, default=str), summary=f"tarea {tarea.ref}"
            )
        tareas = [servicio.resumen(t) for t in store.list(self._case.case_id)]
        salida = {
            "hoy": self._dia().isoformat(),
            "tareas": tareas,
            "plantillas": [{k: p[k] for k in ("id", "titulo", "descripcion")} for p in catalogo()],
        }
        return ToolOutcome(
            content=json.dumps(salida, ensure_ascii=False, default=str), summary=f"{len(tareas)} tarea(s)"
        )

    def _avanzar(self, args: dict[str, Any]) -> ToolOutcome:
        tarea = self._tarea(args.get("tarea"))
        if tarea is None:
            return _error("No hay ninguna tarea con esa referencia en este expediente.")
        cita = str(args.get("cita_del_usuario") or "")
        motor = self.motor()
        try:
            if tarea.estado == "propuesta":
                ultimo = self._ultimo_asistente()
                if not (_en(tarea.titulo, ultimo) or tarea.ref.lower() in ultimo.lower()):
                    return _error(
                        f"No ha empezado: el usuario debe ver antes la tarea. Muéstrale «{tarea.titulo}» con sus pasos "
                        "y pregúntale si la empezamos."
                    )
                motivo = motivo_de_no_confirmacion(cita, self._query)
                if motivo is not None:
                    return _error(f"No ha empezado: {motivo}.")
                tarea = servicio.aceptar(motor, tarea.id, fuente="chat")
            elif tarea.estado == "pausada":
                if not _en(cita, self._query):
                    return _error("No se ha reanudado: `cita_del_usuario` debe ser literal de su mensaje actual.")
                tarea = servicio.reanudar(motor, tarea.id, fuente="chat")
            else:
                tarea = motor.avanzar(tarea.id) or tarea
        except servicio.OperacionNoPermitidaError as exc:
            return _error(str(exc))
        return self._informe(tarea)

    def _confirmar(self, args: dict[str, Any]) -> ToolOutcome:
        tarea = self._tarea(args.get("tarea"))
        if tarea is None:
            return _error("No hay ninguna tarea con esa referencia en este expediente.")
        paso = tarea.paso(str(args.get("paso") or ""))
        if paso is None:
            return _error("Esa tarea no tiene ese paso.")
        cita = str(args.get("cita_del_usuario") or "")
        decision = args.get("decision")
        fecha: date | None = None
        if args.get("fecha"):
            try:
                fecha = date.fromisoformat(str(args["fecha"]))
            except ValueError:
                return _error("`fecha` debe ser AAAA-MM-DD.")
            if not any(_en(p, self._query) for p in _formas_fecha(fecha)):
                return _error("No se ha guardado: la fecha debe ser la que el usuario ha escrito en su mensaje actual.")
        try:
            if decision == "hecho":
                if not _en(paso.titulo, self._ultimo_asistente()):
                    return _error(
                        f"No se ha confirmado: pregúntale antes por el paso «{paso.titulo}» (con ese título) y "
                        "confírmalo solo si dice que sí."
                    )
                motivo = motivo_de_no_confirmacion(cita, self._query)
                if motivo is not None:
                    return _error(f"No se ha confirmado: {motivo}.")
                tarea = servicio.confirmar_paso(self.motor(), tarea.id, paso.id, fuente="chat", fecha=fecha)
            elif decision == "omitir":
                if not _en(cita, self._query):
                    return _error("No se ha saltado: `cita_del_usuario` debe ser literal de su mensaje actual.")
                tarea = servicio.omitir_paso(self.motor(), tarea.id, paso.id, fuente="chat")
            else:
                return _error("`decision` debe ser «hecho» u «omitir».")
        except servicio.OperacionNoPermitidaError as exc:
            return _error(str(exc))
        return self._informe(tarea)

    def _pausar(self, args: dict[str, Any]) -> ToolOutcome:
        tarea = self._tarea(args.get("tarea"))
        if tarea is None:
            return _error("No hay ninguna tarea con esa referencia en este expediente.")
        if not _en(str(args.get("cita_del_usuario") or ""), self._query):
            return _error("No se ha pausado: `cita_del_usuario` debe ser literal de su mensaje actual.")
        try:
            tarea = servicio.pausar(self.motor(), tarea.id, fuente="chat")
        except servicio.OperacionNoPermitidaError as exc:
            return _error(str(exc))
        return self._informe(tarea)

    def _cancelar(self, args: dict[str, Any]) -> ToolOutcome:
        tarea = self._tarea(args.get("tarea"))
        if tarea is None:
            return _error("No hay ninguna tarea con esa referencia en este expediente.")
        if not _en(str(args.get("cita_del_usuario") or ""), self._query):
            return _error("No se ha cancelado: `cita_del_usuario` debe ser literal de su mensaje actual.")
        motivo = str(args.get("motivo") or "").strip() or None
        if motivo is not None and not any(_en(motivo, m) for m in self._usuario):
            return _error("No se ha cancelado: `motivo` debe ser palabras literales del usuario (o déjalo vacío).")
        try:
            tarea = servicio.cancelar(self.motor(), tarea.id, fuente="chat", motivo=motivo, user_id=self._case.user_id)
        except servicio.OperacionNoPermitidaError as exc:
            return _error(str(exc))
        return self._informe(tarea)

    def _informe(self, tarea: Tarea) -> ToolOutcome:
        datos = servicio.resumen(tarea)
        return ToolOutcome(
            content=json.dumps(datos, ensure_ascii=False, default=str),
            summary=f"tarea {tarea.ref}: {tarea.estado}",
        )


def _tiene_tareas(case_id: str) -> bool:
    """Read-only: whether the case has any task (never creates the tables)."""
    import sqlite3

    from asistente.nucleo.tareas.almacen import tabla_existe

    ruta = settings.DECISION_STATE_DB_PATH
    if not tabla_existe(ruta):
        return False
    try:
        with sqlite3.connect(ruta, timeout=5.0) as conn:
            return conn.execute("SELECT 1 FROM tareas WHERE case_id = ? LIMIT 1", (case_id,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _formas_fecha(fecha: date) -> list[str]:
    """How a user may write a date: 2027-03-02, 2/3/2027, 02/03/2027, 2/3, «2 de marzo»."""
    meses = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
    return [
        fecha.isoformat(),
        f"{fecha.day}/{fecha.month}/{fecha.year}",
        f"{fecha.day:02d}/{fecha.month:02d}/{fecha.year}",
        f"{fecha.day}/{fecha.month}",
        f"{fecha.day:02d}/{fecha.month:02d}",
        f"{fecha.day} de {meses[fecha.month - 1]}",
    ]


__all__ = [
    "AVANZAR_TAREA",
    "CANCELAR_TAREA",
    "CONFIRMAR_PASO",
    "CREAR_TAREA",
    "ESTADO_TAREAS",
    "PAUSAR_TAREA",
    "TareasAgente",
    "motivo_de_no_confirmacion",
]

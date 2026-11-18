"""Signals: what is open or new for one case, read from the stores the other packages own.

Every source is read the way its owner exposes it, and never written:

* the inbox (``NotificationStore``): every module's open notices — still due (read or not), or unread without a
  due date (the same rule as the deterministic weekly digest, ``bandeja.abiertas``);
* the pending actions (``AccionStore``): tools that act outside the app, waiting for the owner's confirmation;
* the long-running tasks (``TareaStore``): tasks proposed and not accepted yet, steps waiting for the user, failed
  ones, deadlines;
* the mail's proposals (``correo_propuestas``): data found in the mail waiting to be saved;
* the user's decisions (``asistente.nucleo.memoria``): decided since the last pass, about to expire, proposals
  held back because of them;
* the case's events on the bus (``eventos``) since the last pass: mail classified, facts changed, modules
  switched on (counts and names only: the bus never carries values).

A :class:`Senal` holds its facts (:class:`Hechos`: Decimal amounts, dates, counts) and its source
(:class:`Fuente`). It holds a :class:`Propuesta` ONLY when the layer that owns it produced a concrete one: a
pending action, a proposed task, a step waiting for the user, a mail proposal, or a task template whose declared
ámbito covers the notice (checked by instantiating the template through its own gate, nothing stored). The model
never creates a proposal: it can only reference one a signal carries, and the buttons call the existing
confirmation endpoints.

Text that does not come from code — anything derived from mail, a tool's arguments, a task's goal — is marked
``no_confiable`` and neutralised (:func:`limpiar`) before it can reach a model. Notice bodies are never read:
titles and facts are enough, and they keep the injection surface small.

Each source is read in its own ``try``: one failing source is logged and named in :attr:`Recogida.errores`;
the others still give their signals.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from asistente.conocimiento.llm.security import (
    neutralizar_inyeccion,
    parece_inyeccion,
    quitar_invisibles,
    sanitize_retrieved_context,
)

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("Europe/Madrid")

TipoSenal = Literal["aviso", "accion", "tarea", "paso", "propuesta_correo", "decision", "novedad"]
Gravedad = Literal["info", "aviso", "urgente"]
TipoFuente = Literal["aviso", "evento", "accion", "tarea", "propuesta_correo", "decision"]
TipoPropuesta = Literal["accion", "tarea", "paso", "correo", "plantilla"]

# A decision that expires within this many days is worth a line («vale hasta el…»).
DIAS_DECISION_CADUCA = 14
# A task deadline within this many days is a signal of its own.
DIAS_TAREA_VENCE = 14
# Free text from outside code is cut here before it reaches a model.
MAX_TITULO = 180
MAX_RESUMEN = 300
# Events read per pass at most (the bus is purged after 30 days anyway).
MAX_EVENTOS = 2000
# A case's first pass reads this many days of events.
DIAS_PRIMERA_PASADA = 7

ParametroValor = str | int | bool | None


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fuente(_Modelo):
    """Where a signal comes from: what the UI shows as a chip, and what the trace keeps."""

    tipo: TipoFuente
    id: str = Field(min_length=1, max_length=200)
    # The module it belongs to (``luz``), None when it concerns no module.
    modulo: str | None = None
    fecha: date | None = None
    titulo: str = Field(default="", max_length=400)
    # The inbox key of a notice (to link a task to its notices and to answer it).
    clave: str | None = Field(default=None, max_length=400)


class Hechos(_Modelo):
    """What a signal states, as data: the only numbers and dates a text about it may contain."""

    # The amount the source states (the module's figure for that notice; never a sum of several).
    importe: Decimal | None = None
    vence: date | None = None
    # Days from today to ``vence`` (negative when it is past).
    dias: int | None = None
    # When it happened: received, proposed, decided.
    fecha: date | None = None
    # Other figures code states («dias_rechazo»: 10, «correos»: 4).
    numeros: dict[str, Decimal] = Field(default_factory=dict)
    fechas: dict[str, date] = Field(default_factory=dict)


class Propuesta(_Modelo):
    """A concrete proposal produced by the layer that owns it, answered through that layer's own endpoint.

    * ``accion`` — ``POST /acciones/{ref}/confirmar|rechazar`` (a module tool that acts outside the app);
    * ``tarea`` — ``POST /tareas/{ref}/aceptar|cancelar`` (a task proposed and not accepted yet);
    * ``paso`` — ``POST /tareas/{ref}/pasos/{paso}/confirmar`` (the user says a step is done);
    * ``correo`` — ``POST /correo/propuestas/{ref}/confirmar|rechazar`` (data found in the mail);
    * ``plantilla`` — ``POST /tareas`` with ``aceptar: false``: the task is only PROPOSED; it starts when the user
      accepts it.
    """

    id: str = Field(pattern=r"^(accion|tarea|paso|correo|plantilla):[A-Za-z0-9_:\-]{1,120}$")
    tipo: TipoPropuesta
    ref: str = Field(min_length=1, max_length=120)
    paso: str | None = None
    parametros: dict[str, ParametroValor] = Field(default_factory=dict)
    objetivo: str | None = Field(default=None, max_length=1000)
    # What the button says, and what confirming does (Spanish, code-written).
    etiqueta: str = Field(min_length=1, max_length=80)
    resumen: str = Field(min_length=1, max_length=600)
    # The decision memory's ámbito (a rejection of it holds the proposal back).
    ambito: str | None = None


class Senal(_Modelo):
    """One thing that is open or new for the case."""

    # Stable across passes: ``aviso:<notice key>``, ``accion:<id>``, ``tarea:<id>``, ``paso:<task>:<step>``…
    id: str = Field(min_length=1, max_length=400)
    # The domain (module key: ``luz``, ``fiscal``, ``correo``, ``tareas``…).
    ambito: str = Field(min_length=1, max_length=40)
    tipo: TipoSenal
    titulo: str = Field(min_length=1, max_length=400)
    gravedad: Gravedad = "info"
    hechos: Hechos = Field(default_factory=Hechos)
    fuente: Fuente
    propuesta: Propuesta | None = None
    # New since the last pass.
    nueva: bool = False
    # Derived from mail, web, documents or a tool's arguments: neutralised before any model reads it.
    no_confiable: bool = False
    # A legal consequence, read from a legal data file (never recited): «se entiende rechazada a los…».
    consecuencia: str | None = Field(default=None, max_length=600)
    # An official deadline (the tax calendar, an administration's notification).
    oficial: bool = False
    # Missing it, or doing it, cannot be undone (a deadline, an action outside the app).
    irreversible: bool = False
    # A change in the law (BOE) that the tax module found affects this profile (``cambio_normativo``).
    normativa: bool = False
    # The decision memory's ámbito of what it proposes (``luz:cambio_tarifa:…``).
    ambito_decision: str | None = None
    # Ids of related signals (a task and its action and notices): grouped into one point.
    enlaces: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Marca:
    """Where the previous pass of the case stopped: what is newer is ``nueva``."""

    momento: datetime | None = None
    evento_id: int = 0


@dataclass
class Recogida:
    """What one collection found."""

    senales: list[Senal] = field(default_factory=list)
    # Sources that failed (logged); the pass goes on with the others.
    errores: list[str] = field(default_factory=list)
    # The last event id read (the next pass starts after it).
    evento_hasta: int = 0
    # The case's decisions and facts (the prioritisation applies them).
    decisiones: list[Any] = field(default_factory=list)
    hechos_caso: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def dia_de(momento: datetime) -> date:
    """The Spanish calendar day of an instant."""
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(ZONA).date()


def limpiar(texto: str, maximo: int = MAX_TITULO) -> str:
    """Text that may reach a model: no invisible characters, injection bait redacted, no markup, one line, cut."""
    plano = quitar_invisibles(str(texto or ""))
    plano = neutralizar_inyeccion(sanitize_retrieved_context(plano))
    plano = plano.replace("**", "").replace("`", "'")
    plano = " ".join(plano.split())
    return plano if len(plano) <= maximo else plano[: maximo - 1].rstrip() + "…"


def _sospechoso(*textos: str) -> bool:
    return any(texto and parece_inyeccion(texto) for texto in textos)


def _tabla_existe(db_path: str, tabla: str) -> bool:
    """Read-only check (never creates the file or the table)."""
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        fila = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _nuevo(momento: datetime | None, marca: Marca) -> bool:
    """Newer than the previous pass. On a case's first pass nothing is «new» (there is no «since»)."""
    if marca.momento is None or momento is None:
        return False
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento > marca.momento


def _decimal(valor: Any) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = Decimal(str(valor).strip().replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() else None


def _con_plazo(hechos: Hechos, hoy: date) -> Hechos:
    if hechos.vence is not None:
        hechos.dias = (hechos.vence - hoy).days
    return hechos


# --------------------------------------------------------------------------- #
# The inbox
# --------------------------------------------------------------------------- #

PREFIJO_TAREAS = "modulo:tareas:"
PREFIJO_ADMINISTRACION = "modulo:correo:administracion:"


def _regla_rechazo() -> tuple[int, str] | None:
    """The 10-day rule of electronic notifications, as the mail module's legal data file states it."""
    try:
        from asistente.capacidades.correo.normativa import cargar

        regla = cargar().regla("plazo_rechazo_notificacion")
        dias = int(regla.valores["dias_naturales"])
        return dias, f"{regla.resumen} ({regla.fuente.referencia})"
    except Exception:  # the mail module may be absent: the notice keeps its own wording
        logger.info("The rule of the electronic notifications could not be read", exc_info=True)
        return None


def de_avisos(notificaciones: Iterable[Any], *, hoy: date, marca: Marca) -> list[Senal]:
    """The open notices of the inbox (every module), the digest itself excluded."""
    from asistente.nucleo.memoria.ambitos import ambito_de_aviso
    from asistente.nucleo.proactive.bandeja import abiertas
    from asistente.nucleo.proactive.store import modulo_de

    salida: list[Senal] = []
    regla: tuple[int, str] | None | Literal[False] = False
    for n in abiertas(list(notificaciones), hoy=hoy):
        modulo = modulo_de(n.kind) or "fiscal"
        no_confiable = modulo == "correo" or _sospechoso(n.title)
        hechos = _con_plazo(
            Hechos(importe=n.amount, vence=n.due_date, fecha=dia_de(n.created_at)),
            hoy,
        )
        ambito = ambito_de_aviso(n.kind, n.key)
        senal = Senal(
            id=f"aviso:{n.key}"[:400],
            ambito=modulo,
            tipo="aviso",
            titulo=limpiar(n.title) or "Aviso",
            gravedad=n.severity,
            hechos=hechos,
            fuente=Fuente(
                tipo="aviso",
                id=n.id,
                modulo=modulo,
                fecha=dia_de(n.created_at),
                titulo=n.title[:400],
                clave=n.key[:400],
            ),
            nueva=_nuevo(n.created_at, marca),
            no_confiable=no_confiable,
            oficial=n.kind == "plazo",
            irreversible=n.kind == "plazo",
            normativa=n.kind == "cambio_normativo",
            ambito_decision=ambito.propuesta if ambito is not None else None,
        )
        if n.key.startswith(PREFIJO_ADMINISTRACION):
            if regla is False:
                regla = _regla_rechazo()
            senal.oficial = senal.irreversible = True
            if regla:
                dias, texto = regla
                senal.consecuencia = texto[:600]
                senal.hechos.numeros["dias_rechazo"] = Decimal(dias)
        if n.key.startswith(PREFIJO_TAREAS):
            tarea_id = n.key[len(PREFIJO_TAREAS) :].split(":", 1)[0]
            senal.enlaces.append(f"tarea:{tarea_id}")
        salida.append(senal)
    return salida


# --------------------------------------------------------------------------- #
# Pending actions
# --------------------------------------------------------------------------- #


def de_acciones(acciones: Iterable[Any], *, hoy: date, marca: Marca) -> list[Senal]:
    """Each pending action, with the proposal to confirm it (its own endpoint, its own confirmation)."""
    from asistente.nucleo.memoria.ambitos import ambito_de_accion

    salida: list[Senal] = []
    for accion in acciones:
        if accion.estado != "pendiente":
            continue
        creada = dia_de(accion.created_at)
        resumen = limpiar(accion.resumen, MAX_RESUMEN)
        ambito = ambito_de_accion(accion.modulo, accion.herramienta)
        salida.append(
            Senal(
                id=f"accion:{accion.id}",
                ambito=accion.modulo,
                tipo="accion",
                titulo=limpiar(f"Pendiente de tu confirmación: {resumen}"),
                gravedad="aviso",
                hechos=Hechos(fecha=creada, numeros={"dias_esperando": Decimal(max(0, (hoy - creada).days))}),
                fuente=Fuente(tipo="accion", id=accion.id, modulo=accion.modulo, fecha=creada, titulo=resumen),
                propuesta=Propuesta(
                    id=f"accion:{accion.id}",
                    tipo="accion",
                    ref=accion.id,
                    etiqueta="Confirmar la acción",
                    resumen=f"Si la confirmas, se hará esto: {resumen}",
                    ambito=ambito,
                ),
                nueva=_nuevo(accion.created_at, marca),
                # Its summary carries the tool's arguments, which a model chose.
                no_confiable=True,
                irreversible=True,
                ambito_decision=ambito,
            )
        )
    return salida


# --------------------------------------------------------------------------- #
# Long-running tasks
# --------------------------------------------------------------------------- #


def _paso_confirmable(tarea: Any, paso: Any) -> bool:
    """A step the card may confirm with one button: the user says it is done, and no later step needs the date
    they would give when confirming it (that one is confirmed in «Tareas», where the date can be entered)."""
    if paso.tipo != "confirmacion_usuario" or paso.espera_actual is None or paso.espera_actual.reintentar:
        return False
    return not any(
        otro.espera is not None and otro.espera.fecha_de_paso == paso.id for otro in tarea.pasos if otro is not paso
    )


def de_tareas(tareas: Iterable[Any], *, hoy: date, marca: Marca) -> list[Senal]:
    """Tasks that need the user: proposed, a step waiting for them, failed, or close to their deadline."""
    from asistente.nucleo.tareas.modelo import TERMINALES

    salida: list[Senal] = []
    for tarea in tareas:
        if tarea.estado in TERMINALES:
            continue
        enlaces = [f"aviso:{clave}" for clave in tarea.vinculos.avisos] + [
            f"accion:{accion}" for accion in tarea.vinculos.acciones
        ]
        titulo = limpiar(tarea.titulo, 120)
        creada = dia_de(tarea.creada)
        base: dict[str, Any] = {
            "ambito": "tareas",
            "nueva": _nuevo(tarea.actualizada, marca),
            "ambito_decision": tarea.ambito_decision,
        }
        propias: list[Senal] = []
        if tarea.estado == "propuesta":
            propias.append(
                Senal(
                    id=f"tarea:{tarea.id}",
                    tipo="tarea",
                    titulo=f"Tarea propuesta, pendiente de que la aceptes: {titulo}",
                    gravedad="info",
                    hechos=_con_plazo(Hechos(vence=tarea.vence, fecha=creada), hoy),
                    fuente=Fuente(tipo="tarea", id=tarea.id, modulo="tareas", fecha=creada, titulo=titulo),
                    propuesta=Propuesta(
                        id=f"tarea:{tarea.id}",
                        tipo="tarea",
                        ref=tarea.id,
                        etiqueta="Aceptar la tarea",
                        resumen=(
                            f"Empezar la tarea «{titulo}» ({len(tarea.pasos)} pasos). Cada acción que salga de ella "
                            "te pedirá confirmación aparte."
                        ),
                        ambito=tarea.ambito_decision,
                    ),
                    enlaces=list(enlaces),
                    **base,
                )
            )
        for paso in tarea.pasos:
            espera = paso.espera_actual
            if paso.estado != "esperando" or espera is None or espera.motivo not in ("usuario", "accion"):
                continue
            if espera.motivo == "accion":
                # The pending action carries the proposal: the step is the same matter.
                if espera.accion_id:
                    enlaces.append(f"accion:{espera.accion_id}")
                continue
            propuesta = None
            if _paso_confirmable(tarea, paso):
                propuesta = Propuesta(
                    id=f"paso:{tarea.id}:{paso.id}",
                    tipo="paso",
                    ref=tarea.id,
                    paso=paso.id,
                    etiqueta="Marcar como hecho",
                    resumen=f"Confirmar que ya has hecho «{limpiar(paso.titulo, 120)}»; la tarea sigue con el paso siguiente.",
                    ambito=tarea.ambito_decision,
                )
            propias.append(
                Senal(
                    id=f"paso:{tarea.id}:{paso.id}",
                    tipo="paso",
                    titulo=f"{titulo}: {limpiar(paso.titulo, 120)}",
                    gravedad="aviso",
                    hechos=_con_plazo(Hechos(vence=espera.hasta or tarea.vence, fecha=dia_de(espera.desde)), hoy),
                    fuente=Fuente(
                        tipo="tarea", id=tarea.id, modulo="tareas", fecha=dia_de(espera.desde), titulo=titulo
                    ),
                    propuesta=propuesta,
                    enlaces=list(enlaces),
                    **base,
                )
            )
        if tarea.estado == "fallida":
            propias.append(
                Senal(
                    id=f"tarea:{tarea.id}:fallida",
                    tipo="tarea",
                    titulo=f"La tarea «{titulo}» tiene un paso que ha fallado",
                    gravedad="aviso",
                    hechos=_con_plazo(Hechos(vence=tarea.vence, fecha=dia_de(tarea.actualizada)), hoy),
                    fuente=Fuente(
                        tipo="tarea", id=tarea.id, modulo="tareas", fecha=dia_de(tarea.actualizada), titulo=titulo
                    ),
                    enlaces=list(enlaces),
                    **base,
                )
            )
        if not propias and tarea.vence is not None and 0 <= (tarea.vence - hoy).days <= DIAS_TAREA_VENCE:
            propias.append(
                Senal(
                    id=f"tarea:{tarea.id}:vence",
                    tipo="tarea",
                    titulo=f"La tarea «{titulo}» tiene de plazo hasta el {tarea.vence:%d/%m/%Y}",
                    gravedad="info",
                    hechos=_con_plazo(Hechos(vence=tarea.vence, fecha=creada), hoy),
                    fuente=Fuente(tipo="tarea", id=tarea.id, modulo="tareas", fecha=creada, titulo=titulo),
                    enlaces=list(enlaces),
                    **base,
                )
            )
        for senal in propias:
            # The objective is the user's words: kept out of the titles; the title is enough.
            senal.no_confiable = senal.no_confiable or _sospechoso(tarea.titulo)
            senal.enlaces = list(dict.fromkeys(enlaces))
        salida.extend(propias)
    return salida


# --------------------------------------------------------------------------- #
# The mail's proposals
# --------------------------------------------------------------------------- #


def de_propuestas_correo(propuestas: Iterable[Any], *, marca: Marca, nombres: Mapping[str, str]) -> list[Senal]:
    """Data found in the mail, waiting to be saved in another module (``nota`` proposals save nothing: left out)."""
    salida: list[Senal] = []
    for propuesta in propuestas:
        if propuesta.estado != "pendiente" or propuesta.tipo == "nota":
            continue
        importe = next(
            (_decimal(e.valor) for e in propuesta.evidencias if e.campo == "importe" and _decimal(e.valor) is not None),
            None,
        )
        destino = nombres.get(propuesta.destino, propuesta.destino)
        titulo = limpiar(propuesta.titulo, 140)
        salida.append(
            Senal(
                id=f"correo:{propuesta.id}",
                ambito="correo",
                tipo="propuesta_correo",
                titulo=limpiar(f"Dato encontrado en tu correo para {destino}: {titulo}"),
                gravedad="info",
                hechos=Hechos(importe=importe, fecha=propuesta.origen.fecha),
                fuente=Fuente(
                    tipo="propuesta_correo",
                    id=propuesta.id,
                    modulo="correo",
                    fecha=propuesta.origen.fecha,
                    titulo=titulo,
                ),
                propuesta=Propuesta(
                    id=f"correo:{propuesta.id}",
                    tipo="correo",
                    ref=propuesta.id,
                    etiqueta="Guardar el dato",
                    resumen=f"Guardar en «{destino}» lo que encontré en el correo: {titulo}",
                ),
                nueva=_nuevo(propuesta.creada, marca),
                no_confiable=True,
            )
        )
    return salida


# --------------------------------------------------------------------------- #
# The user's decisions
# --------------------------------------------------------------------------- #


def de_decisiones(
    decisiones: Sequence[Any], *, hoy: date, marca: Marca, hechos_caso: Mapping[str, Any] | None
) -> list[Senal]:
    """Decisions decided since the last pass, about to expire, or that held a proposal back since then."""
    from asistente.nucleo.memoria.ambitos import etiqueta
    from asistente.nucleo.memoria.modelo import ETIQUETAS_TIPO, evaluar

    salida: list[Senal] = []
    for decision in decisiones:
        if decision.sustituida_por:
            continue
        evaluacion = evaluar(decision, hoy=hoy, hechos=dict(hechos_caso) if hechos_caso is not None else None)
        que = limpiar(etiqueta(decision.ambito), 120)
        fecha = dia_de(decision.fecha)
        fuente = Fuente(tipo="decision", id=decision.id, modulo=decision.modulo, fecha=fecha, titulo=que)
        comun: dict[str, Any] = {"ambito": decision.modulo[:40] or "memoria", "tipo": "decision", "fuente": fuente}
        if _nuevo(decision.fecha, marca) and marca.momento is not None:
            salida.append(
                Senal(
                    id=f"decision:{decision.id}",
                    titulo=f"Tomo nota de tu decisión: {ETIQUETAS_TIPO[decision.tipo].lower()} — {que}",
                    hechos=Hechos(fecha=fecha, vence=decision.caducidad),
                    nueva=True,
                    **comun,
                )
            )
        if evaluacion.vigente and decision.caducidad is not None:
            dias = (decision.caducidad - hoy).days
            if 0 < dias <= DIAS_DECISION_CADUCA:
                salida.append(
                    Senal(
                        id=f"decision:{decision.id}:caduca",
                        titulo=f"Tu decisión sobre «{que}» vale hasta el {decision.caducidad:%d/%m/%Y}",
                        hechos=Hechos(vence=decision.caducidad, dias=dias, fecha=fecha),
                        **comun,
                    )
                )
        if (
            decision.omisiones
            and decision.ultima_omision is not None
            and (marca.momento is None or decision.ultima_omision >= dia_de(marca.momento))
        ):
            salida.append(
                Senal(
                    id=f"decision:{decision.id}:omision",
                    titulo=f"No he vuelto a proponerte «{que}» porque me lo pediste",
                    hechos=Hechos(fecha=decision.ultima_omision, numeros={"veces": Decimal(decision.omisiones)}),
                    **comun,
                )
            )
    return salida


# --------------------------------------------------------------------------- #
# The case's events
# --------------------------------------------------------------------------- #


def leer_eventos(db_path: str, case_id: str, *, desde_id: int) -> list[tuple[int, str, dict[str, Any], datetime]]:
    """(id, tipo, payload, created_at) of the case's events after ``desde_id`` (read-only; [] without a bus)."""
    if not _tabla_existe(db_path, "eventos"):
        return []
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)
    try:
        filas = conn.execute(
            "SELECT id, tipo, payload_json, created_at FROM eventos WHERE id > ? AND case_id = ? ORDER BY id LIMIT ?",
            (desde_id, case_id, MAX_EVENTOS),
        ).fetchall()
    finally:
        conn.close()
    salida = []
    for id_, tipo, payload, creado in filas:
        try:
            datos = json.loads(payload)
        except ValueError:
            datos = {}
        momento = datetime.fromisoformat(str(creado))
        salida.append((int(id_), str(tipo), datos if isinstance(datos, dict) else {}, momento))
    return salida


def de_eventos(
    eventos: Sequence[tuple[int, str, dict[str, Any], datetime]], *, nombres: Mapping[str, str], nueva: bool = True
) -> list[Senal]:
    """What happened on the bus, aggregated: one context signal per kind (counts and names, never values)."""
    from asistente.nucleo.eventos.tipos import CARGOS_IMPORTADOS, CORREO_CLASIFICADO, MODULO_ACTIVADO, PERFIL_CAMBIADO

    por_tipo: dict[str, list[tuple[int, dict[str, Any], datetime]]] = {}
    for id_, tipo, payload, momento in eventos:
        por_tipo.setdefault(tipo, []).append((id_, payload, momento))
    salida: list[Senal] = []

    def senal(tipo: str, ambito: str, titulo: str, numeros: dict[str, Decimal]) -> None:
        filas = por_tipo[tipo]
        ultimo = max(f[0] for f in filas)
        fecha = dia_de(max(f[2] for f in filas))
        salida.append(
            Senal(
                id=f"novedad:{tipo}:{ultimo}",
                ambito=ambito,
                tipo="novedad",
                titulo=titulo,
                hechos=Hechos(fecha=fecha, numeros=numeros),
                fuente=Fuente(tipo="evento", id=str(ultimo), modulo=ambito, fecha=fecha, titulo=titulo),
                nueva=nueva,
            )
        )

    if CORREO_CLASIFICADO in por_tipo:
        nuevos = sum(int(_decimal(p.get("nuevos")) or 0) for _i, p, _m in por_tipo[CORREO_CLASIFICADO])
        administracion = sum(
            int(_decimal((p.get("categorias") or {}).get("administracion")) or 0)
            for _i, p, _m in por_tipo[CORREO_CLASIFICADO]
            if isinstance(p.get("categorias"), dict)
        )
        if nuevos > 0:
            titulo = (
                f"Correo nuevo: {nuevos} mensaje{'s' if nuevos != 1 else ''} clasificado{'s' if nuevos != 1 else ''}"
            )
            numeros = {"correos": Decimal(nuevos)}
            if administracion:
                titulo += f", {administracion} de la Administración"
                numeros["administracion"] = Decimal(administracion)
            senal(CORREO_CLASIFICADO, "correo", titulo, numeros)
    if PERFIL_CAMBIADO in por_tipo:
        nombres_hechos = {
            n
            for _i, p, _m in por_tipo[PERFIL_CAMBIADO]
            for n in [*(p.get("hechos") or ()), *(p.get("borrados") or ())]
            if isinstance(n, str)
        }
        if nombres_hechos:
            n = len(nombres_hechos)
            senal(
                PERFIL_CAMBIADO,
                "expediente",
                f"Has actualizado {n} dato{'s' if n != 1 else ''} de tu expediente",
                {"datos": Decimal(n)},
            )
    if CARGOS_IMPORTADOS in por_tipo:
        senal(CARGOS_IMPORTADOS, "suscripciones", "Se han importado cargos de tu banco", {})
    if MODULO_ACTIVADO in por_tipo:
        claves = list(dict.fromkeys(str(p.get("modulo")) for _i, p, _m in por_tipo[MODULO_ACTIVADO] if p.get("modulo")))
        if claves:
            lista = ", ".join(nombres.get(c, c) for c in claves)
            senal(MODULO_ACTIVADO, "modulos", limpiar(f"Has activado: {lista}"), {})
    return salida


# --------------------------------------------------------------------------- #
# Task templates a notice may lead to
# --------------------------------------------------------------------------- #


def proponer_plantillas(
    senales: list[Senal],
    *,
    case_id: str,
    hoy: date,
    tareas: Sequence[Any],
    modulos_activos: Sequence[str] | None,
) -> None:
    """Attach a task template to the notices whose ámbito the template DECLARES it serves (``ambito_decision``).

    Only when no task of that template is open for the case, and the template instantiates through its own gate
    (:func:`asistente.nucleo.tareas.plantillas.instanciar`: same check as ``POST /tareas``; nothing is stored). One
    signal per template. Pressing the button only PROPOSES the task (``aceptar: false``).
    """
    from asistente.nucleo.memoria.modelo import cubre
    from asistente.nucleo.tareas.modelo import TERMINALES
    from asistente.nucleo.tareas.plantillas import instanciar, plantillas

    abiertas = {t.plantilla for t in tareas if t.plantilla and t.estado not in TERMINALES}
    usadas: set[str] = set()
    for plantilla in plantillas():
        if not plantilla.ambito_decision or plantilla.id in abiertas or plantilla.id in usadas:
            continue
        for senal in senales:
            if senal.tipo != "aviso" or senal.propuesta is not None or not senal.ambito_decision:
                continue
            if not cubre(plantilla.ambito_decision, senal.ambito_decision):
                continue
            try:
                tarea, _avisos = instanciar(
                    plantilla.id,
                    case_id=case_id,
                    user_id=None,
                    objetivo=plantilla.titulo,
                    hoy=hoy,
                    modulos_activos=modulos_activos,
                )
            except Exception:  # the template does not fit this case now (a module off…): no proposal
                logger.info("Template %s not proposed for case %s", plantilla.id, case_id, exc_info=True)
                break
            descripcion = plantilla.descripcion.split(". ")[0].rstrip(".")
            senal.propuesta = Propuesta(
                id=f"plantilla:{plantilla.id}",
                tipo="plantilla",
                ref=plantilla.id,
                parametros=dict(tarea.parametros),
                objetivo=plantilla.titulo,
                etiqueta="Preparar la tarea",
                resumen=(
                    f"Dejar preparada la tarea «{plantilla.titulo}» ({descripcion.lower()}). No empieza hasta que la "
                    "aceptes."
                )[:600],
                ambito=plantilla.ambito_decision,
            )
            usadas.add(plantilla.id)
            break


# --------------------------------------------------------------------------- #
# Everything for one case
# --------------------------------------------------------------------------- #


def _nombres_modulos() -> dict[str, str]:
    try:
        from asistente.nucleo.modulos.registro import registro

        return {m.clave: m.nombre for m in registro().modulos.values()}
    except Exception:
        logger.warning("Could not read the module registry for the pass", exc_info=True)
        return {}


def recoger(
    case: Any,
    *,
    hoy: date,
    db_path: str,
    store: Any,
    acciones: Any | None = None,
    marca: Marca | None = None,
    nombres: Mapping[str, str] | None = None,
    modulos_activos: Sequence[str] | None = None,
) -> Recogida:
    """Every signal of one case. ``acciones``: an ``AccionStore`` (opened here when its table exists)."""
    marca = marca or Marca()
    nombres = {**_nombres_modulos(), **dict(nombres or {})}
    recogida = Recogida(evento_hasta=marca.evento_id)
    case_id = case.case_id
    recogida.hechos_caso = {f.name: f.value for f in getattr(case, "facts", ())}

    def paso(nombre: str, funcion: Callable[[], list[Senal]]) -> None:
        try:
            recogida.senales.extend(funcion())
        except Exception:
            logger.exception("Pass source %s failed for case %s", nombre, case_id)
            recogida.errores.append(nombre)

    paso("avisos", lambda: de_avisos(store.list(case_id), hoy=hoy, marca=marca))

    def _acciones() -> list[Senal]:
        if acciones is not None:
            return de_acciones(acciones.list(case_id, estado="pendiente"), hoy=hoy, marca=marca)
        if not _tabla_existe(db_path, "acciones_pendientes"):
            return []
        from asistente.nucleo.modulos.acciones import AccionStore

        propio = AccionStore(db_path)
        try:
            return de_acciones(propio.list(case_id, estado="pendiente"), hoy=hoy, marca=marca)
        finally:
            propio.close()

    paso("acciones", _acciones)

    tareas: list[Any] = []

    def _tareas() -> list[Senal]:
        if not _tabla_existe(db_path, "tareas"):
            return []
        from asistente.nucleo.tareas.almacen import TareaStore

        propio = TareaStore(db_path)
        try:
            tareas.extend(propio.list(case_id))
        finally:
            propio.close()
        return de_tareas(tareas, hoy=hoy, marca=marca)

    paso("tareas", _tareas)

    def _correo() -> list[Senal]:
        if not _tabla_existe(db_path, "correo_propuestas"):
            return []
        from asistente.capacidades.correo import almacen as correo

        return de_propuestas_correo(
            correo.propuestas(case_id, estado="pendiente", db_path=db_path), marca=marca, nombres=nombres
        )

    paso("correo", _correo)

    def _decisiones() -> list[Senal]:
        from asistente.nucleo.memoria.servicio import decisiones_del_caso

        recogida.decisiones = decisiones_del_caso(db_path, case_id)
        return de_decisiones(recogida.decisiones, hoy=hoy, marca=marca, hechos_caso=recogida.hechos_caso)

    paso("decisiones", _decisiones)

    def _eventos() -> list[Senal]:
        eventos = leer_eventos(db_path, case_id, desde_id=marca.evento_id)
        if eventos:
            recogida.evento_hasta = max(e[0] for e in eventos)
        if marca.momento is None:
            # A first pass looks at the last week only (not at everything the bus still keeps).
            eventos = [e for e in eventos if dia_de(e[3]) >= hoy - timedelta(days=DIAS_PRIMERA_PASADA)]
        return de_eventos(eventos, nombres=nombres, nueva=marca.momento is not None)

    paso("eventos", _eventos)

    try:
        proponer_plantillas(recogida.senales, case_id=case_id, hoy=hoy, tareas=tareas, modulos_activos=modulos_activos)
    except Exception:
        logger.exception("Task templates could not be matched for case %s", case_id)
        recogida.errores.append("plantillas")
    # One signal per id (a source read twice never counts twice).
    unicas: dict[str, Senal] = {}
    for senal in recogida.senales:
        unicas.setdefault(senal.id, senal)
    recogida.senales = list(unicas.values())
    return recogida


__all__ = [
    "DIAS_DECISION_CADUCA",
    "Fuente",
    "Gravedad",
    "Hechos",
    "Marca",
    "Propuesta",
    "Recogida",
    "Senal",
    "TipoPropuesta",
    "TipoSenal",
    "de_acciones",
    "de_avisos",
    "de_decisiones",
    "de_eventos",
    "de_propuestas_correo",
    "de_tareas",
    "dia_de",
    "leer_eventos",
    "limpiar",
    "proponer_plantillas",
    "recoger",
]

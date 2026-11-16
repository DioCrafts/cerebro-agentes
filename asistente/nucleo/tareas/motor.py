"""The engine: a deterministic state machine that advances each task's steps whose dependencies are done.

One pass over a task (:meth:`Motor.avanzar`), under the task's lease and over the version it read:

1. signals first: events of the bus that a waiting step was waiting for (written by :class:`ConsumidorTareas`);
2. then, until nothing moves, every step whose dependencies are ``hecho``/``omitido``:

   * ``pendiente`` → run it by type (a module tool, a specialist, a capability, start a wait, ask the user,
     queue a pending action, show a note);
   * ``esperando`` → check whether the wait is over (the date came; the pending action was confirmed,
     rejected or failed; the module was switched on; an event wait timed out → ask the user);
3. the task's state is derived from its steps, its next review date is the earliest date it waits for, and
   the bus keeps one timer per case for the earliest of its tasks (``fecha.umbral`` for ``tareas``).

What makes it safe to run again at any time (restart, two workers, the API and a worker):

* the lease (``TareaStore.reclamar``) and the optimistic version: one writer at a time, never a lost update;
* everything a step creates elsewhere has a key derived from (task, step, attempt): the notice key
  ``modulo:tareas:<tarea>:<paso>[:…]`` (the inbox refuses a duplicate) and the pending action id
  ``acc_<hash>`` (created once, then read back). A crash between the side effect and the save repeats the
  step, and repeating it creates nothing new;
* reading steps (``calculo``, ``capacidad``) are idempotent; an exception is retried with exponential backoff
  and, after the last attempt, the step fails and the user is told (notice) and can retry it.

Nothing here acts outside the app: an ``accion`` step only creates a pending action, and the task goes on only
once the owner confirmed it with the button (``POST /acciones/{id}/confirmar``), which runs the tool once.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import hashlib
import logging
import os
import socket
import sqlite3
from types import SimpleNamespace
from typing import Any

from hacienda_gpt import settings
from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.eventos.tipos import CORREO_CLASIFICADO, FECHA_UMBRAL, Evento
from hacienda_gpt.modulos.contrato import ContextoModulo, Modulo, ResultadoHerramienta
from hacienda_gpt.tareas.almacen import ConflictoDeVersionError, TareaStore, crear_tablas
from hacienda_gpt.tareas.capacidades import Capacidad, ContextoCapacidad, capacidades_por_defecto
from hacienda_gpt.tareas.modelo import (
    EN_MARCHA,
    EVENTOS_DE_ESPERA,
    PASO_CERRADO,
    Espera,
    EsperaActual,
    EstadoTarea,
    Paso,
    Resultado,
    Tarea,
    ahora,
)

logger = logging.getLogger(__name__)

# The bus timer's "module" for tasks (one per case: the earliest date any of its tasks waits for).
MODULO_TEMPORIZADOR = "tareas"
KIND_AVISO = "modulo:tareas"
PREFIJO_AVISO = "modulo:tareas:"

# A specialist's consultation: (module, question, case) -> (report, evidence references). Raises on failure.
Consultor = Callable[[Modulo, str, CaseState], tuple[str, tuple[str, ...]]]
Evento_ = tuple[str, str | None, dict[str, Any]]


def _numero(nombre: str, defecto: float) -> float:
    try:
        return float(getattr(settings, nombre, defecto))
    except (TypeError, ValueError):
        return defecto


@dataclass(frozen=True)
class Configuracion:
    max_intentos: int = 4
    backoff_base_s: float = 300.0
    backoff_max_s: float = 6 * 3600.0
    arriendo_s: float = 300.0
    # Passes over one task in one call (a plan has at most 30 steps; each pass moves at least one).
    max_vueltas: int = 64

    @classmethod
    def desde_settings(cls) -> Configuracion:
        return cls(
            max_intentos=max(1, int(_numero("TAREAS_MAX_INTENTOS", 4))),
            backoff_base_s=max(0.0, _numero("TAREAS_BACKOFF_SEGUNDOS", 300)),
        )

    def espera(self, intentos: int) -> timedelta:
        return timedelta(seconds=min(self.backoff_max_s, self.backoff_base_s * (2 ** max(0, intentos - 1))))


def clave_aviso(tarea: Tarea, paso: Paso | None = None, sufijo: str = "") -> str:
    partes = [PREFIJO_AVISO + tarea.id]
    if paso is not None:
        partes.append(paso.id)
    if sufijo:
        partes.append(sufijo)
    return ":".join(partes)


def id_accion(tarea: Tarea, paso: Paso) -> str:
    """The pending action of a step's attempt: the same id however many times the step is run."""
    huella = hashlib.sha256(f"{tarea.id}:{paso.id}:{paso.intentos}".encode()).hexdigest()[:32]
    return f"acc_{huella}"


def coincide(espera: Espera, evento: Evento) -> bool:
    """Does ``evento`` satisfy the event wait ``espera`` (type and filter)?"""
    if espera.evento != evento.tipo:
        return False
    if espera.hecho is not None:
        return espera.hecho in evento.hechos
    if espera.categoria is not None and evento.tipo == CORREO_CLASIFICADO:
        categorias = evento.payload.get("categorias") or {}
        return isinstance(categorias, Mapping) and int(categorias.get(espera.categoria) or 0) > 0
    return True


# --------------------------------------------------------------------------- #
# The bus consumer
# --------------------------------------------------------------------------- #


@dataclass
class ConsumidorTareas:
    """Reads the bus log with its own cursor (``tareas``) and writes a signal for every waiting step an event
    satisfies, in the bus's transaction (the cursor moves only with the signals written). A ``fecha.umbral``
    for ``tareas`` wakes every task of the case. Ids only: nothing of the event's payload is copied."""

    nombre: str = "tareas"

    def procesar(self, bus: Any, evento: Evento, conn: sqlite3.Connection) -> None:
        if evento.case_id is None:
            return
        despertar = evento.tipo == FECHA_UMBRAL and evento.modulo == MODULO_TEMPORIZADOR
        if not despertar and evento.tipo not in EVENTOS_DE_ESPERA:
            return
        crear_tablas(conn)
        filas = conn.execute(
            "SELECT payload_json FROM tareas WHERE case_id = ? AND estado IN (?, ?, ?, ?)",
            (evento.case_id, *sorted(EN_MARCHA | {"pausada"})),
        ).fetchall()
        creada = ahora().isoformat()
        for fila in filas:
            tarea = Tarea.model_validate_json(fila[0])
            for paso in tarea.pasos:
                espera = paso.espera_actual
                if paso.estado != "esperando" or espera is None:
                    continue
                if despertar:
                    valida = espera.motivo == "fecha"
                else:
                    valida = (
                        espera.motivo == "evento"
                        and paso.espera is not None
                        and coincide(paso.espera, evento)
                        and evento.created_at >= espera.desde
                    )
                if valida:
                    conn.execute(
                        "INSERT OR IGNORE INTO tareas_senales (tarea_id, paso_id, case_id, evento_id, tipo, creada) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (tarea.id, paso.id, tarea.case_id, evento.id, evento.tipo, creada),
                    )


# --------------------------------------------------------------------------- #
# Default specialist consultation (needs a language model)
# --------------------------------------------------------------------------- #


def consultor_por_defecto(modulo: Modulo, pregunta: str, case: CaseState) -> tuple[str, tuple[str, ...]]:
    """Ask the module's specialist (the same bounded sub-agent as in the chat). Read-only: what it would write
    or act is dropped (a task acts only through its own ``accion`` steps)."""
    from hacienda_gpt.agent.especialistas import Coordinador
    from hacienda_gpt.agent.evidence import EvidenceLedger
    from hacienda_gpt.agent.providers import create_provider

    ledger = EvidenceLedger()
    coordinador = Coordinador.para(create_provider(), ledger, [modulo], lambda *_a, **_k: None)
    if coordinador is None:
        raise RuntimeError(f"el módulo {modulo.clave} no tiene especialista")
    caja = SimpleNamespace(case=case, case_changed=False, ledger=ledger)
    coordinador.vincular(caja)  # type: ignore[arg-type]
    _spec, consultar = coordinador.herramientas()[0]
    salida = consultar({"pregunta": pregunta})
    if salida.is_error:
        raise RuntimeError(salida.content[:300])
    texto = salida.content
    if coordinador.acciones:
        texto += (
            "\n(El especialista propuso una acción; no se ha creado: en una tarea, las acciones son pasos propios.)"
        )
    evidencias = tuple((f.locator or f.fragment_id) for f in ledger.all())[:20]
    return texto, evidencias


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


@dataclass
class _Pasada:
    """What one pass over a task did: history lines and notices to write."""

    eventos: list[Evento_] = field(default_factory=list)
    senales: list[int] = field(default_factory=list)

    def anotar(self, evento: str, paso: Paso | None = None, **detalle: Any) -> None:
        self.eventos.append((evento, paso.id if paso is not None else None, detalle))


class Motor:
    """Advances tasks. One per process (or per worker); every collaborator can be injected (tests)."""

    def __init__(
        self,
        db_path: str,
        *,
        hoy: Callable[[], date] | None = None,
        reloj: Callable[[], datetime] | None = None,
        modulos_de: Callable[[str], Sequence[Modulo]] | None = None,
        capacidades: Mapping[str, Capacidad] | None = None,
        consultor: Consultor | None = None,
        ejecutar_consultas: bool = True,
        config: Configuracion | None = None,
        trabajador: str | None = None,
        reg: Any = None,
        auth: Any = None,
    ) -> None:
        self.db_path = db_path
        self.store = TareaStore(db_path)
        self._hoy = hoy
        self._reloj = reloj
        self._modulos_de = modulos_de
        self.capacidades: dict[str, Capacidad] = dict(capacidades or capacidades_por_defecto())
        self.consultor: Consultor = consultor or consultor_por_defecto
        self.ejecutar_consultas = ejecutar_consultas
        self.config = config or Configuracion.desde_settings()
        self.trabajador = trabajador or f"tareas-{socket.gethostname()}-{os.getpid()}"
        self._reg = reg
        self._auth = auth
        self._casos: Any = None
        self._avisos: Any = None
        self._acciones: Any = None
        self._bus: Any = None

    # ------------------------------------------------------------------ collaborators

    def hoy(self) -> date:
        if self._hoy is not None:
            return self._hoy()
        from hacienda_gpt.proactive.jobs import hoy_en_espana

        return hoy_en_espana()

    def ahora(self) -> datetime:
        return self._reloj() if self._reloj is not None else datetime.now(UTC)

    def registro(self) -> Any:
        if self._reg is not None:
            return self._reg
        from hacienda_gpt.modulos.registro import registro

        return registro()

    def modulos_de(self, case_id: str) -> Sequence[Modulo]:
        if self._modulos_de is not None:
            return self._modulos_de(case_id)
        from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso

        if settings.AUTH_MODE == "required" and self._auth is None:
            from hacienda_gpt.auth.store import AuthStore

            self._auth = AuthStore(self.db_path)
        return modulos_activos_para_caso(case_id, self._auth)

    def casos(self) -> Any:
        if self._casos is None:
            from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore

            self._casos = SQLiteCaseStateStore(self.db_path)
        return self._casos

    def avisos(self) -> Any:
        if self._avisos is None:
            from hacienda_gpt.proactive.store import NotificationStore

            self._avisos = NotificationStore(self.db_path)
        return self._avisos

    def acciones(self) -> Any:
        if self._acciones is None:
            from hacienda_gpt.modulos.acciones import AccionStore

            self._acciones = AccionStore(self.db_path)
        return self._acciones

    def bus(self) -> Any:
        if self._bus is None:
            from hacienda_gpt.eventos.bus import Bus

            self._bus = Bus(
                self.db_path,
                reloj=self._reloj,
                hoy=self._hoy,
                modulos_activos=lambda case_id: list(self.modulos_de(case_id)),
                consumidores=[ConsumidorTareas()],
            )
        return self._bus

    def close(self) -> None:
        for store in (self.store, self._casos, self._avisos, self._acciones, self._bus):
            if store is not None:
                store.close()

    # ------------------------------------------------------------------ driving

    def ciclo(self) -> dict[str, int]:
        """One pass of the worker: due timers → events → every task in progress. Returns what was done."""
        resumen = {"temporizadores": 0, "eventos": 0, "tareas": 0}
        try:
            bus = self.bus()
            resumen["temporizadores"] = bus.disparar_temporizadores(self.hoy())
            resumen["eventos"] = bus.avanzar_consumidores()
        except Exception:  # the bus never stops the tasks: their dates are checked here too
            logger.exception("Task cycle: the event bus failed; tasks are advanced anyway")
        ids = dict.fromkeys([*self.store.tareas_con_senales(), *self.store.ids_en_marcha()])
        for tarea_id in ids:
            try:
                self.avanzar(tarea_id)
                resumen["tareas"] += 1
            except Exception:
                logger.exception("Task %s could not be advanced", tarea_id)
        return resumen

    def avanzar(self, tarea_id: str) -> Tarea | None:
        """Advance one task as far as it can go now. Safe to call at any time, from any process."""
        if not self.store.reclamar(tarea_id, self.trabajador, segundos=self.config.arriendo_s):
            return self.store.get(tarea_id)  # somebody else is on it
        try:
            for _ in range(3):
                tarea = self.store.get(tarea_id)
                if tarea is None:
                    return None
                try:
                    return self._avanzar(tarea)
                except ConflictoDeVersionError:
                    continue
            return self.store.get(tarea_id)
        finally:
            self.store.soltar(tarea_id, self.trabajador)

    def _avanzar(self, original: Tarea) -> Tarea:
        senales = self.store.senales(original.id)
        if original.estado not in EN_MARCHA:
            return original
        case = self.casos().get_case(original.case_id)
        if case is None:  # the case is gone (its trigger erases the task too)
            return original
        tarea = original.model_copy(deep=True)
        pasada = _Pasada()
        modulos = {m.clave: m for m in self.modulos_de(tarea.case_id)}
        self._senales(tarea, senales, pasada)
        for _ in range(self.config.max_vueltas):
            if not self._vuelta(tarea, case, modulos, pasada):
                break
        self._vencimiento(tarea, pasada)
        estado_anterior = original.estado
        tarea.estado = self._estado(tarea)
        if tarea.estado == "completada":
            tarea.terminada = self.ahora()
        tarea.proxima_revision = self._proxima_revision(tarea)
        if tarea.estado != estado_anterior:
            pasada.anotar("estado", estado=tarea.estado, antes=estado_anterior)
        if not pasada.eventos and tarea.proxima_revision == original.proxima_revision and not pasada.senales:
            return original  # nothing moved: no write
        guardada = self.store.guardar(tarea, eventos=pasada.eventos)
        self.store.marcar_senales(pasada.senales)
        self._programar(tarea.case_id)
        return guardada

    # ------------------------------------------------------------------ one pass

    def _senales(self, tarea: Tarea, senales: list[dict[str, Any]], pasada: _Pasada) -> None:
        for senal in senales:
            pasada.senales.append(int(senal["seq"]))
            paso = tarea.paso(str(senal["paso_id"]))
            if paso is None or paso.estado != "esperando" or paso.espera_actual is None:
                continue
            if paso.espera_actual.motivo == "evento":
                self._cerrar(
                    tarea,
                    paso,
                    pasada,
                    Resultado(resumen="Ha llegado lo que esperaba.", evidencias=[f"evento:{senal['evento_id']}"]),
                )

    def _listo(self, tarea: Tarea, paso: Paso) -> bool:
        return all((dep := tarea.paso(d)) is not None and dep.estado in PASO_CERRADO for d in paso.depende_de)

    def _vuelta(self, tarea: Tarea, case: CaseState, modulos: dict[str, Modulo], pasada: _Pasada) -> bool:
        movido = False
        for paso in tarea.pasos:
            if paso.estado == "esperando":
                movido |= self._revisar_espera(tarea, paso, modulos, pasada)
            elif paso.estado == "pendiente" and self._listo(tarea, paso):
                if paso.siguiente_intento is not None and paso.siguiente_intento > self.ahora():
                    continue
                movido |= self._ejecutar(tarea, paso, case, modulos, pasada)
        return movido

    def _ejecutar(self, tarea: Tarea, paso: Paso, case: CaseState, modulos: dict[str, Modulo], pasada: _Pasada) -> bool:
        if paso.iniciado is None:
            paso.iniciado = self.ahora()
            pasada.anotar("paso_iniciado", paso, tipo=paso.tipo)
        try:
            if paso.tipo == "nota":
                self._cerrar(tarea, paso, pasada, Resultado(resumen=paso.descripcion or paso.titulo))
            elif paso.tipo in ("calculo", "accion"):
                return self._herramienta(tarea, paso, case, modulos, pasada)
            elif paso.tipo == "consulta":
                if not self.ejecutar_consultas:
                    return False  # a worker with a language model does it
                modulo = modulos.get(paso.modulo or "")
                if modulo is None:
                    return self._modulo_apagado(tarea, paso, str(paso.modulo), pasada)
                texto, evidencias = self.consultor(modulo, paso.pregunta or paso.titulo, case)
                self._cerrar(tarea, paso, pasada, Resultado(resumen=texto[:4000], evidencias=list(evidencias)[:30]))
            elif paso.tipo == "capacidad":
                return self._capacidad(tarea, paso, case, modulos, pasada)
            elif paso.tipo == "espera":
                return self._empezar_espera(tarea, paso, case, pasada)
            elif paso.tipo == "confirmacion_usuario":
                self._esperar(
                    tarea,
                    paso,
                    pasada,
                    EsperaActual(motivo="usuario", detalle=paso.descripcion or paso.titulo),
                    aviso=(paso.descripcion or paso.titulo) + " Márcalo en «Tareas» o dímelo en el chat.",
                )
        except Exception as exc:
            logger.exception("Step %s of task %s failed", paso.id, tarea.id)
            self._fallo(tarea, paso, pasada, f"Error interno ({type(exc).__name__}).")
        return True

    # ------------------------------------------------------------------ step kinds

    def _herramienta(
        self, tarea: Tarea, paso: Paso, case: CaseState, modulos: dict[str, Modulo], pasada: _Pasada
    ) -> bool:
        encontrada = self.registro().herramienta(paso.herramienta or "")
        if encontrada is None:
            self._fallo(tarea, paso, pasada, f"La herramienta {paso.herramienta} ya no existe.", definitivo=True)
            return True
        modulo_registrado, herramienta = encontrada
        modulo = modulos.get(modulo_registrado.clave)
        if modulo is None:
            return self._modulo_apagado(tarea, paso, modulo_registrado.clave, pasada, nombre=modulo_registrado.nombre)
        if paso.tipo == "accion":
            if herramienta.efecto != "actua":
                self._fallo(tarea, paso, pasada, "Esta herramienta ya no es una acción.", definitivo=True)
                return True
            return self._accion(tarea, paso, modulo, herramienta, pasada)
        if herramienta.efecto == "actua":  # never run an acting tool as a calculation
            self._fallo(tarea, paso, pasada, "Esta herramienta actúa: no se ejecuta como cálculo.", definitivo=True)
            return True
        from hacienda_gpt.modulos.perfil import perfil_de

        contexto = ContextoModulo(perfil=perfil_de(case), hoy=self.hoy(), case_id=case.case_id, registrar=None)
        resultado = herramienta.ejecutar(dict(paso.argumentos), contexto)
        if not isinstance(resultado, ResultadoHerramienta):
            raise TypeError("la herramienta no devolvió un ResultadoHerramienta")
        if resultado.es_error:
            # Deterministic tools fail for a reason the user can fix (a missing figure): ask them.
            self._esperar(
                tarea,
                paso,
                pasada,
                EsperaActual(motivo="usuario", detalle=resultado.contenido[:600], reintentar=True),
                aviso=resultado.contenido[:800] + " Cuando lo hayas resuelto, pulsa «Reintentar» en «Tareas».",
                sufijo=f"dato{paso.intentos}",
            )
            paso.intentos += 1
            return True
        evidencias = [f"modulo://{modulo.clave}/{herramienta.spec.name}", *resultado.citas]
        self._cerrar(tarea, paso, pasada, Resultado(resumen=resultado.contenido[:4000], evidencias=evidencias[:30]))
        return True

    def _accion(self, tarea: Tarea, paso: Paso, modulo: Modulo, herramienta: Any, pasada: _Pasada) -> bool:
        from hacienda_gpt.modulos.acciones import AccionPendiente, describir

        acciones = self.acciones()
        accion_id = id_accion(tarea, paso)
        accion = acciones.get(accion_id)
        if accion is None:
            accion = acciones.add(
                AccionPendiente(
                    id=accion_id,
                    case_id=tarea.case_id,
                    modulo=modulo.clave,
                    herramienta=herramienta.spec.name,
                    argumentos=dict(paso.argumentos),
                    resumen=describir(modulo.nombre, herramienta.spec.description, paso.argumentos),
                )
            )
        if accion.id not in tarea.vinculos.acciones:
            tarea.vinculos.acciones.append(accion.id)
        self._esperar(
            tarea,
            paso,
            pasada,
            EsperaActual(motivo="accion", accion_id=accion.id, detalle=accion.resumen[:600]),
            aviso=f"Acción pendiente de tu confirmación: {accion.resumen} No se hará nada hasta que la confirmes.",
            sufijo=accion.id,
        )
        pasada.anotar("accion_pendiente", paso, accion_id=accion.id)
        return True

    def _capacidad(
        self, tarea: Tarea, paso: Paso, case: CaseState, modulos: dict[str, Modulo], pasada: _Pasada
    ) -> bool:
        from hacienda_gpt.modulos.perfil import perfil_de

        capacidad = self.capacidades.get(paso.capacidad or "")
        if capacidad is None or (paso.operacion or "") not in capacidad.operaciones:
            self._fallo(tarea, paso, pasada, f"La capacidad {paso.capacidad} no está disponible.", definitivo=True)
            return True
        ctx = ContextoCapacidad(
            case=case,
            hoy=self.hoy(),
            modulos_activos=list(modulos.values()),
            clave=f"{tarea.id}:{paso.id}:{paso.intentos}",
            perfil=perfil_de(case),
        )
        resultado = capacidad.ejecutar(paso.operacion or "", dict(paso.parametros), ctx)
        if resultado.es_error:
            self._fallo(tarea, paso, pasada, resultado.resumen[:500] or "La capacidad falló.")
            return True
        if resultado.aviso is not None:
            self._aviso(tarea, paso, pasada, titulo=resultado.aviso[0], cuerpo=resultado.aviso[1], sufijo="capacidad")
        hecho = Resultado(
            resumen=resultado.resumen[:4000], evidencias=list(resultado.evidencias)[:30], borrador=resultado.borrador
        )
        if resultado.estado == "esperando_usuario":
            paso.resultado = hecho
            self._esperar(tarea, paso, pasada, EsperaActual(motivo="usuario", detalle=resultado.resumen[:600]))
        else:
            self._cerrar(tarea, paso, pasada, hecho)
        return True

    def _empezar_espera(self, tarea: Tarea, paso: Paso, case: CaseState, pasada: _Pasada) -> bool:
        espera = paso.espera
        assert espera is not None
        hoy = self.hoy()
        if espera.evento is not None:
            hasta = hoy + timedelta(days=espera.max_dias) if espera.max_dias else None
            self._esperar(tarea, paso, pasada, EsperaActual(motivo="evento", hasta=hasta, detalle=paso.descripcion))
            return True
        nota = ""
        if espera.fecha is not None:
            fecha: date | None = espera.fecha
        elif espera.dias is not None:
            fecha = hoy + timedelta(days=espera.dias)
        elif espera.fecha_de_paso is not None:
            origen = tarea.paso(espera.fecha_de_paso)
            base = origen.resultado.fecha if origen is not None and origen.resultado is not None else None
            fecha = base - timedelta(days=espera.dias_antes) if base is not None else None
            if base is None:
                nota = "No me diste fecha: no hay recordatorio."
        else:
            fecha, nota = self._fecha_de_calendario(espera, case, hoy)
        if fecha is None or fecha <= hoy:
            self._cerrar(tarea, paso, pasada, Resultado(resumen=nota or "La fecha ya ha llegado.", fecha=fecha))
            if fecha is not None:
                self._aviso(tarea, paso, pasada, titulo=paso.titulo, cuerpo=paso.descripcion or paso.titulo)
            return True
        self._esperar(
            tarea,
            paso,
            pasada,
            EsperaActual(motivo="fecha", hasta=fecha, detalle=(nota + " " if nota else "") + paso.titulo),
        )
        return True

    def _fecha_de_calendario(self, espera: Espera, case: CaseState, hoy: date) -> tuple[date | None, str]:
        from hacienda_gpt.tax.calendario import proximos_vencimientos

        hechos = {f.name: f.value for f in case.facts}
        for vencimiento in proximos_vencimientos(hechos, hoy=hoy, dias=730):
            if vencimiento.id.startswith(f"{espera.calendario}:") or vencimiento.id == espera.calendario:
                nota = f"{vencimiento.titulo}: {vencimiento.fecha:%d/%m/%Y} ({vencimiento.referencia})."
                if not vencimiento.verificado:
                    nota += " Fecha sin verificar: " + (vencimiento.nota or "compruébala en la orden de la campaña.")
                return vencimiento.fecha - timedelta(days=espera.dias_antes), nota[:500]
        return None, "El calendario fiscal no tiene ese plazo en los próximos dos años."

    def _revisar_espera(self, tarea: Tarea, paso: Paso, modulos: dict[str, Modulo], pasada: _Pasada) -> bool:
        espera = paso.espera_actual
        if espera is None:
            return False
        hoy = self.hoy()
        if espera.motivo == "fecha" and espera.hasta is not None and hoy >= espera.hasta:
            self._cerrar(tarea, paso, pasada, Resultado(resumen=espera.detalle, fecha=espera.hasta))
            self._aviso(tarea, paso, pasada, titulo=paso.titulo, cuerpo=paso.descripcion or paso.titulo)
            return True
        if espera.motivo == "evento" and espera.hasta is not None and hoy >= espera.hasta:
            detalle = f"No ha llegado lo que esperaba («{paso.titulo}»). Si ya está, márcalo; si no, sáltalo."
            self._esperar(tarea, paso, pasada, EsperaActual(motivo="usuario", detalle=detalle), aviso=detalle)
            return True
        if espera.motivo == "modulo" and paso.tipo in ("calculo", "accion", "consulta"):
            clave = paso.modulo if paso.tipo == "consulta" else self._modulo_de_herramienta(paso)
            if clave in modulos:
                paso.estado, paso.espera_actual = "pendiente", None
                pasada.anotar("paso_reanudado", paso, motivo="modulo_activado")
                return True
        if espera.motivo == "accion" and espera.accion_id:
            accion = self.acciones().get(espera.accion_id)
            if accion is None:
                self._fallo(tarea, paso, pasada, "La acción pendiente ya no existe.", definitivo=True)
                return True
            if accion.estado == "ejecutada":
                self._cerrar(
                    tarea, paso, pasada, Resultado(resumen=(accion.resultado or "")[:4000], evidencias=[accion.id])
                )
                return True
            if accion.estado in ("rechazada", "fallida"):
                motivo = "La rechazaste." if accion.estado == "rechazada" else "La acción falló."
                if paso.opcional and accion.estado == "rechazada":
                    self._cerrar(tarea, paso, pasada, Resultado(resumen=motivo, evidencias=[accion.id]), omitido=True)
                else:
                    self._fallo(tarea, paso, pasada, motivo, definitivo=True)
                return True
        return False

    # ------------------------------------------------------------------ state changes

    def _modulo_de_herramienta(self, paso: Paso) -> str | None:
        encontrada = self.registro().herramienta(paso.herramienta or "")
        return encontrada[0].clave if encontrada is not None else None

    def _modulo_apagado(self, tarea: Tarea, paso: Paso, clave: str, pasada: _Pasada, *, nombre: str = "") -> bool:
        nombre = nombre or clave
        if paso.opcional:
            self._cerrar(
                tarea,
                paso,
                pasada,
                Resultado(resumen=f"Se ha saltado: el módulo {nombre} no está activado."),
                omitido=True,
            )
            return True
        detalle = f"Para seguir, activa el módulo {nombre} en «Módulos» (o salta este paso)."
        self._esperar(tarea, paso, pasada, EsperaActual(motivo="modulo", detalle=detalle), aviso=detalle)
        return True

    def _cerrar(
        self, tarea: Tarea, paso: Paso, pasada: _Pasada, resultado: Resultado, *, omitido: bool = False
    ) -> None:
        paso.estado = "omitido" if omitido else "hecho"
        paso.resultado = resultado
        paso.espera_actual = None
        paso.error = None
        paso.siguiente_intento = None
        paso.terminado = self.ahora()
        pasada.anotar("paso_" + paso.estado, paso, tipo=paso.tipo, evidencias=len(resultado.evidencias))

    def _esperar(
        self,
        tarea: Tarea,
        paso: Paso,
        pasada: _Pasada,
        espera: EsperaActual,
        *,
        aviso: str | None = None,
        sufijo: str = "",
    ) -> None:
        espera = espera.model_copy(update={"desde": self.ahora()})
        if aviso is not None:
            espera = espera.model_copy(
                update={
                    "aviso_clave": self._aviso(tarea, paso, pasada, titulo=paso.titulo, cuerpo=aviso, sufijo=sufijo)
                }
            )
        paso.estado = "esperando"
        paso.espera_actual = espera
        pasada.anotar("paso_esperando", paso, motivo=espera.motivo)

    def _fallo(self, tarea: Tarea, paso: Paso, pasada: _Pasada, mensaje: str, *, definitivo: bool = False) -> None:
        paso.intentos += 1
        paso.error = mensaje[:500]
        if definitivo or paso.intentos >= self.config.max_intentos:
            paso.estado = "fallido"
            paso.siguiente_intento = None
            paso.espera_actual = None
            paso.terminado = self.ahora()
            pasada.anotar("paso_fallido", paso, intentos=paso.intentos)
            self._aviso(
                tarea,
                paso,
                pasada,
                titulo=f"No he podido completar «{paso.titulo}»",
                cuerpo=f"{mensaje} Puedes reintentarlo o cancelar la tarea en «Tareas».",
                gravedad="aviso",
                sufijo=f"fallo{paso.intentos}",
            )
            return
        paso.siguiente_intento = self.ahora() + self.config.espera(paso.intentos)
        pasada.anotar("paso_reintento", paso, intentos=paso.intentos)

    def _aviso(
        self,
        tarea: Tarea,
        paso: Paso | None,
        pasada: _Pasada,
        *,
        titulo: str,
        cuerpo: str,
        gravedad: str = "info",
        sufijo: str = "",
        vence: date | None = None,
    ) -> str:
        """A notice in the user's inbox, keyed by task and step (a repeat writes nothing). Returns its key."""
        from hacienda_gpt.proactive.store import Notification

        clave = clave_aviso(tarea, paso, sufijo)
        try:
            escrito = self.avisos().add(
                Notification(
                    case_id=tarea.case_id,
                    kind=KIND_AVISO,
                    key=clave,
                    title=f"{tarea.titulo}: {titulo}"[:300],
                    body=cuerpo,
                    severity=gravedad,  # type: ignore[arg-type]
                    actions=["Ábrela en «Tareas»."],
                    due_date=vence,
                )
            )
        except Exception:  # a notice never stops a task
            logger.exception("Notice of task %s could not be written", tarea.id)
            return clave
        if clave not in tarea.vinculos.avisos:
            tarea.vinculos.avisos.append(clave)
        if escrito:
            pasada.anotar("aviso", paso)
        return clave

    def _vencimiento(self, tarea: Tarea, pasada: _Pasada) -> None:
        if tarea.vence is not None and self.hoy() > tarea.vence:
            self._aviso(
                tarea,
                None,
                pasada,
                titulo="ha vencido el plazo que tenía",
                cuerpo=f"Esta tarea tenía de plazo hasta el {tarea.vence:%d/%m/%Y}. Sigue, páusala o cancélala.",
                gravedad="aviso",
                sufijo="vence",
            )

    def _estado(self, tarea: Tarea) -> EstadoTarea:
        pasos = tarea.pasos
        if all(p.estado in PASO_CERRADO for p in pasos):
            return "completada"
        if any(p.estado == "fallido" for p in pasos):
            return "fallida"
        esperando = [p.espera_actual.motivo for p in pasos if p.estado == "esperando" and p.espera_actual]
        if any(m in ("usuario", "accion", "modulo") for m in esperando):
            return "esperando_usuario"
        ejecutables = [p for p in pasos if p.estado == "pendiente" and self._listo(tarea, p)]
        if esperando and not ejecutables:
            return "esperando_evento"
        if ejecutables and all(p.siguiente_intento is not None for p in ejecutables):
            return "esperando_evento"  # every runnable step is waiting for its retry
        return "activa"

    def _proxima_revision(self, tarea: Tarea) -> date | None:
        if tarea.estado not in EN_MARCHA:
            return None
        fechas: list[date] = []
        for paso in tarea.pasos:
            if paso.estado == "esperando" and paso.espera_actual is not None:
                if paso.espera_actual.motivo in ("fecha", "evento") and paso.espera_actual.hasta is not None:
                    fechas.append(paso.espera_actual.hasta)
                elif paso.espera_actual.motivo == "accion":
                    fechas.append(self.hoy() + timedelta(days=1))
            elif paso.estado == "pendiente" and paso.siguiente_intento is not None:
                fechas.append(paso.siguiente_intento.date())
        if tarea.vence is not None and tarea.vence >= self.hoy():
            fechas.append(tarea.vence + timedelta(days=1))
        return min(fechas) if fechas else None

    def _programar(self, case_id: str) -> None:
        """The bus timer of the case: the earliest date any of its tasks waits for (or none)."""
        try:
            self.bus().programar(case_id, MODULO_TEMPORIZADOR, self.store.proxima_revision_de_caso(case_id))
        except Exception:  # the cycle checks every task's dates anyway
            logger.exception("Timer of the tasks of a case could not be programmed")


def servicio_tareas(contexto: Any) -> None:
    """The platform worker's loop (``hacienda_gpt.plataforma``): one :meth:`Motor.ciclo` every
    ``TAREAS_INTERVALO_SEGUNDOS`` (60) until ``contexto.parar`` is set. Specialist consultations run here."""
    motor = Motor(contexto.db_path, trabajador=f"{contexto.nombre_proceso}-tareas")
    pausa = max(1.0, _numero("TAREAS_INTERVALO_SEGUNDOS", 60))
    try:
        while not contexto.parar.is_set():
            try:
                resumen = motor.ciclo()
                if resumen["eventos"] or resumen["temporizadores"]:
                    logger.info("Task cycle: %s", resumen)
            except Exception:
                logger.exception("Task worker cycle failed; retrying after the pause")
            contexto.parar.wait(pausa)
    finally:
        motor.close()


def ciclo(db_path: str | None = None, **kwargs: Any) -> dict[str, int]:
    """One worker pass over ``db_path`` (default: the cases' database). For cron and the daily run."""
    motor = Motor(db_path or settings.DECISION_STATE_DB_PATH, **kwargs)
    try:
        return motor.ciclo()
    finally:
        motor.close()


__all__ = [
    "KIND_AVISO",
    "MODULO_TEMPORIZADOR",
    "PREFIJO_AVISO",
    "Configuracion",
    "ConsumidorTareas",
    "Consultor",
    "Motor",
    "ciclo",
    "clave_aviso",
    "coincide",
    "consultor_por_defecto",
    "id_accion",
    "servicio_tareas",
]

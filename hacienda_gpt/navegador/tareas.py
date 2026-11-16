"""Browser tasks: who may start one, how often, what is kept, and what happens with the result.

A task is one run of one script for one case. Its life:

``preparada`` (the chat tool or the UI validated the parameters; no browser yet) → ``en_curso`` (the user
pressed «Empezar»: kill switch, consent, mode and rate limits checked, a thread starts) → ``esperando_login``
(the user logs in, themselves) → ``trabajando`` (the agent's own steps) → one of ``importado`` (the CSV went
to the luz module's importer through the module tool path), ``descargado`` (a file with no destination),
``detenido_antes_de_enviar`` (a procedure filled in and stopped before signing; a pending action records it),
``fallida``, ``cancelada``, ``login_no_completado`` → ``cerrada`` (the user closed it, or
:data:`MINUTOS_TRAS_TERMINAR` passed): its screenshot, summary and parameters are dropped.

What is kept, and where:

* in memory, for the run only: the parameters, the latest screenshot, the summary of what would be sent;
* in the case's audit trail: that a task started and ended, its steps and their outcomes, the parameter NAMES
  and the blocked origins — never a value, a screenshot or a page;
* in the pending actions: the procedure that would be submitted (labels only, no values);
* in the case (through the luz module's own tool, validated by it): the consumption summary;
* on disk, per case: the redacted skeleton of a page where a step failed (``vigilancia.py``).

Credentials never reach this module: the login happens in the browser the user controls, and a handed-over
session goes straight into the browser context and is dropped with it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging
import threading
import time
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from hacienda_gpt import settings
from hacienda_gpt.modulos.acciones import AccionPendiente, AccionStore
from hacienda_gpt.modulos.contrato import ContextoModulo, ResultadoHerramienta
from hacienda_gpt.navegador.ejecutor import Ejecutor, Evento, Modo, ResultadoEjecucion
from hacienda_gpt.navegador.guion import (
    GuionError,
    SpecGuion,
    claves_incorporadas,
    enmascarar_valor,
    guion_activo,
    validar_parametros,
)
from hacienda_gpt.navegador.reparacion import Reparador
from hacienda_gpt.navegador.seguridad import origen_de, origenes_efectivos, sustitutos_validos, texto_de_fichero
from hacienda_gpt.navegador.vigilancia import guardar_diagnostico, observar_ejecucion, proponer_reparacion

logger = logging.getLogger(__name__)

CLAVE_MODULO = "navegador"
HERRAMIENTA_PRESENTAR = "navegador_presentar_tramite"
MINUTOS_TRAS_TERMINAR = 30
MAX_EVENTOS = 200
VENTANA_LIMITE_S = 24 * 3600
TEXTO_CONSENTIMIENTO_VERSION = "navegador-2026-11"
TEXTO_CONSENTIMIENTO = (
    "Vas a dejar que HaciendaGPT maneje un navegador en tu nombre en un sitio oficial. "
    "Tú inicias sesión (en la ventana del navegador o entregando una sesión para esta tarea): HaciendaGPT no "
    "ve, no guarda y no envía a ningún modelo tu contraseña, tu PIN ni tu certificado. "
    "El navegador solo puede visitar los sitios de la tarea y, mientras trabaja el agente, solo consulta: no "
    "envía formularios, no firma, no paga y no acepta nada; los trámites se detienen antes de «Firmar y "
    "enviar» y te enseñan qué se enviaría. Las capturas existen solo durante la tarea; en tu expediente queda "
    "constancia de los pasos, sin los datos que escribiste. Al terminar se cierra el navegador y se descarta la "
    "sesión."
)

EstadoTarea = Literal[
    "preparada",
    "en_curso",
    "esperando_login",
    "trabajando",
    "descargado",
    "importado",
    "detenido_antes_de_enviar",
    "fallida",
    "cancelada",
    "login_no_completado",
    "cerrada",
]
ACTIVOS: frozenset[str] = frozenset({"en_curso", "esperando_login", "trabajando"})
TERMINADOS: frozenset[str] = frozenset(
    {"descargado", "importado", "detenido_antes_de_enviar", "fallida", "cancelada", "login_no_completado"}
)


class TareaError(Exception):
    """A task that cannot be prepared or started. ``codigo`` is the HTTP status the API answers."""

    def __init__(self, mensaje: str, codigo: int = 409) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


@dataclass(frozen=True)
class EventoTarea:
    momento: datetime
    tipo: str
    texto: str
    paso: str | None = None


@dataclass
class Tarea:
    id: str
    case_id: str
    clave: str
    version: int
    titulo: str
    parametros: dict[str, Any]
    personales: frozenset[str]
    user_id: str | None = None
    modo: Modo | None = None
    estado: EstadoTarea = "preparada"
    creada: datetime = field(default_factory=lambda: datetime.now(UTC))
    actualizada: datetime = field(default_factory=lambda: datetime.now(UTC))
    terminada: datetime | None = None
    eventos: list[EventoTarea] = field(default_factory=list)
    pasos: list[dict[str, str]] = field(default_factory=list)
    captura: bytes | None = None
    captura_momento: datetime | None = None
    resumen_envio: list[tuple[str, str]] = field(default_factory=list)
    mensaje: str | None = None
    importacion: str | None = None
    importacion_error: bool = False
    accion_id: str | None = None
    bloqueadas: list[str] = field(default_factory=list)
    cancelar: threading.Event = field(default_factory=threading.Event)
    cerrar: threading.Event = field(default_factory=threading.Event)
    hilo: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def anotar(self, tipo: str, texto: str, paso: str | None = None) -> None:
        with self.lock:
            self.eventos.append(EventoTarea(datetime.now(UTC), tipo, texto, paso))
            del self.eventos[:-MAX_EVENTOS]
            self.actualizada = datetime.now(UTC)

    def parametros_visibles(self) -> dict[str, str]:
        """The parameters as the UI shows them: personal ones masked."""
        out: dict[str, str] = {}
        for nombre, valor in self.parametros.items():
            if valor is None:
                continue
            texto = valor.isoformat() if isinstance(valor, date) else str(valor)
            out[nombre] = enmascarar_valor(texto) if nombre in self.personales else texto
        return out


@dataclass
class Servicios:
    """What a run needs from the app: the stores of the case, and an optional selector repairer."""

    cases: Any  # SQLiteCaseStateStore
    acciones: AccionStore
    auth: Any | None = None  # AuthStore when AUTH_MODE=required
    reparador: Reparador | None = None


# --------------------------------------------------------------------------- #
# Consent and availability
# --------------------------------------------------------------------------- #


def modos_permitidos() -> list[Modo]:
    modos = [m.strip() for m in settings.NAVEGADOR_MODOS.split(",")]
    return [m for m in ("ventana", "sesion") if m in modos]  # type: ignore[misc]


def sustitutos() -> dict[str, str]:
    try:
        return sustitutos_validos(settings.NAVEGADOR_ORIGENES_SUSTITUTOS)
    except ValueError:
        logger.error("NAVEGADOR_ORIGENES_SUSTITUTOS is invalid: ignored")
        return {}


def disponibles() -> list[SpecGuion]:
    """Scripts this service offers. A demonstration script only when its sites are local mock sites."""
    locales = sustitutos()
    out: list[SpecGuion] = []
    for clave in claves_incorporadas():
        try:
            spec = guion_activo(clave)
        except (GuionError, ValueError):
            logger.exception("browser script %s is not usable", clave)
            continue
        if spec.reglas.demostracion and not all((origen_de(o) or o) in locales for o in spec.reglas.origenes.values()):
            continue
        out.append(spec)
    return out


def consentimiento(user_id: str | None, auth: Any | None) -> bool:
    """The user switched the browser agent on THEMSELVES (accounts), or the operator did (no accounts).

    With accounts a default (``MODULOS_ACTIVOS_POR_DEFECTO=todos``) is not consent: only an explicit choice of
    the user, which is recorded with its date, exported and erased with the account.
    """
    from hacienda_gpt.modulos.preferencias import activos_por_defecto
    from hacienda_gpt.modulos.registro import desactivados_por_operador

    if CLAVE_MODULO in desactivados_por_operador():
        return False
    if settings.AUTH_MODE != "required":
        return CLAVE_MODULO in activos_por_defecto()
    if user_id is None or auth is None:
        return False
    eleccion = auth.module_choices(user_id).get(CLAVE_MODULO)
    return eleccion is not None and bool(eleccion.activo)


def _modulo_de_herramienta_activo(nombre: str, case_id: str, auth: Any | None) -> bool:
    from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso
    from hacienda_gpt.modulos.registro import registro

    encontrada = registro().herramienta(nombre)
    if encontrada is None:
        return False
    return encontrada[0].clave in {m.clave for m in modulos_activos_para_caso(case_id, auth)}


# --------------------------------------------------------------------------- #
# The module tool path: hand the downloaded file to another module's tool
# --------------------------------------------------------------------------- #


def ejecutar_herramienta_de_modulo(
    servicios: Servicios, case_id: str, nombre: str, argumentos: dict[str, Any]
) -> ResultadoHerramienta:
    """Run another module's tool for a case exactly as ``POST /modulos/{clave}/herramientas/{nombre}`` does.

    The module must be active for the case; a tool that acts outside the app is refused (it would need the
    user's confirmation); facts are written only through the tool's own ``registrar`` (validated by the
    module); the audit records the argument NAMES only (the CSV is not copied there).
    """
    from hacienda_gpt.modulos.perfil import HechoInvalidoError, perfil_de, registrar_hechos_de_modulo
    from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso
    from hacienda_gpt.modulos.registro import registro
    from hacienda_gpt.proactive.jobs import hoy_en_espana

    encontrada = registro().herramienta(nombre)
    if encontrada is None:
        return ResultadoHerramienta(
            contenido=f"La herramienta {nombre} no existe.", resumen="sin herramienta", es_error=True
        )
    modulo, herramienta = encontrada
    activos = {m.clave for m in modulos_activos_para_caso(case_id, servicios.auth)}
    if modulo.clave not in activos:
        return ResultadoHerramienta(
            contenido=f"El módulo «{modulo.nombre}» no está activado: actívalo para importar el fichero.",
            resumen="módulo desactivado",
            es_error=True,
        )
    if herramienta.efecto == "actua":
        return ResultadoHerramienta(contenido="Esa herramienta necesita tu confirmación.", resumen="", es_error=True)
    case = servicios.cases.get_case(case_id)
    if case is None:
        return ResultadoHerramienta(contenido="El expediente ya no existe.", resumen="sin expediente", es_error=True)
    escrito: dict[str, Any] = {"case": case, "cambiado": False}

    def registrar(valores: Any) -> None:
        escrito["case"] = registrar_hechos_de_modulo(escrito["case"], modulo, valores, fuente=f"modulo:{modulo.clave}")
        escrito["cambiado"] = True

    contexto = ContextoModulo(perfil=perfil_de(case), hoy=hoy_en_espana(), case_id=case_id, registrar=registrar)
    try:
        resultado = herramienta.ejecutar(dict(argumentos), contexto)
    except HechoInvalidoError as exc:
        resultado = ResultadoHerramienta(contenido=f"No se pudo guardar: {exc}", resumen="no guardado", es_error=True)
    except Exception as exc:
        logger.exception("module tool %s failed from a browser task", nombre)
        resultado = ResultadoHerramienta(
            contenido=f"La herramienta falló: {type(exc).__name__}.", resumen="error", es_error=True
        )
    if escrito["cambiado"] and not resultado.es_error:
        servicios.cases.save_case(escrito["case"])
    servicios.cases.append_audit_event(
        case_id,
        {
            "event_type": "module_tool_run_by_navegador",
            "modulo": modulo.clave,
            "herramienta": nombre,
            "argumentos_recibidos": sorted(argumentos),
            "es_error": resultado.es_error,
        },
    )
    return resultado


# --------------------------------------------------------------------------- #
# The manager
# --------------------------------------------------------------------------- #


class _Observador:
    def __init__(self, tarea: Tarea) -> None:
        self.tarea = tarea

    def evento(self, evento: Evento) -> None:
        t = self.tarea
        t.anotar(evento.tipo, evento.texto, evento.paso)
        with t.lock:
            if evento.tipo == "esperando_login":
                t.estado = "esperando_login"
            elif evento.tipo == "agente":
                t.estado = "trabajando"
            elif evento.tipo == "paso" and evento.paso:
                t.pasos.append({"id": evento.paso, "descripcion": evento.texto, "estado": "en_curso"})
            elif evento.tipo == "fallo" and t.pasos:
                t.pasos[-1]["estado"] = "fallo"

    def captura(self, png: bytes) -> None:
        with self.tarea.lock:
            self.tarea.captura = png
            self.tarea.captura_momento = datetime.now(UTC)


class GestorTareas:
    """In-memory registry of the tasks of this process (one service process; see the README)."""

    def __init__(
        self,
        *,
        reloj: Callable[[], float] = time.monotonic,
        opciones_ejecutor: dict[str, Any] | None = None,
        directorio_formatos: str | None = None,
        state_path: str | None = None,
    ) -> None:
        self._tareas: dict[str, Tarea] = {}
        self._inicios: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._reloj = reloj
        # Tests inject headless=True, the Chromium path, a simulated user, no pauses…
        self.opciones_ejecutor = dict(opciones_ejecutor or {})
        self.directorio_formatos = directorio_formatos
        self.state_path = state_path

    # --- lookup ---------------------------------------------------------

    def get(self, tarea_id: str) -> Tarea | None:
        self.purgar()
        return self._tareas.get(tarea_id)

    def de_caso(self, case_id: str) -> list[Tarea]:
        self.purgar()
        return sorted((t for t in self._tareas.values() if t.case_id == case_id), key=lambda t: t.creada, reverse=True)

    def usadas(self, clave_usuario: str) -> int:
        ahora = self._reloj()
        with self._lock:
            cola = self._inicios.get(clave_usuario, deque())
            while cola and ahora - cola[0] > VENTANA_LIMITE_S:
                cola.popleft()
            return len(cola)

    @staticmethod
    def clave_usuario(user_id: str | None, case_id: str) -> str:
        return f"u:{user_id}" if user_id else f"c:{case_id}"

    # --- lifecycle ------------------------------------------------------

    def preparar(
        self, *, case_id: str, clave: str, parametros: dict[str, Any], hoy: date, user_id: str | None = None
    ) -> Tarea:
        """Validate a task (no browser is started). Raises :class:`TareaError` (Spanish)."""
        if not settings.NAVEGADOR_ACTIVO:
            raise TareaError("El agente con navegador está desactivado en este servicio.", 503)
        spec = next((s for s in disponibles() if s.reglas and clave == s.fuente.removeprefix("nav_")), None)
        if spec is None:
            raise TareaError("Esa tarea no existe o no está disponible en este servicio.", 404)
        try:
            valores = validar_parametros(spec.reglas, parametros, hoy=hoy)
        except GuionError as exc:
            raise TareaError(str(exc), 422) from exc
        tarea = Tarea(
            id=f"nav_{uuid4().hex}",
            case_id=case_id,
            clave=clave,
            version=spec.version,
            titulo=spec.reglas.titulo,
            parametros=valores,
            personales=frozenset(p.nombre for p in spec.reglas.parametros if p.personal),
            user_id=user_id,
        )
        tarea.anotar("preparada", "Tarea preparada. Pulsa «Empezar» cuando vayas a iniciar sesión.")
        with self._lock:
            self._tareas[tarea.id] = tarea
        return tarea

    def iniciar(
        self,
        tarea: Tarea,
        *,
        modo: Modo,
        servicios: Servicios,
        user_id: str | None,
        sesion: dict[str, Any] | None = None,
    ) -> Tarea:
        """Start a prepared task in a thread, after the kill switch, consent, mode and rate checks."""
        if not settings.NAVEGADOR_ACTIVO:
            raise TareaError("El agente con navegador está desactivado en este servicio.", 503)
        if modo not in modos_permitidos():
            raise TareaError(f"Este servicio no admite el modo «{modo}».", 422)
        if modo == "sesion" and not sesion:
            raise TareaError("En el modo «sesión» tienes que entregar la sesión para esta tarea.", 422)
        if not consentimiento(user_id, servicios.auth):
            raise TareaError(
                "Activa antes el «Agente con navegador» en Módulos: es tu consentimiento para que actúe por ti.", 403
            )
        spec = guion_activo(tarea.clave)
        destino = spec.reglas.descarga.destino if spec.reglas.descarga else None
        if destino is not None and not _modulo_de_herramienta_activo(destino, tarea.case_id, servicios.auth):
            raise TareaError("Activa antes el módulo que recibe el fichero (Luz) para este expediente.", 409)
        if spec.version != tarea.version:
            raise TareaError("La tarea se preparó con otra versión del guion: prepárala de nuevo.", 409)
        clave_usuario = self.clave_usuario(user_id, tarea.case_id)
        with self._lock:
            if tarea.estado != "preparada":
                raise TareaError("Esta tarea ya se empezó; prepara otra.", 409)
            activas = [t for t in self._tareas.values() if t.estado in ACTIVOS]
            if any(self.clave_usuario(t.user_id, t.case_id) == clave_usuario for t in activas):
                raise TareaError("Ya tienes una tarea del navegador en curso: termina o detén esa antes.", 429)
            if len(activas) >= settings.NAVEGADOR_MAX_SIMULTANEAS:
                raise TareaError("El servicio ya está ejecutando el máximo de tareas a la vez; prueba en un rato.", 429)
            cola = self._inicios.setdefault(clave_usuario, deque())
            ahora = self._reloj()
            while cola and ahora - cola[0] > VENTANA_LIMITE_S:
                cola.popleft()
            if len(cola) >= settings.NAVEGADOR_MAX_TAREAS_DIA:
                raise TareaError(
                    f"Has alcanzado el máximo de {settings.NAVEGADOR_MAX_TAREAS_DIA} tareas del navegador en 24 horas.",
                    429,
                )
            cola.append(ahora)
            tarea.estado = "en_curso"
            tarea.modo = modo
            tarea.user_id = user_id
        tarea.anotar("en_curso", "Abriendo el navegador…")
        servicios.cases.append_audit_event(
            tarea.case_id,
            {
                "event_type": "navegador_tarea_iniciada",
                "tarea_id": tarea.id,
                "guion": tarea.clave,
                "version": tarea.version,
                "modo": modo,
                "parametros_recibidos": sorted(k for k, v in tarea.parametros.items() if v is not None),
                "texto_consentimiento": TEXTO_CONSENTIMIENTO_VERSION,
            },
        )
        hilo = threading.Thread(
            target=self._correr, args=(tarea, spec, servicios, sesion), name=f"navegador-{tarea.id}", daemon=True
        )
        tarea.hilo = hilo
        hilo.start()
        return tarea

    def detener(self, tarea: Tarea) -> Tarea:
        """Stop a running task at the next check (between steps, or while waiting for the login)."""
        tarea.cancelar.set()
        tarea.cerrar.set()
        with tarea.lock:
            if tarea.estado == "preparada":
                tarea.estado = "cancelada"
                tarea.terminada = datetime.now(UTC)
                tarea.parametros = {}
        tarea.anotar("detener", "Has pedido detener la tarea.")
        return tarea

    def cerrar(self, tarea: Tarea) -> Tarea:
        """Discard a task: close its window, drop its screenshot, summary and parameters."""
        self.detener(tarea)
        with tarea.lock:
            tarea.captura = None
            tarea.captura_momento = None
            tarea.resumen_envio = []
            tarea.parametros = {}
            if tarea.estado not in ACTIVOS:
                tarea.estado = "cerrada"
        return tarea

    def purgar(self) -> None:
        """Forget tasks finished more than :data:`MINUTOS_TRAS_TERMINAR` ago (and everything they held)."""
        limite = datetime.now(UTC) - timedelta(minutes=MINUTOS_TRAS_TERMINAR)
        with self._lock:
            viejas = [t.id for t in self._tareas.values() if t.terminada is not None and t.terminada < limite]
            for tarea_id in viejas:
                tarea = self._tareas.pop(tarea_id)
                tarea.captura, tarea.resumen_envio, tarea.parametros = None, [], {}
            preparadas = [
                t.id
                for t in self._tareas.values()
                if t.estado == "preparada" and t.creada < limite - timedelta(hours=1)
            ]
            for tarea_id in preparadas:
                self._tareas.pop(tarea_id, None)

    def esperar(self, tarea: Tarea, timeout: float = 120) -> None:
        """Tests and the CLI: wait for a task's thread to end."""
        if tarea.hilo is not None:
            tarea.hilo.join(timeout)

    # --- the run ----------------------------------------------------------

    def _correr(self, tarea: Tarea, spec: SpecGuion, servicios: Servicios, sesion: dict[str, Any] | None) -> None:
        resultado: ResultadoEjecucion | None = None
        try:
            ejecutor = Ejecutor(
                spec.reglas,
                tarea.parametros,
                origenes=origenes_efectivos(spec.reglas, sustitutos()),
                modo=tarea.modo or "ventana",
                sesion=sesion,
                observador=_Observador(tarea),
                cancelar=tarea.cancelar,
                cerrar=tarea.cerrar,
                reparador=servicios.reparador,
                activo=lambda: bool(settings.NAVEGADOR_ACTIVO),
                **self.opciones_ejecutor,
            )
            sesion = None
            resultado = ejecutor.ejecutar()
            self._terminar(tarea, spec, servicios, resultado)
        except Exception as exc:  # the thread must always leave the task in a final state
            logger.exception("browser task %s crashed", tarea.id)
            with tarea.lock:
                tarea.estado = "fallida"
                tarea.mensaje = f"La tarea ha fallado: {type(exc).__name__}. No se ha enviado nada."
        finally:
            with tarea.lock:
                if tarea.estado in ACTIVOS:
                    tarea.estado = "fallida"
                tarea.terminada = datetime.now(UTC)
                tarea.parametros = {}
            self._auditar_fin(tarea, servicios, resultado)

    def _terminar(self, tarea: Tarea, spec: SpecGuion, servicios: Servicios, r: ResultadoEjecucion) -> None:
        estados: dict[str, EstadoTarea] = {
            "descargado": "descargado",
            "detenido_antes_de_enviar": "detenido_antes_de_enviar",
            "fallido": "fallida",
            "cancelado": "cancelada",
            "login_no_completado": "login_no_completado",
        }
        with tarea.lock:
            tarea.estado = estados[r.estado]
            tarea.mensaje = r.mensaje
            tarea.bloqueadas = r.bloqueadas
            tarea.resumen_envio = list(r.resumen_envio)
            if r.captura is not None:
                tarea.captura, tarea.captura_momento = r.captura, datetime.now(UTC)
            hechos = {p.id: p.estado for p in r.pasos}
            for paso in tarea.pasos:
                paso["estado"] = hechos.get(paso["id"], paso["estado"])
        try:
            observar_ejecucion(
                tarea.clave, tarea.version, r, state_path=self.state_path, directorio=self.directorio_formatos
            )
            if r.reparaciones:
                propuesta = proponer_reparacion(tarea.clave, spec, r.reparaciones, directorio=self.directorio_formatos)
                if propuesta is not None:
                    tarea.anotar("reparacion", f"Selector reparado: queda una propuesta por revisar ({propuesta.id}).")
            if r.esqueleto_fallo is not None:
                fallo = r.pasos[-1] if r.pasos else None
                guardar_diagnostico(
                    tarea.case_id,
                    tarea.id,
                    {
                        "guion": tarea.clave,
                        "version": tarea.version,
                        "paso": fallo.id if fallo else None,
                        "estado": fallo.estado if fallo else None,
                        "detalle": fallo.detalle if fallo else None,
                        "esqueleto_redactado": r.esqueleto_fallo,
                    },
                )
        except Exception:
            logger.exception("could not record the format observation of browser task %s", tarea.id)

        if r.estado == "descargado" and r.descarga is not None:
            destino = spec.reglas.descarga.destino if spec.reglas.descarga else None
            if destino is not None:
                try:
                    texto = texto_de_fichero(r.descarga)
                except ValueError as exc:
                    resultado = ResultadoHerramienta(contenido=f"Descarga no válida: {exc}.", resumen="", es_error=True)
                else:
                    resultado = ejecutar_herramienta_de_modulo(servicios, tarea.case_id, destino, {"csv": texto})
                    del texto
                with tarea.lock:
                    tarea.importacion = resultado.contenido
                    tarea.importacion_error = resultado.es_error
                    tarea.estado = "fallida" if resultado.es_error else "importado"
                tarea.anotar(
                    "importacion",
                    ("No se ha podido importar: " if resultado.es_error else "Importado en el módulo de luz. ")
                    + resultado.contenido.splitlines()[0][:200],
                )
            r.descarga = None
        if r.estado == "detenido_antes_de_enviar":
            accion = self._accion_pendiente(tarea, spec, r, servicios.acciones)
            with tarea.lock:
                tarea.accion_id = accion.id

    def _accion_pendiente(
        self, tarea: Tarea, spec: SpecGuion, r: ResultadoEjecucion, acciones: AccionStore
    ) -> AccionPendiente:
        """Record what WOULD be submitted as a pending action (labels only). Confirming it submits nothing."""
        etiquetas = [etiqueta for etiqueta, _valor in r.resumen_envio]
        sitio = urlsplit(next(iter(spec.reglas.origenes.values()))).hostname or "la sede"
        resumen = (
            f"Agente con navegador — presentar «{spec.reglas.titulo}» en {sitio}. "
            f"Se enviarían {len(etiquetas)} datos ({', '.join(etiquetas) or 'sin resumen'}). "
            "En esta versión HaciendaGPT NO firma, NO presenta y NO paga: revisa la captura y, si estás de acuerdo, "
            "preséntalo tú en la sede."
        )
        return acciones.add(
            AccionPendiente(
                case_id=tarea.case_id,
                modulo=CLAVE_MODULO,
                herramienta=HERRAMIENTA_PRESENTAR,
                argumentos={"tarea_id": tarea.id, "guion": tarea.clave, "version": tarea.version, "campos": etiquetas},
                resumen=resumen,
            )
        )

    def _auditar_fin(self, tarea: Tarea, servicios: Servicios, r: ResultadoEjecucion | None) -> None:
        try:
            servicios.cases.append_audit_event(
                tarea.case_id,
                {
                    "event_type": "navegador_tarea_terminada",
                    "tarea_id": tarea.id,
                    "guion": tarea.clave,
                    "version": tarea.version,
                    "estado": tarea.estado,
                    "pasos": [{"id": p.id, "accion": p.accion, "estado": p.estado} for p in (r.pasos if r else [])],
                    "origenes_bloqueados": sorted(set(r.bloqueadas)) if r else [],
                    "selectores_reparados": sorted(r.reparaciones) if r else [],
                    "accion_id": tarea.accion_id,
                },
            )
        except Exception:
            logger.exception("could not audit the end of browser task %s", tarea.id)


_gestor: list[GestorTareas] = []
_gestor_lock = threading.Lock()


def gestor() -> GestorTareas:
    """The process-wide task manager."""
    with _gestor_lock:
        if not _gestor:
            _gestor.append(GestorTareas())
        return _gestor[0]


__all__ = [
    "CLAVE_MODULO",
    "HERRAMIENTA_PRESENTAR",
    "TEXTO_CONSENTIMIENTO",
    "TEXTO_CONSENTIMIENTO_VERSION",
    "GestorTareas",
    "Servicios",
    "Tarea",
    "TareaError",
    "consentimiento",
    "disponibles",
    "ejecutar_herramienta_de_modulo",
    "gestor",
    "modos_permitidos",
]

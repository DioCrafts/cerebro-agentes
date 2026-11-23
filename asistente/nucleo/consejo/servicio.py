"""The council as a product: convene with an estimate, confirm, run in the background, store, deliver the ruling.

* :meth:`ServicioConsejo.convocar` creates a session in ``pendiente_confirmacion`` with the cost and time estimate
  (``estimacion``): NOTHING runs and no model is called until the user confirms. The brain's tool
  ``convocar_consejo`` and ``POST /cases/{id}/consejo`` both end here. The same open question of a case is never
  convened twice (the existing session is returned).
* :meth:`confirmar` / :meth:`rechazar` / :meth:`cancelar` are single conditional transitions (``almacen``): a double
  «Confirmar» queues the session once and starts it once. Only the case's owner reaches them (the API checks the
  case; the service also checks the session's owner).
* :meth:`ejecutar_pendientes` is the job (and :meth:`_trabajar_caso` the immediate background start in a
  single-process deployment, ``CONSEJO_EN_SEGUNDO_PLANO``, in a thread under ``contextvars.copy_context``): it claims
  queued sessions with a lease (at most one running per case), and for each one, inside the owner's usage context
  (``contexto_consumo``):

  1. builds the dossier (``expediente.instruir``: the specialists' figures, pseudonymised, the user's preference
     sealed);
  2. runs the deliberation engine (``consejo.deliberacion.Deliberacion``, agent F) with a provider per purpose;
  3. runs the brain's figures gate on the ruling against the dossier's figures (when the engine has not), and the
     verifier on the claims it cites;
  4. stores the result — only while it still holds the lease (a session cancelled meanwhile keeps «cancelada») —
     and writes ONE notification (kind ``modulo:consejo``, severity ``info``, never ``urgente``): «Dictamen del
     consejo listo», or an honest «El consejo no ha podido terminar» saying why.

  Every call of the run is metered by a per-session store (:class:`_ConsumoDeSesion`) and checked BEFORE it starts
  by a per-session budget (:class:`_PresupuestoDeSesion`): the daily limits of the app, the session's own call cap
  (``CONSEJO_MAX_LLAMADAS``) and the hard cap the user accepted (``tope_usd``). Past it no further call is made:
  the engine (which absorbs the stop) rules with what it has — a partial ruling written by code, its first warning
  «Dictamen parcial: …», shown as such and notified as «Dictamen parcial del consejo» — unless the stop came before
  round 1 gave the council its minimum of proposals, then the session is ``fallida``; a stop during the dossier fails
  it too. Nothing is invented to fill the gap. A cancellation stops the next call the same way, and whatever the
  engine returns after it is discarded (the row says «cancelada»).

The job never raises: a failing session is marked ``fallida`` (with a notification), a crash mid-session leaves an
expired lease that the next run takes over (``CONSEJO_MAX_INTENTOS``), and housekeeping (expired convocations,
retention) runs on every pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import contextvars
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import inspect
import logging
import re
import threading
from typing import TYPE_CHECKING, Any, Literal, Protocol

from asistente.nucleo.cerebro.contrato import (
    CLAVE_FISCAL,
    EVENTO_ESPECIALISTA,
    Especialista,
    InformeEspecialista,
    ProveedorMemoria,
)
from asistente.nucleo.consejo.almacen import (
    EN_MARCHA,
    AlmacenConsejo,
    ExtraSesion,
    OrigenSesion,
    PasoProgreso,
    Registro,
    almacen,
    huella_pregunta,
    nuevo_id,
)
from asistente.nucleo.consejo.contrato import (
    EVENTO_FASE,
    EVENTO_MIEMBRO,
    MIN_MIEMBROS,
    CosteSesion,
    Dictamen,
    MotorDeliberacion,
    SesionConsejo,
)
from asistente.nucleo.consejo.estimacion import (
    EXPEDIENTE_BASE_TOKENS,
    Estimacion,
    estimar,
    formatear_usd,
    tokens_aprox,
)
from asistente.plataforma import settings
from asistente.plataforma.ops.consumo import ConsumoStore, Presupuesto, PresupuestoAgotadoError, Usage

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.agent.providers.base import LLMProvider
    from asistente.nucleo.consejo.deteccion import Deteccion
    from asistente.nucleo.consejo.expediente import ResultadoInstruccion
    from asistente.nucleo.decision.schemas import CaseState
    from asistente.nucleo.modulos.contrato import Modulo

logger = logging.getLogger(__name__)

KIND_CONSEJO = "modulo:consejo"
TITULO_LISTO = "Dictamen del consejo listo"
TITULO_PARCIAL = "Dictamen parcial del consejo"
# The engine's partial ruling (written by code when a stop — the cap, the budget — ended the session early) says so in
# its first warning, with this prefix.
PREFIJO_PARCIAL = "Dictamen parcial:"
TITULO_FALLIDO = "El consejo no ha podido terminar"
MAX_PREGUNTA = 4000
MIN_PREGUNTA = 10
# The phase each engine event moves the session to (its stored state).
_ESTADO_DE_FASE: dict[str, Literal["instruyendo", "deliberando", "sintetizando"]] = {
    "instruccion": "instruyendo",
    "propuestas": "deliberando",
    "criticas": "deliberando",
    "revision": "deliberando",
    "sintesis": "sintetizando",
}
CodigoError = Literal["no_encontrada", "estado", "desactivado", "invalida", "tope"]


class ConsejoError(Exception):
    """A request the council cannot honour. ``mensaje`` is Spanish, for the user; ``codigo`` maps to HTTP."""

    def __init__(self, mensaje: str, *, codigo: CodigoError) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo: CodigoError = codigo


def activo() -> bool:
    return bool(getattr(settings, "CONSEJO_ACTIVO", True))


# --------------------------------------------------------------------------------------------------------------
# Metering and the hard cap of one run
# --------------------------------------------------------------------------------------------------------------


class ControlSesion:
    """The cost, the cap and the cancellation of ONE run of a session, shared by every call it makes (threads
    included). :meth:`comprobar` runs before each call (through the session's budget)."""

    def __init__(
        self, *, tope_usd: Decimal, max_llamadas: int | None = None, inicial: CosteSesion | None = None
    ) -> None:
        self._lock = threading.Lock()
        # Seeded with what earlier runs of the same session already spent: the cap is per session, not per attempt.
        self.inicial = inicial.model_copy() if inicial is not None else CosteSesion()
        self._coste = self.inicial.model_copy()
        self.tope_usd = tope_usd
        self.max_llamadas = max_llamadas
        self.cancelada = threading.Event()
        self.motivo: Literal["tope", "llamadas", "cancelada"] | None = None

    def anotar(self, usage: Usage, coste: Decimal | None) -> None:
        with self._lock:
            self._coste = CosteSesion(
                llamadas=self._coste.llamadas + 1,
                usd=self._coste.usd + (coste or Decimal(0)),
                tokens_entrada=self._coste.tokens_entrada
                + usage.input_tokens
                + usage.cache_read_tokens
                + usage.cache_write_tokens,
                tokens_salida=self._coste.tokens_salida + usage.output_tokens,
            )

    def coste(self) -> CosteSesion:
        with self._lock:
            return self._coste.model_copy()

    def comprobar(self) -> None:
        if self.cancelada.is_set():
            self.motivo = "cancelada"
            raise PresupuestoAgotadoError("La sesión del consejo se ha cancelado.", limite="llamadas_por_pregunta")
        with self._lock:
            gastado, llamadas = self._coste.usd, self._coste.llamadas
        if gastado >= self.tope_usd:
            self.motivo = "tope"
            raise PresupuestoAgotadoError(
                f"La sesión del consejo ha llegado a su tope de coste ({formatear_usd(self.tope_usd)}).",
                limite="llamadas_por_pregunta",
            )
        if self.max_llamadas is not None and llamadas >= self.max_llamadas:
            self.motivo = "llamadas"
            raise PresupuestoAgotadoError(
                f"La sesión del consejo ha llegado a su máximo de {self.max_llamadas} llamadas al modelo.",
                limite="llamadas_por_pregunta",
            )


class _ConsumoDeSesion(ConsumoStore):
    """The usage log as always, plus the session's own running total."""

    def __init__(self, db_path: str, control: ControlSesion) -> None:
        super().__init__(db_path)
        self._control = control

    def registrar(
        self,
        usage: Usage,
        *,
        user_id: str | None = None,
        case_id: str | None = None,
        ts: datetime | None = None,
    ) -> Decimal | None:
        coste = super().registrar(usage, user_id=user_id, case_id=case_id, ts=ts)
        if coste is None:
            # An unpriced model counts at the dearest price of the table against the hard cap.
            from asistente.plataforma.ops.consumo import coste_prudente

            try:
                self._control.anotar(usage, coste_prudente(usage))
            except Exception:  # a broken price table never breaks the session
                logger.warning("could not price a council call for the cap", exc_info=True)
                self._control.anotar(usage, None)
            return coste
        self._control.anotar(usage, coste)
        return coste


@dataclass(frozen=True)
class _PresupuestoDeSesion(Presupuesto):
    """The app's daily limits, plus the session's cap and cancellation, checked before every call."""

    control: ControlSesion | None = None

    def comprobar(
        self,
        user_id: str | None,
        case_id: str | None,
        *,
        llamadas_en_pregunta: int = 0,
        ahora: datetime | None = None,
    ) -> None:
        if self.control is not None:
            self.control.comprobar()
        super().comprobar(user_id, case_id, llamadas_en_pregunta=0, ahora=ahora)


# --------------------------------------------------------------------------------------------------------------
# What one run needs (injectable: tests pass scripted providers, fake specialists and a stand-in engine)
# --------------------------------------------------------------------------------------------------------------


@dataclass
class EntornoSesion:
    proveedor_para: Callable[[str], LLMProvider]
    motor: MotorDeliberacion
    especialistas: Mapping[str, Especialista] | Callable[[str], Mapping[str, Especialista]] | None = None
    memoria: ProveedorMemoria | None = None
    # Called once the run ends (close connections).
    cerrar: Callable[[], None] = field(default=lambda: None)


class FabricaConsejo(Protocol):
    def entorno(
        self, registro: Registro, *, case: CaseState, modulos: Sequence[Modulo], control: ControlSesion
    ) -> EntornoSesion: ...


def nombres_de_miembros() -> list[str]:
    """The seats' names for the convocation card (the engine's configured roles)."""
    try:
        from asistente.nucleo.consejo.miembros import roles_configurados

        return [r.nombre for r in roles_configurados()]
    except Exception:  # the engine is not there yet: the card shows the count only
        logger.debug("council roles unavailable", exc_info=True)
        return []


_buscador_lock = threading.Lock()
_buscador: Any = None


def _buscador_del_corpus() -> Any:
    """The corpus searcher (FAISS), loaded once per process; raises when the index is not available."""
    global _buscador
    with _buscador_lock:
        if _buscador is None:
            from asistente.nucleo.agent.search import FaissCorpusSearcher

            _buscador = FaissCorpusSearcher.from_settings()
        return _buscador


class FabricaPorDefecto:
    """Production wiring: the configured providers (the members' families from ``CONSEJO_FAMILIAS``), metered under
    each purpose by the session's store and budget; the household specialists of the case's active modules plus
    the tax advisor (when the corpus index is available); the conversation memory; the engine of agent F.

    ``db_path``: where the usage log is (the service's database; default ``DECISION_STATE_DB_PATH``)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path

    def entorno(
        self, registro: Registro, *, case: CaseState, modulos: Sequence[Modulo], control: ControlSesion
    ) -> EntornoSesion:
        from asistente.nucleo.agent.providers import create_provider
        from asistente.nucleo.evaluacion.variantes import argumentos_proveedor
        from asistente.plataforma.ops.consumo import MeteredProvider

        store = _ConsumoDeSesion(self.db_path or str(settings.DECISION_STATE_DB_PATH), control)
        base = Presupuesto.desde_settings(store)
        presupuesto = _PresupuestoDeSesion(
            store=store,
            diario_usd=base.diario_usd,
            diario_por_usuario_usd=base.diario_por_usuario_usd,
            max_llamadas_por_pregunta=None,
            control=control,
        )
        principal = create_provider(**argumentos_proveedor())
        familias: Any = None
        try:
            from asistente.nucleo.consejo.miembros import FabricaProveedores, roles_configurados

            familias = FabricaProveedores(roles_configurados(), medir=False)
        except Exception:  # without the engine's seats every purpose runs on the configured provider
            logger.debug("council seat families unavailable", exc_info=True)
        lock = threading.Lock()
        cache: dict[str, LLMProvider] = {}

        def proveedor_para(proposito: str) -> LLMProvider:
            with lock:
                if proposito not in cache:
                    asiento = familias.asiento(proposito) if familias is not None else None
                    inner = familias(proposito) if asiento is not None else principal
                    cache[proposito] = MeteredProvider(
                        inner, store, purpose=proposito, presupuesto=presupuesto, compartido=True
                    )
                return cache[proposito]

        def especialistas(consulta: str) -> Mapping[str, Especialista]:
            return especialistas_de_produccion(consulta, modulos, proveedor_para)

        from asistente.nucleo.cerebro.construir import servicio_de_memoria

        return EntornoSesion(
            proveedor_para=proveedor_para,
            motor=motor_por_defecto(proveedor_para),
            especialistas=especialistas,
            memoria=servicio_de_memoria(),
            cerrar=store.close,
        )


def especialistas_de_produccion(
    consulta: str, modulos: Sequence[Modulo], proveedor_para: Callable[[str], LLMProvider]
) -> dict[str, Especialista]:
    """The household specialists of the active modules, plus the tax advisor with this run's providers (its query
    is the NEUTRAL question, so it never reads the user's leaning)."""
    from asistente.nucleo.cerebro.construir import especialistas_del_hogar
    from asistente.nucleo.modulos.ganchos import tiene_especialista

    hogar = [m for m in modulos if getattr(m, "clave", "") != CLAVE_FISCAL and tiene_especialista(m)]
    salida: dict[str, Especialista] = dict(especialistas_del_hogar(hogar)) if hogar else {}
    if any(getattr(m, "clave", "") == CLAVE_FISCAL for m in modulos) or not modulos:
        try:
            from asistente.nucleo.agent.agent import TaxAgent
            from asistente.nucleo.agent.verifier import Verifier
            from asistente.nucleo.cerebro.fiscal import EspecialistaFiscal

            asesor = TaxAgent(
                provider=proveedor_para("agente"),
                searcher=_buscador_del_corpus(),
                verifier=Verifier(proveedor_para("verificador")),
            )
            salida[CLAVE_FISCAL] = EspecialistaFiscal(
                asesor, consulta=consulta, modulos=list(modulos) or None, otros={c: e.nombre for c, e in salida.items()}
            )
        except Exception:
            logger.warning("tax specialist unavailable for the council's dossier", exc_info=True)
    return salida


def motor_por_defecto(proveedor_para: Callable[[str], LLMProvider]) -> MotorDeliberacion:
    """Agent F's engine with this run's providers."""
    from asistente.nucleo.consejo import deliberacion

    try:
        motor: MotorDeliberacion = deliberacion.Deliberacion(proveedor_para=proveedor_para)
    except TypeError:
        motor = deliberacion.construir_motor()
    return motor


# --------------------------------------------------------------------------------------------------------------
# Words for the user
# --------------------------------------------------------------------------------------------------------------


def nota_convocatoria(estimacion_usd: Decimal, minutos: int) -> str:
    """The sentence the brain includes in its answer when it proposes the council."""
    return (
        f"Te propongo consultarlo con el consejo: coste estimado {formatear_usd(estimacion_usd)}, "
        f"unos {minutos} minuto{'s' if minutos != 1 else ''}. Confírmalo en la tarjeta."
    )


def confianza_en_palabras(confianza: float) -> str:
    if confianza >= 0.75:
        return "confianza alta"
    if confianza >= 0.5:
        return "confianza media"
    if confianza >= 0.3:
        return "confianza baja"
    return "confianza muy baja"


CONSENSO_EN_PALABRAS = {
    "unanime": "todos los miembros coinciden",
    "mayoria": "la mayoría coincide",
    "dividido": "el consejo está dividido",
    "insuficiente": "no hay base suficiente para recomendar",
}


def recomendacion_en_palabras(dictamen: Dictamen, sesion: SesionConsejo) -> str:
    if dictamen.recomendacion == "depende":
        return "Depende" + (f": {dictamen.condicion}" if dictamen.condicion else "")
    opciones = {o.id: o.descripcion for o in (sesion.expediente.opciones if sesion.expediente else [])}
    return opciones.get(dictamen.recomendacion, dictamen.recomendacion)


def _recorte(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------------------------------------------


@dataclass
class ResultadoConfirmacion:
    sesion: SesionConsejo
    # This call made the transition (False: it was already confirmed, or not pending any more).
    confirmada_ahora: bool


class ServicioConsejo:
    """The council's sessions of one database. Thread-safe; build it with :func:`servicio_para`."""

    def __init__(
        self,
        db_path: str,
        *,
        fabrica: FabricaConsejo | None = None,
        en_segundo_plano: bool | None = None,
        hoy: Callable[[], date] | None = None,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.almacen: AlmacenConsejo = almacen(self.db_path)
        self._fabrica: FabricaConsejo = fabrica or FabricaPorDefecto(self.db_path)
        self._en_segundo_plano = en_segundo_plano
        self._hoy = hoy
        self._reloj = reloj or (lambda: datetime.now(UTC))
        self._hilos: list[threading.Thread] = []

    # -- settings ------------------------------------------------------------------------------------------------

    @property
    def en_segundo_plano(self) -> bool:
        if self._en_segundo_plano is not None:
            return self._en_segundo_plano
        return bool(getattr(settings, "CONSEJO_EN_SEGUNDO_PLANO", True))

    @staticmethod
    def _arriendo_s() -> int:
        return max(60, int(getattr(settings, "CONSEJO_ARRIENDO_S", 900)))

    @staticmethod
    def _max_intentos() -> int:
        return max(1, int(getattr(settings, "CONSEJO_MAX_INTENTOS", 2)))

    def _dia(self) -> date:
        if self._hoy is not None:
            return self._hoy()
        from asistente.nucleo.proactive.jobs import hoy_en_espana

        return hoy_en_espana()

    # -- convening ------------------------------------------------------------------------------------------------

    def estimar(self, pregunta: str, *, modulos: Sequence[Modulo] = (), case: CaseState | None = None) -> Estimacion:
        """The estimate of convening the council on ``pregunta`` (no model call)."""
        previstos = especialistas_previstos(pregunta, modulos, case)
        miembros = nombres_de_miembros()
        tokens = EXPEDIENTE_BASE_TOKENS + tokens_aprox(pregunta) + 700 * len(previstos)
        return estimar(len(miembros) or 4, tokens, especialistas=previstos)

    def convocar(
        self,
        *,
        case: CaseState,
        pregunta: str,
        user_id: str | None = None,
        origen: OrigenSesion = "usuario",
        motivo: str = "",
        opciones: Sequence[str] = (),
        preferencia: str | None = None,
        historial: Sequence[Mapping[str, Any]] = (),
        modulos: Sequence[Modulo] = (),
        deteccion: Deteccion | None = None,
    ) -> SesionConsejo:
        """A new session waiting for the user's confirmation (or the open one for the same question). No model call."""
        if not activo():
            raise ConsejoError("El consejo no está disponible en este servidor.", codigo="desactivado")
        texto = " ".join((pregunta or "").split())
        if len(texto) < MIN_PREGUNTA:
            raise ConsejoError("Escribe la decisión que quieres que estudie el consejo.", codigo="invalida")
        if len(texto) > MAX_PREGUNTA:
            raise ConsejoError("La pregunta es demasiado larga para el consejo.", codigo="invalida")
        huella = huella_pregunta(case.case_id, texto)
        existente = self.almacen.abierta_igual(case.case_id, huella)
        if existente is not None:
            return existente.sesion
        estimacion = self.estimar(texto, modulos=modulos, case=case)
        if estimacion.excede_maximo:
            from asistente.nucleo.consejo.estimacion import tope_maximo

            raise ConsejoError(
                f"Estudiarlo en consejo costaría unos {formatear_usd(estimacion.usd)}, más que el máximo permitido "
                f"({formatear_usd(tope_maximo())}).",
                codigo="tope",
            )
        from asistente.nucleo.recuerdos.privacidad import limpiar

        momento = self._reloj()
        dueno = user_id or case.user_id or None
        sesion = SesionConsejo(
            id=nuevo_id(),
            case_id=case.case_id,
            user_id=dueno,
            estado="pendiente_confirmacion",
            pregunta_usuario=texto,
            creada=momento,
            actualizada=momento,
            estimacion_usd=estimacion.usd,
            tope_usd=estimacion.tope_usd,
        )
        motivos = motivo or ("; ".join(deteccion.motivos) if deteccion is not None else "")
        previos = [
            limpiar(str(m.get("content") or ""))[:500]
            for m in historial
            if isinstance(m, Mapping) and m.get("role") == "user" and isinstance(m.get("content"), str)
        ][-4:]
        extra = ExtraSesion(
            origen=origen,
            motivo=_recorte(limpiar(motivos), 600),
            opciones_sugeridas=[_recorte(limpiar(o), 400) for o in opciones if isinstance(o, str) and o.strip()][:6],
            minutos_estimados=estimacion.minutos,
            llamadas_estimadas=estimacion.llamadas,
            miembros=nombres_de_miembros(),
            desglose=dict(estimacion.desglose),
            precios_conocidos=estimacion.precios_conocidos,
            preferencia=_recorte(limpiar(preferencia), 600) if preferencia and preferencia.strip() else None,
            mensajes_previos=[p for p in previos if p and p != texto],
        )
        return self.almacen.crear(sesion, extra, huella=huella).sesion

    def _propia(self, sesion_id: str, case_id: str, user_id: str | None) -> Registro:
        registro = self.almacen.obtener(sesion_id, case_id=case_id)
        if registro is None or (user_id and registro.sesion.user_id and registro.sesion.user_id != user_id):
            raise ConsejoError("No existe esa sesión del consejo.", codigo="no_encontrada")
        return registro

    def confirmar(self, sesion_id: str, *, case_id: str, user_id: str | None = None) -> ResultadoConfirmacion:
        """The user accepts the estimate: the session is queued (once) and, in single-process mode, started now."""
        registro = self._propia(sesion_id, case_id, user_id)
        if not activo():
            raise ConsejoError("El consejo no está disponible en este servidor.", codigo="desactivado")
        extra = registro.extra.model_copy(update={"confirmada": self._reloj()})
        hecho = self.almacen.transicion(
            sesion_id, case_id=case_id, desde=("pendiente_confirmacion",), a="en_cola", extra=extra
        )
        actual = self._propia(sesion_id, case_id, user_id)
        if not hecho and actual.sesion.estado in ("rechazada", "cancelada"):
            raise ConsejoError("Esta propuesta ya no está pendiente (se rechazó o caducó).", codigo="estado")
        if hecho:
            self._lanzar(case_id)
        return ResultadoConfirmacion(sesion=actual.sesion, confirmada_ahora=hecho)

    def rechazar(self, sesion_id: str, *, case_id: str, user_id: str | None = None) -> SesionConsejo:
        registro = self._propia(sesion_id, case_id, user_id)
        if registro.sesion.estado == "rechazada":
            return registro.sesion
        if not self.almacen.transicion(sesion_id, case_id=case_id, desde=("pendiente_confirmacion",), a="rechazada"):
            raise ConsejoError("Solo se puede rechazar una propuesta pendiente de confirmar.", codigo="estado")
        return self._propia(sesion_id, case_id, user_id).sesion

    def cancelar(self, sesion_id: str, *, case_id: str, user_id: str | None = None) -> SesionConsejo:
        """Stop a pending, queued or running session. A running one stops at its next model call; whatever it would
        have produced is discarded (its lease is gone)."""
        registro = self._propia(sesion_id, case_id, user_id)
        if registro.sesion.estado == "cancelada":
            return registro.sesion
        hecho = self.almacen.transicion(
            sesion_id,
            case_id=case_id,
            desde=("pendiente_confirmacion", "en_cola", *EN_MARCHA),
            a="cancelada",
            error="Cancelada a petición tuya.",
        )
        if not hecho:
            raise ConsejoError("Esta sesión ya ha terminado: no se puede cancelar.", codigo="estado")
        with _controles_lock:
            control = _controles.get(sesion_id)
        if control is not None:
            control.cancelada.set()
        return self._propia(sesion_id, case_id, user_id).sesion

    def obtener(self, sesion_id: str, *, case_id: str, user_id: str | None = None) -> Registro:
        return self._propia(sesion_id, case_id, user_id)

    def listar(self, case_id: str, *, limite: int = 50) -> list[Registro]:
        return self.almacen.listar(case_id, limite=limite)

    def borrar(self, sesion_id: str, *, case_id: str, user_id: str | None = None) -> None:
        """Delete a session (a running one is cancelled first: its worker can no longer write)."""
        registro = self._propia(sesion_id, case_id, user_id)
        if registro.sesion.estado in ("pendiente_confirmacion", "en_cola", *EN_MARCHA):
            try:
                self.cancelar(sesion_id, case_id=case_id, user_id=user_id)
            except ConsejoError:
                pass
        self.almacen.borrar(sesion_id, case_id=case_id)

    # -- running ----------------------------------------------------------------------------------------------

    def _lanzar(self, case_id: str) -> None:
        """Single-process mode: run the case's queued sessions now, in a background thread (the job is the net)."""
        if not self.en_segundo_plano:
            return
        contexto = contextvars.copy_context()
        hilo = threading.Thread(
            target=contexto.run, args=(self._trabajar_caso, case_id), name="consejo-sesion", daemon=True
        )
        self._hilos = [h for h in self._hilos if h.is_alive()] + [hilo]
        hilo.start()

    def esperar(self, timeout: float | None = None) -> None:
        """Wait for this service's background threads (tests, a clean shutdown)."""
        for hilo in list(self._hilos):
            hilo.join(timeout)

    def _trabajar_caso(self, case_id: str) -> None:
        try:
            while True:
                reclamadas = self.almacen.reclamar(
                    momento=self._reloj(),
                    arriendo_s=self._arriendo_s(),
                    max_intentos=self._max_intentos(),
                    case_id=case_id,
                )
                if not reclamadas:
                    return
                registro, arriendo = reclamadas[0]
                self._ejecutar(registro, arriendo)
        except Exception:  # the job is the safety net: it picks up what is left
            logger.exception("council background run failed for case %s", case_id)

    def ejecutar_pendientes(self, *, limite: int = 5) -> dict[str, int]:
        """The job: housekeeping, then up to ``limite`` sessions. Never raises; returns counts."""
        resumen = {"ejecutadas": 0, "terminadas": 0, "fallidas": 0, "caducadas": 0, "agotadas": 0, "purgadas": 0}
        try:
            antes = self._reloj() - timedelta(hours=max(1, int(getattr(settings, "CONSEJO_CADUCIDAD_HORAS", 72))))
            resumen["caducadas"] = len(self.almacen.caducar_pendientes(antes_de=antes))
        except Exception:
            logger.exception("council: expiring convocations failed")
        try:
            for registro in self.almacen.agotadas(momento=self._reloj(), max_intentos=self._max_intentos()):
                error = "Se interrumpió varias veces y no se pudo terminar."
                if self.almacen.fallar_agotada(registro.sesion.id, error=error, momento=self._reloj()):
                    resumen["agotadas"] += 1
                    actual = self.almacen.obtener(registro.sesion.id, case_id=registro.sesion.case_id)
                    if actual is not None:
                        self._notificar(actual.sesion)
        except Exception:
            logger.exception("council: exhausted sessions check failed")
        while resumen["ejecutadas"] < limite:
            try:
                reclamadas = self.almacen.reclamar(
                    momento=self._reloj(), arriendo_s=self._arriendo_s(), max_intentos=self._max_intentos()
                )
            except Exception:
                logger.exception("council: claiming sessions failed")
                break
            if not reclamadas:
                break
            registro, arriendo = reclamadas[0]
            resumen["ejecutadas"] += 1
            estado = self._ejecutar(registro, arriendo)
            if estado == "terminada":
                resumen["terminadas"] += 1
            elif estado == "fallida":
                resumen["fallidas"] += 1
        try:
            dias = max(1, int(getattr(settings, "CONSEJO_RETENCION_DIAS", 365)))
            resumen["purgadas"] = self.almacen.purgar(antes_de=self._reloj() - timedelta(days=dias))
        except Exception:
            logger.exception("council: retention failed")
        return resumen

    def _emitir(self, sesion_id: str, arriendo: str, control: ControlSesion) -> Callable[..., None]:
        """The engine's ``emit``: progress recorded, lease renewed, and a lost lease (cancelled / taken over) stops
        the run at its next model call."""
        lock = threading.Lock()

        def emit(tipo: str, **datos: Any) -> None:
            fase = datos.get("fase")
            paso: PasoProgreso | None = None
            estado = None
            if tipo == EVENTO_FASE:
                paso = PasoProgreso(tipo="fase", fase=fase, estado=str(datos.get("estado", "")), en=self._reloj())
                estado = _ESTADO_DE_FASE.get(str(fase)) if datos.get("estado") in ("start", "inicio") else None
            elif tipo == EVENTO_MIEMBRO:
                paso = PasoProgreso(
                    tipo="miembro",
                    fase=fase,
                    estado=str(datos.get("estado", "")),
                    miembro_id=None,  # who is who stays hidden until the ruling
                    en=self._reloj(),
                )
            elif tipo == EVENTO_ESPECIALISTA:
                paso = PasoProgreso(
                    tipo="miembro",
                    fase="instruccion",
                    estado=str(datos.get("estado", "")),
                    miembro_id=str(datos.get("clave") or "") or None,
                    en=self._reloj(),
                )
            if paso is None:
                return
            try:
                with lock:
                    vivo = self.almacen.renovar(
                        sesion_id,
                        arriendo,
                        arriendo_s=self._arriendo_s(),
                        estado=estado,
                        fase=str(fase) if tipo == EVENTO_FASE and fase else None,
                        paso=paso,
                        coste=control.coste(),
                        momento=self._reloj(),
                    )
            except Exception:  # progress is best effort
                logger.exception("council progress not recorded")
                return
            if not vivo:
                control.motivo = "cancelada"
                control.cancelada.set()

        return emit

    def _ejecutar(self, registro: Registro, arriendo: str) -> str:
        """One run of a claimed session. Returns the final state («terminada», «fallida» or «perdida»)."""
        sesion = registro.sesion
        control = ControlSesion(
            tope_usd=sesion.tope_usd,
            max_llamadas=int(getattr(settings, "CONSEJO_MAX_LLAMADAS", 150) or 150),
            inicial=sesion.coste,
        )
        with _controles_lock:
            _controles[sesion.id] = control
        entorno: EntornoSesion | None = None
        instruccion: ResultadoInstruccion | None = None
        try:
            from asistente.plataforma.ops.consumo import contexto_consumo

            with contexto_consumo(user_id=sesion.user_id, case_id=sesion.case_id):
                case = self._caso(sesion.case_id)
                if case is None:
                    return self._fallar(registro, arriendo, control, "El expediente de esta decisión ya no existe.")
                modulos = self._modulos(sesion.case_id)
                entorno = self._fabrica.entorno(registro, case=case, modulos=modulos, control=control)
                emit = self._emitir(sesion.id, arriendo, control)
                from asistente.nucleo.consejo.expediente import instruir

                instruccion = instruir(
                    sesion.pregunta_usuario,
                    case=case,
                    modulos=modulos,
                    proveedor_para=entorno.proveedor_para,
                    preferencia_usuario=registro.extra.preferencia,
                    hoy=self._dia(),
                    especialistas=entorno.especialistas,
                    memoria=entorno.memoria,
                    user_id=sesion.user_id,
                    historial=[{"role": "user", "content": m} for m in registro.extra.mensajes_previos],
                    opciones=registro.extra.opciones_sugeridas,
                    emit=emit,
                    expediente_id=f"exp_{sesion.id[5:]}",
                )
                if not self.almacen.renovar(
                    sesion.id,
                    arriendo,
                    arriendo_s=self._arriendo_s(),
                    estado="deliberando",
                    fase="propuestas",
                    expediente=instruccion.expediente,
                    coste=control.coste(),
                    momento=self._reloj(),
                ):
                    return "perdida"
                restante = max(Decimal(0), sesion.tope_usd - control.coste().usd)
                extra_motor: dict[str, Any] = {}
                if "base" in inspect.signature(entorno.motor.deliberar).parameters:
                    extra_motor["base"] = sesion  # the engine keeps the stored identity fields
                resultado = entorno.motor.deliberar(
                    instruccion.expediente, sesion_id=sesion.id, emit=emit, tope_usd=restante, **extra_motor
                )
                if control.cancelada.is_set() or control.motivo == "cancelada":
                    # The engine absorbs the stop (it never re-raises): the user cancelled, or another run took the
                    # session over. The row already says so; this run's result is discarded.
                    return "perdida"
                final = self._final(registro, resultado, instruccion, control, entorno)
        except PresupuestoAgotadoError as exc:
            if control.motivo == "cancelada":
                return "perdida"
            if control.motivo == "tope":
                mensaje = (
                    f"Se alcanzó el tope de coste que aceptaste ({formatear_usd(sesion.tope_usd)}) antes de terminar. "
                    "No hay dictamen: prefiero no darte uno a medias."
                )
            else:
                mensaje = f"{exc} No hay dictamen: prefiero no darte uno a medias."
            return self._fallar(registro, arriendo, control, mensaje, instruccion=instruccion)
        except Exception:
            logger.exception("council session %s failed", sesion.id)
            return self._fallar(
                registro,
                arriendo,
                control,
                "Ha fallado algo por mi parte mientras el consejo deliberaba. No hay dictamen.",
                instruccion=instruccion,
            )
        finally:
            with _controles_lock:
                _controles.pop(sesion.id, None)
            if entorno is not None:
                try:
                    entorno.cerrar()
                except Exception:
                    logger.debug("council run cleanup failed", exc_info=True)
        extra = registro.extra.model_copy(update={"terminada": self._reloj()})
        actual = self.almacen.obtener(sesion.id, case_id=sesion.case_id)
        if actual is not None:
            extra.progreso = actual.extra.progreso
        if not self.almacen.guardar_resultado(final, arriendo, extra=extra):
            logger.info("council session %s: result discarded (cancelled or taken over)", sesion.id)
            return "perdida"
        self._notificar(final)
        return final.estado

    def _final(
        self,
        registro: Registro,
        resultado: SesionConsejo,
        instruccion: ResultadoInstruccion,
        control: ControlSesion,
        entorno: EntornoSesion,
    ) -> SesionConsejo:
        sesion = registro.sesion
        if not isinstance(resultado, SesionConsejo):
            raise TypeError("el motor no devolvió una SesionConsejo")
        dictamen = resultado.dictamen
        estado = resultado.estado if resultado.estado in ("terminada", "fallida") else "fallida"
        error = resultado.error
        if estado == "terminada" and dictamen is None:
            estado, error = "fallida", "El consejo terminó sin dictamen."
        parcial = motivo_parcial(dictamen)
        primera_ronda = [p for p in resultado.propuestas if p.ronda == 1]
        if estado == "terminada" and parcial is not None and len(primera_ronda) < MIN_MIEMBROS:
            # A stop before round 1 gave the council its minimum of independent proposals: there is nothing a
            # council could rule on, partial or not.
            estado, error, dictamen = (
                "fallida",
                f"{parcial} No hay dictamen: no llegó a haber propuestas suficientes.",
                None,
            )
        if dictamen is not None:
            dictamen = dictamen.model_copy(update={"sesion_id": sesion.id})
            dictamen = self._puertas(dictamen, sesion, instruccion, entorno)
        medido = control.coste()
        if medido.llamadas > control.inicial.llamadas:
            coste = medido
        else:  # nothing metered in this run: the engine's own accounting, on top of earlier runs
            previo, motor = control.inicial, resultado.coste
            coste = CosteSesion(
                llamadas=previo.llamadas + motor.llamadas,
                usd=previo.usd + motor.usd,
                tokens_entrada=previo.tokens_entrada + motor.tokens_entrada,
                tokens_salida=previo.tokens_salida + motor.tokens_salida,
            )
        if estado == "fallida" and not error:
            error = "El consejo no pudo terminar la deliberación."
        return sesion.model_copy(
            update={
                "estado": estado,
                "fase": resultado.fase or ("sintesis" if dictamen is not None else None),
                "actualizada": self._reloj(),
                "coste": coste,
                "expediente": instruccion.expediente,
                "propuestas": list(resultado.propuestas),
                "criticas": list(resultado.criticas),
                "rankings": list(resultado.rankings),
                "dictamen": dictamen,
                "error": error,
            }
        )

    def _puertas(
        self, dictamen: Dictamen, sesion: SesionConsejo, instruccion: ResultadoInstruccion, entorno: EntornoSesion
    ) -> Dictamen:
        """The brain's figures gate on the ruling (when the engine has not run it), against the dossier's figures;
        offending figures are withdrawn and said. Then the verifier on the claims that cite a fragment (advisory:
        each unsupported claim becomes a warning)."""
        from asistente.nucleo.cerebro.cerebro import puerta_de_cifras, retirar_cifras

        avisos = list(dictamen.avisos)
        puerta = dictamen.puerta
        if puerta is None:
            informes = instruccion.informes or [_informe_de_cifras(instruccion)]
            textos_usuario = [
                sesion.pregunta_usuario,
                instruccion.expediente.pregunta,
                *(o.descripcion for o in instruccion.expediente.opciones),
            ]
            puerta = puerta_de_cifras(
                _texto_del_dictamen(dictamen), informes, ledger=instruccion.ledger, textos_usuario=textos_usuario
            )
            if puerta is None:
                # The gate did not run: it never fails open; the ruling's figures are flagged and its confidence
                # capped.
                from asistente.nucleo.cerebro.cerebro import AVISO_PUERTA_CAIDA

                avisos.append(AVISO_PUERTA_CAIDA)
                dictamen = dictamen.model_copy(update={"confianza": min(dictamen.confianza, 0.5)})
            if puerta is not None and puerta.violaciones:
                dictamen = dictamen.model_copy(
                    update={
                        "resumen": _al_menos(retirar_cifras(dictamen.resumen, puerta.violaciones), dictamen.resumen),
                        "disenso": retirar_cifras(dictamen.disenso, puerta.violaciones) if dictamen.disenso else None,
                    }
                )
                avisos.append(
                    "He retirado del dictamen "
                    + ("una cifra" if len(puerta.violaciones) == 1 else f"{len(puerta.violaciones)} cifras")
                    + " que no salía"
                    + ("" if len(puerta.violaciones) == 1 else "n")
                    + " de ningún cálculo de los especialistas: "
                    + "; ".join(f"«{v.literal}»" for v in puerta.violaciones[:5])
                )
        avisos += self._verificar(dictamen, sesion, instruccion, entorno)
        return dictamen.model_copy(update={"puerta": puerta, "avisos": list(dict.fromkeys(avisos))})

    @staticmethod
    def _verificar(
        dictamen: Dictamen, sesion: SesionConsejo, instruccion: ResultadoInstruccion, entorno: EntornoSesion
    ) -> list[str]:
        from asistente.nucleo.agent.evidence import extract_cited_ids

        citados = extract_cited_ids(dictamen.resumen)
        fragmentos = [f for f in (instruccion.ledger.get(i) for i in citados) if f is not None]
        if not fragmentos:
            return []
        try:
            from asistente.nucleo.agent.verifier import Verifier

            informe = Verifier(entorno.proveedor_para("verificador")).verify(
                question=instruccion.expediente.pregunta, answer=dictamen.resumen, fragments=fragmentos
            )
        except PresupuestoAgotadoError:
            return ["No se pudieron comprobar las citas del dictamen (límite de coste de la sesión)."]
        except Exception:
            logger.exception("council ruling verifier failed")
            return ["No se pudieron comprobar las citas del dictamen."]
        if informe.error is not None:
            return ["No se pudieron comprobar las citas del dictamen."]
        return [f"Afirmación sin respaldo suficiente en su fuente: «{_recorte(c.text, 200)}»" for c in informe.problems]

    def _fallar(
        self,
        registro: Registro,
        arriendo: str,
        control: ControlSesion,
        mensaje: str,
        *,
        instruccion: ResultadoInstruccion | None = None,
    ) -> str:
        sesion = registro.sesion.model_copy(
            update={
                "estado": "fallida",
                "actualizada": self._reloj(),
                "coste": control.coste(),
                "error": mensaje[:1000],
                "expediente": instruccion.expediente if instruccion is not None else None,
            }
        )
        actual = self.almacen.obtener(sesion.id, case_id=sesion.case_id)
        extra = (actual.extra if actual is not None else registro.extra).model_copy(update={"terminada": self._reloj()})
        if not self.almacen.guardar_resultado(sesion, arriendo, extra=extra):
            return "perdida"
        self._notificar(sesion)
        return "fallida"

    # -- delivery ---------------------------------------------------------------------------------------------

    def _notificar(self, sesion: SesionConsejo) -> None:
        """ONE notification per session (kind ``modulo:consejo``, severity ``info``). Never raises."""
        try:
            from asistente.nucleo.proactive.store import Notification, NotificationStore

            pregunta = _recorte(sesion.pregunta_usuario, 160)
            if sesion.estado == "terminada" and sesion.dictamen is not None:
                dictamen = sesion.dictamen
                parcial = motivo_parcial(dictamen)
                titulo = TITULO_PARCIAL if parcial is not None else TITULO_LISTO
                cuerpo = (
                    f"El consejo ha estudiado tu decisión: «{pregunta}».\n\n"
                    f"**Recomendación:** {recomendacion_en_palabras(dictamen, sesion)} "
                    f"({confianza_en_palabras(dictamen.confianza)}; {CONSENSO_EN_PALABRAS[dictamen.consenso]}).\n\n"
                    "En «Consejo» tienes en qué coinciden y en qué discrepan, el argumento de la minoría, los "
                    "supuestos de los que depende y qué dato lo aclararía."
                )
                if parcial is not None:
                    cuerpo += f"\n\n**Es un dictamen parcial:** {parcial}"
                acciones = ["Abre «Consejo» para leer el dictamen completo."]
            else:
                titulo = TITULO_FALLIDO
                cuerpo = (
                    f"No he podido completar el estudio de «{pregunta}». {sesion.error or ''}\n\n"
                    f"Gasto: {formatear_usd(sesion.coste.usd)} de un máximo de {formatear_usd(sesion.tope_usd)}. "
                    "Puedes volver a convocarlo cuando quieras."
                ).strip()
                acciones = []
            store = NotificationStore(self.db_path)
            try:
                notificacion = Notification(
                    case_id=sesion.case_id,
                    kind=KIND_CONSEJO,
                    key=f"{KIND_CONSEJO}:{sesion.id}",
                    title=titulo,
                    body=cuerpo,
                    severity="info",
                    actions=acciones,
                )
                if store.add(notificacion):
                    self.almacen.anotar_notificacion(sesion.id, notificacion.id)
            finally:
                store.close()
        except Exception:
            logger.exception("council notification not written for session %s", sesion.id)

    # -- the case -----------------------------------------------------------------------------------------------

    def _caso(self, case_id: str) -> CaseState | None:
        from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore

        return SQLiteCaseStateStore(self.db_path).get_case(case_id)

    @staticmethod
    def _modulos(case_id: str) -> list[Modulo]:
        try:
            from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso

            return list(modulos_activos_para_caso(case_id))
        except Exception:
            logger.exception("active modules unavailable for the council")
            return []


def motivo_parcial(dictamen: Dictamen | None) -> str | None:
    """Why the ruling is partial (the engine's «Dictamen parcial: …» warning), or None for a complete one."""
    if dictamen is None:
        return None
    for aviso in dictamen.avisos:
        if aviso.startswith(PREFIJO_PARCIAL):
            return aviso[len(PREFIJO_PARCIAL) :].strip() or "la sesión se detuvo antes de terminar."
    return None


def _texto_del_dictamen(dictamen: Dictamen) -> str:
    partes = [dictamen.resumen, dictamen.disenso or "", dictamen.condicion or ""]
    for postura in dictamen.posturas:
        partes += [*postura.a_favor, *postura.en_contra]
    partes += [*dictamen.supuestos_decisivos, *dictamen.sensibilidad, *dictamen.datos_que_resolverian]
    return "\n".join(p for p in partes if p)


def _al_menos(texto: str, original: str) -> str:
    return texto if len(texto) >= 20 else original


def _informe_de_cifras(instruccion: ResultadoInstruccion) -> InformeEspecialista:
    """The dossier's figures as one report (when phase 0 reports are not at hand)."""
    return InformeEspecialista(
        encargo_id="e1",
        especialista="cerebro",
        nombre="Expediente",
        estado="ok",
        cifras=list(instruccion.expediente.cifras[:60]),
    )


def especialistas_previstos(pregunta: str, modulos: Sequence[Modulo], case: CaseState | None = None) -> list[str]:
    """The specialists phase 0 will probably consult (for the estimate; the rules, no model)."""
    try:
        from asistente.nucleo.cerebro.fiscal import DESCRIPCION_FISCAL, NOMBRE_FISCAL
        from asistente.nucleo.cerebro.intencion import FichaEspecialista, senales

        fichas = [FichaEspecialista(clave=CLAVE_FISCAL, nombre=NOMBRE_FISCAL, descripcion=DESCRIPCION_FISCAL)]
        for modulo in modulos:
            clave = str(getattr(modulo, "clave", ""))
            if clave and clave != CLAVE_FISCAL and re.fullmatch(r"[a-z][a-z0-9_]{1,30}", clave):
                fichas.append(FichaEspecialista.de(modulo, modulo))
        candidatos = senales(pregunta, fichas, case=case).candidatos()
    except Exception:
        logger.debug("specialists for the estimate unavailable", exc_info=True)
        candidatos = []
    return (candidatos or [CLAVE_FISCAL])[:3]


_controles: dict[str, ControlSesion] = {}
_controles_lock = threading.Lock()
_servicios: dict[str, ServicioConsejo] = {}
_servicios_lock = threading.Lock()


def servicio_para(db_path: str) -> ServicioConsejo:
    """The process-wide service of a database (the API and its background threads share it)."""
    with _servicios_lock:
        servicio = _servicios.get(str(db_path))
        if servicio is None:
            servicio = _servicios[str(db_path)] = ServicioConsejo(str(db_path))
        return servicio


def configurar_servicio(db_path: str, servicio: ServicioConsejo | None) -> None:
    """Replace (or forget, with None) the service of a database (tests, a pinned factory)."""
    with _servicios_lock:
        if servicio is None:
            _servicios.pop(str(db_path), None)
        else:
            _servicios[str(db_path)] = servicio


def servicio_consejo() -> ServicioConsejo:
    return servicio_para(str(settings.DECISION_STATE_DB_PATH))


def convocar_desde_cerebro(
    *,
    case: CaseState,
    pregunta: str,
    opciones: Sequence[str] = (),
    motivo: str = "",
    historial: Sequence[Mapping[str, Any]] = (),
    modulos: Sequence[Modulo] = (),
    deteccion: Deteccion | None = None,
) -> tuple[SesionConsejo, str]:
    """What the brain's ``convocar_consejo`` tool does: a pending session and the sentence for the answer."""
    servicio = servicio_consejo()
    sesion = servicio.convocar(
        case=case,
        pregunta=pregunta,
        origen="cerebro",
        motivo=motivo,
        opciones=opciones,
        historial=historial,
        modulos=modulos,
        deteccion=deteccion,
    )
    registro = servicio.almacen.obtener(sesion.id, case_id=case.case_id)
    minutos = registro.extra.minutos_estimados if registro is not None else 5
    return sesion, nota_convocatoria(sesion.estimacion_usd, max(1, minutos))


# --------------------------------------------------------------------------------------------------------------
# The brain's tool
# --------------------------------------------------------------------------------------------------------------

CONVOCAR = "convocar_consejo"


@dataclass
class HerramientaConsejo:
    """What the brain adds to a turn when the council may be offered: the tool and its prompt paragraph."""

    herramientas: list[Any]
    addendum: str
    deteccion: Deteccion


def spec_convocar() -> Any:
    from asistente.nucleo.agent.providers.base import ToolSpec

    return ToolSpec(
        name=CONVOCAR,
        description=(
            "PROPONE al usuario estudiar una decisión difícil en el consejo (varias IA que analizan por separado, se "
            "critican de forma anónima y emiten un dictamen). NO ejecuta nada: crea una propuesta con su coste "
            "estimado que el usuario confirma o rechaza en una tarjeta. Úsala solo para decisiones difíciles "
            "(alternativas, consecuencias difíciles de deshacer, importes grandes, incertidumbre) o si el usuario lo "
            "pide; nunca para preguntas informativas."
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "pregunta": {
                    "type": "string",
                    "description": "La decisión, como la ha planteado el usuario (sin añadir tu opinión).",
                },
                "opciones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 6,
                    "description": "Las alternativas que se plantean, si están claras.",
                },
                "motivo": {"type": "string", "description": "Por qué es una decisión difícil, en una frase."},
            },
            "required": ["pregunta", "motivo"],
        },
    )


def herramienta_convocar(
    *,
    case: CaseState | None,
    consulta: str,
    intencion: Any = None,
    chat_history: Sequence[Mapping[str, Any]] | None = None,
    modulos: Sequence[Modulo] | None = None,
) -> HerramientaConsejo | None:
    """The ``convocar_consejo`` tool for this turn, or None.

    Offered ONLY when the deterministic detector says the question is a hard decision (or the user asked for the
    council): the model proposes, the rules decide whether the offer exists at all, and the user decides whether it
    runs. The tool never runs the council: it creates a pending session with its estimate and returns the sentence
    the answer must carry (the chat renders the confirmation card)."""
    if case is None or not activo():
        return None
    from asistente.nucleo.agent.tools import ToolOutcome
    from asistente.nucleo.cerebro.prompts import ADDENDUM_CONSEJO
    from asistente.nucleo.consejo.deteccion import es_decision_dificil

    deteccion = es_decision_dificil(consulta, intencion, case)
    if not deteccion.dificil:
        return None
    historial = [dict(m) for m in chat_history or () if isinstance(m, Mapping)]

    def convocar(argumentos: dict[str, Any]) -> ToolOutcome:
        propuesta = " ".join(str(argumentos.get("pregunta") or "").split())
        # The session keeps the user's own question when it is the decision itself; a follow-up such as
        # «consúltalo con el consejo» carries no decision, so the brain's statement of it is used.
        pregunta = consulta if not deteccion.pedido_por_usuario or len(propuesta) < MIN_PREGUNTA else propuesta
        opciones = [str(o) for o in argumentos.get("opciones") or [] if isinstance(o, str) and o.strip()]
        try:
            sesion, nota = convocar_desde_cerebro(
                case=case,
                pregunta=pregunta,
                opciones=opciones[:6] or deteccion.opciones_detectadas,
                motivo=str(argumentos.get("motivo") or "")[:600] or "; ".join(deteccion.motivos),
                historial=historial,
                modulos=list(modulos or []),
                deteccion=deteccion,
            )
        except ConsejoError as exc:
            return ToolOutcome(content=exc.mensaje, summary=exc.mensaje[:120], is_error=True)
        contenido = (
            "Propuesta creada: el consejo NO se ha ejecutado; espera a que el usuario la confirme o la rechace en la "
            f"tarjeta. Incluye en tu respuesta, tal cual, esta frase: «{nota}». No anticipes lo que dirá el consejo."
        )
        return ToolOutcome(content=contenido, summary=f"consejo propuesto ({sesion.id})")

    return HerramientaConsejo(
        herramientas=[(spec_convocar(), convocar)],
        addendum=ADDENDUM_CONSEJO.replace("{pista}", deteccion.como_pista()),
        deteccion=deteccion,
    )


__all__ = [
    "CONSENSO_EN_PALABRAS",
    "CONVOCAR",
    "HerramientaConsejo",
    "herramienta_convocar",
    "spec_convocar",
    "KIND_CONSEJO",
    "ConsejoError",
    "ControlSesion",
    "EntornoSesion",
    "FabricaConsejo",
    "FabricaPorDefecto",
    "ResultadoConfirmacion",
    "ServicioConsejo",
    "activo",
    "configurar_servicio",
    "confianza_en_palabras",
    "convocar_desde_cerebro",
    "especialistas_previstos",
    "motivo_parcial",
    "motor_por_defecto",
    "nombres_de_miembros",
    "nota_convocatoria",
    "recomendacion_en_palabras",
    "servicio_consejo",
    "servicio_para",
]

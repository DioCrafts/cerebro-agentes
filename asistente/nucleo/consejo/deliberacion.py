"""The council's deliberation engine: phases 1–4 and the chair, over a verified dossier (``MotorDeliberacion``).

``Deliberacion.deliberar(expediente, sesion_id=…, emit=…, tope_usd=…)`` runs one session and returns a
``SesionConsejo`` (``terminada`` or ``fallida``, never an exception for a model's misbehaviour):

1. **Propuestas** — every seat, in parallel (bounded threads, each in a copy of the caller's ``contextvars`` so the
   metering context and the prompt variant reach its calls), writes a ``Propuesta`` from the dossier, the user's
   preference SEALED. Each output is strictly validated (option ids, confidence in [0, 1], a condition for
   «depende», texts, figure ids); an invalid one gets ONE repair attempt (the error and the previous answer are sent
   back); a seat that still fails is dropped with a warning. Fewer than ``MIN_MIEMBROS`` valid proposals → the
   session is ``fallida``, said honestly.
2. **Figure discipline** — every number of every proposal goes through the brain's figures gate against the
   dossier (``presidente.revisar_cifras_consejo``): a number that is not a dossier figure (or a cross-specialist sum
   nobody computed, or an unknown figure id) is a violation. Violations are NOT silently fixed: the reviewers see them
   next to the proposal («comprobacion_cifras»), the author must fix them in the revision, the chair sees them, the
   ruling lists them.
3. **Críticas** — every member reviews the others' proposals, anonymised (``anonimato``: own proposal excluded,
   labels by position, a balanced Latin-square order so every proposal appears once in every position, uniform form),
   with the fixed rubric; its ranking must be a permutation of exactly the labels it saw (one repair, else its
   critiques are dropped and its proposal still counts). Rankings are translated to seats on the server and
   aggregated (``agregacion``: Kemeny–Young, Kendall's W, outlier reviewers).
4. **Revisión** — each member reads the critiques of ITS proposal (anonymous, shuffled) and, now unsealed, the user's
   preference, and revises (``cambios`` mandatory). The devil's advocate alone is told which option led round 1 (its
   mandate is to argue against it). A member that cannot revise stands by its round-1 proposal.
5. **Síntesis** — code fixes the recommendation, the consensus label (unánime / mayoría / dividido / insuficiente,
   from the final votes), the confidence and the agreement; the chair writes the prose from the final proposals
   (anonymised again, its own labels), the aggregate order, W in words and the critiques' error lists. The figures
   gate runs on the ruling (one correction round, then withdrawal). Authorship is revealed only now
   (``Dictamen.miembros``, ``Dictamen.autoria``), and the stored critiques and rankings are relabelled into the
   ruling's public labels so they can be read against ``autoria``.

Cost: every call is metered (the providers come from ``proveedor_para(<purpose>)``) and its tokens, as the API
reported them through the provider's ``on_usage`` hook, are priced with the project's price table into
``CosteSesion``. Before the critiques, the revision and the synthesis, the engine projects the phase's cost from
round 1's actual tokens (tokens per prompt character and output tokens per call, priced for each call's model,
× :data:`MARGEN_PROYECCION`) and does NOT start a phase that would take the session over ``tope_usd``: it rules with
what it has (a partial ruling written by code, said so) or fails honestly. The platform's budget
(``PresupuestoAgotadoError``) stops the session the same way. A call whose usage was not reported, or whose model has
no price, is counted as a call with unknown cost (the ruling says so; the cap cannot see it).

Determinism: given the seed (``semilla``, else ``CONSEJO_SEMILLA``, else one derived from the session id) and
scripted providers, a session is reproducible: orders are seeded hashes, results are collected in seat order
whatever the thread timing, aggregation ties are broken by a documented key.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import contextvars
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
import logging
import re
import threading
from typing import Any, Literal, TypeVar

from pydantic import ValidationError

from asistente.nucleo.agent.providers.base import LLMProvider, ProviderError, Usage
from asistente.nucleo.cerebro.contrato import Emit, ResultadoPuerta
from asistente.nucleo.consejo import prompts
from asistente.nucleo.consejo.agregacion import (
    DEPENDE,
    Agregacion,
    agregar_detallado,
    confianza_del_consejo,
    consenso_de,
)
from asistente.nucleo.consejo.anonimato import (
    Limpiador,
    anonimizar_para,
    diseno_equilibrado,
    generador,
    orden_para,
    semilla_efectiva,
)
from asistente.nucleo.consejo.contrato import (
    EVENTO_FASE,
    EVENTO_MIEMBRO,
    MAX_MIEMBROS,
    MAX_TEXTO_PROPUESTA,
    MIN_MIEMBROS,
    CosteSesion,
    Critica,
    ErrorSenalado,
    EstadoSesion,
    ExpedienteConsejo,
    FaseConsejo,
    Miembro,
    Propuesta,
    PuntuacionRubrica,
    Ranking,
    Rol,
    SesionConsejo,
)
from asistente.nucleo.consejo.miembros import (
    PRESIDENTE,
    ROLES_CONOCIDOS,
    ROLES_CONTRARIOS,
    FabricaProveedores,
    miembro_de,
    roles_configurados,
)
from asistente.nucleo.consejo.presidente import (
    RedaccionPresidente,
    ResultadoCalculado,
    SalidaInvalidaError,
    componer_dictamen,
    describir_violaciones,
    mensaje_correccion_presidente,
    mensaje_presidente,
    redaccion_determinista,
    revisar_cifras_consejo,
    validar_redaccion,
)
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SESION_RE = re.compile(r"^cons_[0-9a-f]{32}$")
_CIFRA_RE = re.compile(r"^C[0-9]{1,4}$")
MARGEN_PROYECCION = Decimal("1.15")
# Output tokens of one call of each phase relative to a round-1 call (a critique covers n−1 proposals briefly; the
# chair writes the longest piece).
FACTOR_SALIDA: dict[str, Decimal] = {"criticas": Decimal("1.0"), "revision": Decimal("1.0"), "sintesis": Decimal("1.5")}
TOPE_ITEM = 400
MAX_LISTA_PROPUESTA = 10
MAX_CAMBIO_PROPUESTA = 6
EJES = ("rigor", "completitud", "prudencia", "claridad", "ajuste_al_caso")
TIPOS_ERROR = ("cifra", "supuesto", "omision", "logica", "ley", "riesgo_ignorado")

EstadoLlamada = Literal["ok", "reparado", "descartado", "presupuesto", "error"]


# --------------------------------------------------------------------------------------------------------------
# Usage observation (tokens per call, from the provider's ``on_usage`` hook)
# --------------------------------------------------------------------------------------------------------------

_USOS: contextvars.ContextVar[list[Usage] | None] = contextvars.ContextVar("consejo_usos", default=None)


def _observar(usage: Usage) -> None:
    usos = _USOS.get()
    if usos is not None:
        usos.append(usage)


def _proveedor_real(proveedor: Any) -> Any:
    """The provider that makes the API calls (a metering wrapper exposes it as ``wrapped``)."""
    actual = proveedor
    for _ in range(4):
        if isinstance(getattr(type(actual), "wrapped", None), property):
            actual = actual.wrapped
        else:
            break
    return actual


def observar_usos(proveedor: Any) -> bool:
    """Chain the council's observer onto the provider's ``on_usage`` hook (once; keeps any hook already there).

    The observer only records while a council call runs in the current context, so concurrent sessions and other
    users of the same provider never mix. False when the provider has no such hook (its tokens stay unknown).
    """
    real = _proveedor_real(proveedor)
    if real is None or not hasattr(real, "on_usage"):
        return False
    previo = real.on_usage
    if previo is _observar or getattr(previo, "observa_consejo", False):
        return True
    if previo is None:
        real.on_usage = _observar
        return True

    def ambos(usage: Usage) -> None:
        previo(usage)
        _observar(usage)

    ambos.observa_consejo = True  # type: ignore[attr-defined]
    if getattr(previo, "__name__", "") == "_capturar" or getattr(previo, "captura_consumo", False):
        ambos.captura_consumo = True  # type: ignore[attr-defined]  # the metering capture is inside ``previo``
    real.on_usage = ambos
    return True


# --------------------------------------------------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------------------------------------------------


@dataclass
class _UsoLlamada:
    fase: str
    miembro: str
    caracteres: int
    tokens_entrada: int
    tokens_salida: int
    usd: Decimal | None
    modelo: str
    proveedor: str


class Contabilidad:
    """The session's spend: calls, tokens and USD (priced with the project's table), per phase. Thread-safe."""

    def __init__(self, precios: Any | None = None) -> None:
        self._lock = threading.Lock()
        self._precios = precios
        self.llamadas: list[_UsoLlamada] = []

    def _tabla(self) -> Any:
        if self._precios is None:
            from asistente.plataforma.ops.consumo import cargar_precios

            self._precios = cargar_precios()
        return self._precios

    def _coste(self, usage: Usage) -> Decimal | None:
        from asistente.plataforma.ops.consumo import calcular_coste

        try:
            return calcular_coste(usage, self._tabla())
        except Exception:  # a broken price table never breaks the session
            logger.warning("could not price a council call", exc_info=True)
            return None

    def anotar(self, fase: str, miembro: str, usos: Sequence[Usage], *, caracteres: int, proveedor: Any) -> None:
        entrada = sum(u.input_tokens + u.cache_read_tokens + u.cache_write_tokens for u in usos)
        salida = sum(u.output_tokens for u in usos)
        costes = [self._coste(u) if u.reported else None for u in usos]
        usd = sum((c for c in costes if c is not None), Decimal(0)) if usos and None not in costes else None
        modelo = usos[-1].model if usos else str(getattr(proveedor, "model", "") or "")
        nombre = usos[-1].provider if usos else str(getattr(proveedor, "name", "") or "")
        with self._lock:
            self.llamadas.append(_UsoLlamada(fase, miembro, caracteres, entrada, salida, usd, modelo, nombre))

    @property
    def usd(self) -> Decimal:
        with self._lock:
            return sum((ll.usd for ll in self.llamadas if ll.usd is not None), Decimal(0))

    @property
    def sin_coste(self) -> int:
        with self._lock:
            return sum(1 for ll in self.llamadas if ll.usd is None)

    def por_fase(self) -> dict[str, Decimal]:
        with self._lock:
            totales: dict[str, Decimal] = {}
            for ll in self.llamadas:
                totales[ll.fase] = totales.get(ll.fase, Decimal(0)) + (ll.usd or Decimal(0))
            return totales

    def coste(self) -> CosteSesion:
        with self._lock:
            return CosteSesion(
                llamadas=len(self.llamadas),
                usd=sum((ll.usd for ll in self.llamadas if ll.usd is not None), Decimal(0)).quantize(
                    Decimal("0.000001")
                ),
                tokens_entrada=sum(ll.tokens_entrada for ll in self.llamadas),
                tokens_salida=sum(ll.tokens_salida for ll in self.llamadas),
            )

    def modelo_de(self, miembro: str) -> str | None:
        with self._lock:
            for ll in self.llamadas:
                if ll.miembro == miembro and ll.fase == "propuestas" and ll.modelo:
                    return ll.modelo
        return None

    def proyectar(self, previstas: Sequence[tuple[str, str, int]], factor_salida: Decimal) -> Decimal | None:
        """Projected USD of ``previstas`` (provider, model, prompt characters) from round 1's actual tokens.

        None when it cannot be known: round 1 reported no usage, or a model has no price.
        """
        from asistente.plataforma.ops.consumo import calcular_coste

        with self._lock:
            base = [ll for ll in self.llamadas if ll.fase == "propuestas" and ll.tokens_entrada and ll.caracteres]
        if not base:
            return None
        por_caracter = Decimal(sum(ll.tokens_entrada for ll in base)) / Decimal(sum(ll.caracteres for ll in base))
        salida = Decimal(sum(ll.tokens_salida for ll in base)) / Decimal(len(base))
        total = Decimal(0)
        for nombre, modelo, caracteres in previstas:
            uso = Usage(
                provider=nombre,
                model=modelo,
                input_tokens=int(por_caracter * caracteres),
                output_tokens=int(salida * factor_salida),
            )
            try:
                coste = calcular_coste(uso, self._tabla())
            except Exception:
                coste = None
            if coste is None:
                return None
            total += coste
        return (total * MARGEN_PROYECCION).quantize(Decimal("0.000001"))


# --------------------------------------------------------------------------------------------------------------
# The audit trail of one session
# --------------------------------------------------------------------------------------------------------------


@dataclass
class RegistroDeliberacion:
    """What happened in the last session, for the audit trail and the evaluation's bias harness (no model text).

    ``presentaciones``: reviewer → (label → seat) exactly as that reviewer saw the proposals (label A = first
    shown): with ``rankings_por_asiento`` it measures position bias and self-preference. ``etiquetas_publicas``: the
    ruling's labels (chair's view) → seat.
    """

    sesion_id: str
    semilla: str
    asientos: dict[str, Miembro] = field(default_factory=dict)
    presentaciones: dict[str, dict[str, str]] = field(default_factory=dict)
    rankings_por_asiento: dict[str, list[str]] = field(default_factory=dict)  # reviewer -> seats, best first
    etiquetas_publicas: dict[str, str] = field(default_factory=dict)
    puertas: dict[str, ResultadoPuerta] = field(default_factory=dict)  # "<seat>:<round>" -> figures gate
    descartados: dict[str, str] = field(default_factory=dict)  # seat -> why it left the deliberation
    agregacion: Agregacion | None = None
    coste_por_fase: dict[str, Decimal] = field(default_factory=dict)
    parada: str | None = None  # why the session stopped before the end, if it did
    eventos_llamada: list[dict[str, Any]] = field(default_factory=list)  # fase, miembro, estado per call


# --------------------------------------------------------------------------------------------------------------
# Strict validation of the members' outputs
# --------------------------------------------------------------------------------------------------------------


def _texto(valor: Any, campo: str, tope: int, *, obligatorio: bool = False, minimo: int = 0) -> str:
    if valor is None:
        valor = ""
    if not isinstance(valor, str):
        raise SalidaInvalidaError(f"«{campo}» debe ser un texto.")
    limpio = " ".join(valor.split())
    if obligatorio and len(limpio) < max(1, minimo):
        raise SalidaInvalidaError(
            f"«{campo}» es obligatorio" + (f" (al menos {minimo} caracteres)." if minimo else ".")
        )
    if len(limpio) > tope:
        from asistente.nucleo.consejo.anonimato import recortar

        limpio = recortar(limpio, tope)
    return limpio


def _lista(valor: Any, campo: str, maximo: int, tope: int = TOPE_ITEM) -> list[str]:
    if valor is None:
        return []
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise SalidaInvalidaError(f"«{campo}» debe ser una lista de textos.")
    salida: list[str] = []
    for v in valor:
        limpio = _texto(v, campo, tope)
        if limpio and limpio not in salida:
            salida.append(limpio)
    return salida[:maximo]


def _resumen_validacion(exc: ValidationError) -> str:
    partes = []
    for error in exc.errors()[:4]:
        ruta = ".".join(str(p) for p in error.get("loc", ())) or "(raíz)"
        partes.append(f"{ruta}: {error.get('msg', '')}")
    return "; ".join(partes)


def validar_propuesta(
    datos: Any, *, miembro_id: str, ronda: Literal[1, 2], expediente: ExpedienteConsejo
) -> tuple[Propuesta, list[str]]:
    """A member's JSON as a :class:`Propuesta` (seat and round set by code) and the unknown figure ids it cited.

    Strict on meaning (a real option id or «depende» with its condition, confidence in [0, 1], reasoning, «cambios»
    in round 2); length caps are applied by cutting, never by rejecting. Raises :class:`SalidaInvalidaError`.
    """
    if not isinstance(datos, dict):
        raise SalidaInvalidaError("la respuesta debe ser un objeto JSON.")
    opciones = {o.id for o in expediente.opciones}
    recomendacion = str(datos.get("recomendacion") or "").strip().lower().strip("«»\"'. ")
    if recomendacion not in opciones and recomendacion != DEPENDE:
        validas = ", ".join([*sorted(opciones), DEPENDE])
        raise SalidaInvalidaError(
            f"«recomendacion» debe ser una de: {validas}; has puesto «{recomendacion or 'nada'}»."
        )
    condicion = _texto(datos.get("condicion"), "condicion", 600) or None
    if recomendacion == DEPENDE and (not condicion or len(condicion) < 10):
        raise SalidaInvalidaError("con «depende», «condicion» es obligatoria: el hecho concreto que decide.")
    if recomendacion != DEPENDE:
        condicion = None
    confianza = datos.get("confianza")
    if isinstance(confianza, bool) or not isinstance(confianza, int | float) or not 0 <= float(confianza) <= 1:
        raise SalidaInvalidaError("«confianza» debe ser un número entre 0 y 1.")
    razonamiento = _texto(datos.get("razonamiento"), "razonamiento", MAX_TEXTO_PROPUESTA, obligatorio=True, minimo=20)
    crudas = datos.get("cifras_usadas") or []
    if not isinstance(crudas, list) or not all(isinstance(c, str) for c in crudas):
        raise SalidaInvalidaError("«cifras_usadas» debe ser una lista de ids de cifra (C1, C2…).")
    conocidas = {c.id for c in expediente.cifras}
    usadas: list[str] = []
    desconocidas: list[str] = []
    for cruda in crudas:
        cifra_id = cruda.strip().strip("[]").strip()
        if _CIFRA_RE.match(cifra_id.upper()):
            cifra_id = cifra_id.upper()
        if not cifra_id:
            continue
        destino = usadas if cifra_id in conocidas else desconocidas
        if cifra_id not in destino:
            destino.append(cifra_id)
    cambios: str | None = None
    if ronda == 2:
        cambios = _texto(datos.get("cambios"), "cambios", 1200, obligatorio=True, minimo=10)
    try:
        propuesta = Propuesta(
            miembro_id=miembro_id,
            ronda=ronda,
            recomendacion=recomendacion,
            condicion=condicion,
            confianza=float(confianza),
            razonamiento=razonamiento,
            supuestos=_lista(datos.get("supuestos"), "supuestos", MAX_LISTA_PROPUESTA),
            riesgos=_lista(datos.get("riesgos"), "riesgos", MAX_LISTA_PROPUESTA),
            que_me_haria_cambiar=_lista(
                datos.get("que_me_haria_cambiar"), "que_me_haria_cambiar", MAX_CAMBIO_PROPUESTA
            ),
            cifras_usadas=usadas,
            cambios=cambios,
        )
    except ValidationError as exc:
        raise SalidaInvalidaError(_resumen_validacion(exc)) from exc
    return propuesta, desconocidas


def validar_criticas(datos: Any, *, evaluador_id: str, etiquetas: Sequence[str]) -> tuple[list[Critica], Ranking]:
    """A reviewer's JSON as its critiques (exactly one per label it saw) and its ranking (a permutation of them).

    Raises :class:`SalidaInvalidaError` with what to fix (sent back in the repair attempt).
    """
    if not isinstance(datos, dict):
        raise SalidaInvalidaError("la respuesta debe ser un objeto JSON.")
    vistas = list(etiquetas)
    lista_vistas = ", ".join(vistas)
    crudas = datos.get("criticas")
    if not isinstance(crudas, list):
        raise SalidaInvalidaError("«criticas» debe ser una lista con una crítica por propuesta.")
    por_etiqueta: dict[str, Critica] = {}
    for cruda in crudas:
        if not isinstance(cruda, dict):
            raise SalidaInvalidaError("cada crítica debe ser un objeto.")
        etiqueta = str(cruda.get("etiqueta") or "").strip().upper()
        if etiqueta not in vistas:
            raise SalidaInvalidaError(
                f"«{etiqueta or 'sin etiqueta'}» no es una de las propuestas revisadas ({lista_vistas})."
            )
        if etiqueta in por_etiqueta:
            raise SalidaInvalidaError(f"hay dos críticas de la propuesta {etiqueta}.")
        errores_crudos = cruda.get("errores") or []
        if not isinstance(errores_crudos, list):
            raise SalidaInvalidaError(f"«errores» de {etiqueta} debe ser una lista.")
        errores: list[ErrorSenalado] = []
        for error in errores_crudos[:8]:
            if not isinstance(error, dict):
                raise SalidaInvalidaError(f"cada error de {etiqueta} debe ser un objeto con «tipo» y «texto».")
            tipo = str(error.get("tipo") or "").strip().lower().replace(" ", "_").replace("ó", "o")
            if tipo not in TIPOS_ERROR:
                raise SalidaInvalidaError(
                    f"tipo de error «{tipo}» no válido en {etiqueta}: usa {', '.join(TIPOS_ERROR)}."
                )
            texto = _texto(error.get("texto"), "texto", 500, obligatorio=True, minimo=5)
            errores.append(ErrorSenalado(tipo=tipo, texto=texto))  # type: ignore[arg-type]
        puntuacion = cruda.get("puntuacion")
        if not isinstance(puntuacion, dict):
            raise SalidaInvalidaError(f"falta la «puntuacion» de {etiqueta} ({', '.join(EJES)}, de 1 a 5).")
        notas: dict[str, int] = {}
        for eje in EJES:
            nota = puntuacion.get(eje)
            if isinstance(nota, float) and nota.is_integer():
                nota = int(nota)
            if isinstance(nota, bool) or not isinstance(nota, int) or not 1 <= nota <= 5:
                raise SalidaInvalidaError(f"«{eje}» de {etiqueta} debe ser un entero de 1 a 5.")
            notas[eje] = nota
        try:
            por_etiqueta[etiqueta] = Critica(
                evaluador_id=evaluador_id,
                etiqueta=etiqueta,
                fortalezas=_lista(cruda.get("fortalezas"), "fortalezas", 6),
                debilidades=_lista(cruda.get("debilidades"), "debilidades", 6),
                errores=errores,
                puntuacion=PuntuacionRubrica(**notas),
            )
        except ValidationError as exc:
            raise SalidaInvalidaError(_resumen_validacion(exc)) from exc
    faltan = [e for e in vistas if e not in por_etiqueta]
    if faltan:
        raise SalidaInvalidaError(f"faltan las críticas de: {', '.join(faltan)} (una por cada propuesta revisada).")
    ranking = datos.get("ranking")
    if not isinstance(ranking, list) or not all(isinstance(e, str) for e in ranking):
        raise SalidaInvalidaError(f"«ranking» debe ser una lista con las etiquetas {lista_vistas}.")
    orden = [e.strip().upper() for e in ranking]
    if sorted(orden) != sorted(vistas):
        raise SalidaInvalidaError(
            f"«ranking» debe contener cada etiqueta revisada ({lista_vistas}) exactamente una vez, de la mejor a la "
            f"peor; has enviado: {', '.join(orden) or 'nada'}."
        )
    return [por_etiqueta[e] for e in vistas], Ranking(evaluador_id=evaluador_id, orden=orden)


# --------------------------------------------------------------------------------------------------------------
# One session's state
# --------------------------------------------------------------------------------------------------------------


@dataclass
class _Respuesta:
    datos: Any | None = None
    error: str | None = None
    invalida: bool = False
    presupuesto: bool = False


@dataclass
class _Resultado:
    """A validated call: the value, how it went, and why not when it failed."""

    valor: Any | None
    estado: EstadoLlamada
    motivo: str | None = None


@dataclass
class _Sesion:
    expediente: ExpedienteConsejo
    sesion_id: str
    semilla: str
    emitir: Emit
    tope: Decimal | None
    contabilidad: Contabilidad
    registro: RegistroDeliberacion
    proveedores: dict[str, Any] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    presupuesto: Exception | None = None
    proyeccion_desconocida: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def marcar_presupuesto(self, exc: Exception) -> None:
        with self.lock:
            if self.presupuesto is None:
                self.presupuesto = exc

    def avisar(self, texto: str) -> None:
        with self.lock:
            if texto not in self.avisos:
                self.avisos.append(texto)


class _ParadaError(Exception):
    """The session stops before ``fase`` (cost cap or budget); the reason is for the user."""

    def __init__(self, fase: FaseConsejo, motivo: str) -> None:
        super().__init__(motivo)
        self.fase = fase
        self.motivo = motivo


# The phase that ran last when the session stops before the key one.
_ANTERIOR: dict[str, FaseConsejo] = {"criticas": "propuestas", "revision": "criticas", "sintesis": "revision"}
_NOMBRE_FASE: dict[str, str] = {
    "criticas": "las críticas cruzadas",
    "revision": "la revisión de las propuestas",
    "sintesis": "la síntesis del presidente",
}


def _emisor(emit: Emit | None) -> Emit:
    """``emit`` serialised across threads; a failing callback is logged, never raised."""
    lock = threading.Lock()

    def emitir(tipo: str, **datos: Any) -> None:
        if emit is None:
            return
        try:
            with lock:
                emit(tipo, **datos)
        except Exception:
            logger.exception("council progress callback failed")

    return emitir


# --------------------------------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------------------------------


class Deliberacion:
    """The council's deliberation engine (implements ``contrato.MotorDeliberacion``). One engine, many sessions.

    ``miembros``: the seats (default: ``CONSEJO_MIEMBROS``, i.e. :data:`ROLES_POR_DEFECTO`; 3 to 7, unique ids).
    ``proveedor_para``: the provider of a purpose (``consejo:miembro:<id>``, ``consejo:presidente``) — metered in
    production (:class:`~asistente.nucleo.consejo.miembros.FabricaProveedores`), scripted in tests.
    ``max_paralelo``: calls at once in one phase (``CONSEJO_MAX_PARALELO``). ``semilla``: the presentation orders'
    seed (default ``CONSEJO_SEMILLA``; none = one per session, from its id). ``diseno_equilibrado``: Latin-square
    presentation orders (default) instead of independent seeded shuffles. ``dictamen_parcial``: when the cost cap or
    the budget stops the session after round 1, rule with what there is (default, said so in the ruling) or end
    ``fallida`` (False). ``precios``: a price table (default: the project's). ``reloj``: the clock for the session's
    timestamps (tests). After each run, :attr:`registro` holds that session's audit trail.
    """

    def __init__(
        self,
        miembros: Sequence[Rol] | None = None,
        *,
        proveedor_para: Callable[[str], LLMProvider],
        presidente: Rol | None = None,
        max_paralelo: int | None = None,
        semilla: int | str | None = None,
        diseno_equilibrado: bool = True,
        dictamen_parcial: bool = True,
        precios: Any | None = None,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        roles = tuple(miembros) if miembros is not None else roles_configurados()
        if not MIN_MIEMBROS <= len(roles) <= MAX_MIEMBROS:
            raise ValueError(f"el consejo necesita entre {MIN_MIEMBROS} y {MAX_MIEMBROS} miembros, no {len(roles)}")
        ids = [r.id for r in roles]
        if len(set(ids)) != len(ids):
            raise ValueError(f"asientos repetidos: {ids}")
        self._roles = roles
        self._presidente = presidente or PRESIDENTE
        if self._presidente.id in ids:
            raise ValueError("el presidente no puede ocupar también un asiento")
        self._proveedor_para = proveedor_para
        configurado = max_paralelo if max_paralelo is not None else getattr(settings, "CONSEJO_MAX_PARALELO", 3)
        self._max_paralelo = max(1, int(configurado or 1))
        self._semilla = semilla if semilla is not None else semilla_configurada()
        self._equilibrado = diseno_equilibrado
        self._parcial = dictamen_parcial
        self._precios = precios
        self._reloj = reloj or (lambda: datetime.now(UTC))
        self._limpiador = Limpiador((*roles, self._presidente, *ROLES_CONOCIDOS.values()))
        self.registro: RegistroDeliberacion | None = None

    @property
    def roles(self) -> tuple[Rol, ...]:
        return self._roles

    @property
    def presidente(self) -> Rol:
        return self._presidente

    @property
    def semilla(self) -> int | str | None:
        """The configured seed (None: each session derives its own from its id)."""
        return self._semilla

    def autoria_criticas(self, sesion: SesionConsejo) -> dict[tuple[str, str], tuple[str, int]]:
        """For each stored critique, keyed by (reviewer, label in the ruling), the seat it was about and the position
        (0-based) at which that reviewer was shown it. For the evaluation's bias harness (position bias,
        self-preference). Recomputed from the session and the seed (the orders are deterministic), so it works on
        any stored session of this engine's configuration; empty when the session has no ruling."""
        if sesion.dictamen is None or not sesion.criticas:
            return {}
        if self.registro is not None and self.registro.sesion_id == sesion.id and self.registro.presentaciones:
            vistas = {r: list(mapa.values()) for r, mapa in self.registro.presentaciones.items()}
        else:
            semilla = semilla_efectiva(self._semilla, sesion.id)
            asientos = self._orden_asientos({p.miembro_id for p in sesion.propuestas if p.ronda == 1})
            if self._equilibrado:
                vistas = diseno_equilibrado(asientos, semilla, sesion.id)
            else:
                vistas = {a: orden_para(a, asientos, semilla, sesion.id) for a in asientos}
        autoria: dict[tuple[str, str], tuple[str, int]] = {}
        for critica in sesion.criticas:
            asiento = sesion.dictamen.autoria.get(critica.etiqueta)
            orden = vistas.get(critica.evaluador_id, [])
            if asiento is not None and asiento in orden:
                autoria[(critica.evaluador_id, critica.etiqueta)] = (asiento, orden.index(asiento))
        return autoria

    # -- the session ---------------------------------------------------------------------------------------------

    def deliberar(
        self,
        expediente: ExpedienteConsejo,
        *,
        sesion_id: str,
        emit: Emit | None = None,
        tope_usd: Decimal | None = None,
        base: SesionConsejo | None = None,
    ) -> SesionConsejo:
        """Deliberate on ``expediente`` and rule. Never raises for a model's or provider's failure: the session
        comes back ``fallida`` with the reason in ``error``. ``base``: the service's stored session, whose identity
        fields (case, user, question, estimate, creation) are kept; without it they are placeholders."""
        if not _SESION_RE.match(sesion_id):
            raise ValueError(f"id de sesión no válido: {sesion_id!r}")
        tope = tope_usd if tope_usd is not None else (base.tope_usd if base is not None and base.tope_usd else None)
        semilla = semilla_efectiva(self._semilla, sesion_id)
        registro = RegistroDeliberacion(sesion_id=sesion_id, semilla=semilla)
        self.registro = registro
        sesion = _Sesion(
            expediente=expediente,
            sesion_id=sesion_id,
            semilla=semilla,
            emitir=_emisor(emit),
            tope=tope,
            contabilidad=Contabilidad(self._precios),
            registro=registro,
        )
        estado: EstadoSesion = "fallida"
        fase: FaseConsejo | None = "propuestas"
        propuestas: list[Propuesta] = []
        criticas: list[Critica] = []
        rankings: list[Ranking] = []
        dictamen = None
        error: str | None = None
        try:
            salida = self._deliberar(sesion)
            estado, fase = salida["estado"], salida["fase"]
            propuestas, criticas, rankings = salida["propuestas"], salida["criticas"], salida["rankings"]
            dictamen, error = salida["dictamen"], salida["error"]
        except Exception as exc:  # a bug here must not lose the session: it ends «fallida», logged
            logger.exception("council session %s failed", sesion_id)
            error = f"Error interno del consejo ({type(exc).__name__}); no se ha podido terminar la deliberación."
        registro.coste_por_fase = sesion.contabilidad.por_fase()
        sesion.emitir(EVENTO_FASE, fase=fase, estado="terminada" if estado == "terminada" else "fallida")
        ahora = self._reloj()
        cambios: dict[str, Any] = {
            "estado": estado,
            "fase": fase,
            "actualizada": ahora,
            "coste": sesion.contabilidad.coste(),
            "expediente": expediente,
            "propuestas": propuestas,
            "criticas": criticas,
            "rankings": rankings,
            "dictamen": dictamen,
            "error": error,
        }
        if base is not None:
            return base.model_copy(update={**cambios, "tope_usd": tope if tope is not None else base.tope_usd})
        return SesionConsejo(
            id=sesion_id,
            case_id="",
            pregunta_usuario=expediente.pregunta[:4000],
            creada=ahora,
            estimacion_usd=tope if tope is not None else Decimal(0),
            tope_usd=tope if tope is not None else Decimal(0),
            **cambios,
        )

    def _deliberar(self, s: _Sesion) -> dict[str, Any]:
        roles = {r.id: r for r in self._roles}
        for rol in self._roles:
            try:
                s.proveedores[rol.id] = self._proveedor(s, rol.proposito)
            except Exception:  # a seat whose model cannot be reached leaves; the others deliberate
                logger.exception("council seat %s: provider unavailable", rol.id)
                s.registro.descartados[rol.id] = "proveedor no disponible"
                s.avisar(f"No se pudo contactar con el modelo de «{rol.nombre}»: quedó fuera de la deliberación.")
                continue
            s.registro.asientos[rol.id] = miembro_de(rol, s.proveedores[rol.id])

        # 1. Independent proposals (the preference sealed).
        s.emitir(EVENTO_FASE, fase="propuestas", estado="inicio")
        r1 = self._ronda_propuestas(s)
        s.emitir(EVENTO_FASE, fase="propuestas", estado="fin", validas=len(r1))
        if len(r1) < MIN_MIEMBROS:
            motivo = (
                f"Solo {len(r1)} de los {len(self._roles)} miembros presentaron una propuesta válida; hacen falta al "
                f"menos {MIN_MIEMBROS} para deliberar."
            )
            if s.presupuesto is not None:
                motivo += f" {s.presupuesto}"
            return self._fallida(s, "propuestas", motivo, r1)
        puertas_r1: dict[str, ResultadoPuerta] = {}
        for asiento, (propuesta, desconocidas) in r1.items():
            puertas_r1[asiento] = self._puerta(s, propuesta, desconocidas, con_preferencia=False)
        propuestas_r1 = {a: p for a, (p, _d) in r1.items()}

        criticas_por_evaluador: dict[str, list[Critica]] = {}
        rankings_por_evaluador: dict[str, Ranking] = {}
        mapas: dict[str, dict[str, str]] = {}
        agregacion: Agregacion | None = None
        posiciones: dict[str, Propuesta] = dict(propuestas_r1)
        revisadas: dict[str, Propuesta] = {}
        puertas_finales = dict(puertas_r1)
        parada: _ParadaError | None = None
        try:
            # 2. Anonymous cross critiques.
            criticas_por_evaluador, rankings_por_evaluador, mapas = self._ronda_criticas(s, propuestas_r1, puertas_r1)
            en_asientos = {
                evaluador: [mapas[evaluador][e] for e in ranking.orden]
                for evaluador, ranking in rankings_por_evaluador.items()
            }
            s.registro.rankings_por_asiento = en_asientos
            if en_asientos:
                agregacion = agregar_detallado(en_asientos, semilla=s.semilla)
                s.registro.agregacion = agregacion
            self._comprobar_presupuesto(s, "revision")
            # 3. Revision (the preference unsealed).
            revisadas, puertas_r2 = self._ronda_revision(
                s, propuestas_r1, puertas_r1, criticas_por_evaluador, mapas, roles
            )
            for asiento in propuestas_r1:
                if asiento in revisadas:
                    posiciones[asiento] = revisadas[asiento]
                    puertas_finales[asiento] = puertas_r2[asiento]
            self._comprobar_presupuesto(s, "sintesis")
        except _ParadaError as exc:
            parada = exc
            s.registro.parada = exc.motivo
            if not self._parcial:
                return self._fallida(s, _ANTERIOR[exc.fase], exc.motivo, r1)

        # The ruling (the cap may still stop the session before the chair).
        dictamen, parada = self._dictamen(
            s,
            posiciones,
            agregacion,
            criticas_por_evaluador,
            mapas,
            puertas_finales,
            revisadas,
            parada=parada,
        )
        if parada is not None and not self._parcial:
            return self._fallida(s, _ANTERIOR[parada.fase], parada.motivo, r1)
        etiqueta_publica = {asiento: e for e, asiento in s.registro.etiquetas_publicas.items()}
        criticas_publicas: list[Critica] = []
        for evaluador in self._orden_asientos(criticas_por_evaluador):
            mapa = mapas[evaluador]
            publicas = [
                c.model_copy(update={"etiqueta": etiqueta_publica[mapa[c.etiqueta]]})
                for c in criticas_por_evaluador[evaluador]
            ]
            criticas_publicas += sorted(publicas, key=lambda c: c.etiqueta)
        rankings_publicos = [
            Ranking(
                evaluador_id=evaluador,
                orden=[etiqueta_publica[mapas[evaluador][e]] for e in rankings_por_evaluador[evaluador].orden],
            )
            for evaluador in self._orden_asientos(rankings_por_evaluador)
        ]
        propuestas = [propuestas_r1[a] for a in self._orden_asientos(propuestas_r1)] + [
            revisadas[a] for a in self._orden_asientos(revisadas)
        ]
        return {
            "estado": "terminada",
            "fase": _ANTERIOR[parada.fase] if parada is not None else "sintesis",
            "propuestas": propuestas,
            "criticas": criticas_publicas,
            "rankings": rankings_publicos,
            "dictamen": dictamen,
            "error": None,
        }

    def _fallida(self, s: _Sesion, fase: FaseConsejo, motivo: str, r1: Mapping[str, Any]) -> dict[str, Any]:
        s.registro.parada = motivo
        propuestas = [r1[a][0] for a in self._orden_asientos(r1)]
        return {
            "estado": "fallida",
            "fase": fase,
            "propuestas": propuestas,
            "criticas": [],
            "rankings": [],
            "dictamen": None,
            "error": motivo,
        }

    def _orden_asientos(self, claves: Iterable[str]) -> list[str]:
        presentes = set(claves)
        return [r.id for r in self._roles if r.id in presentes]

    # -- providers and calls -------------------------------------------------------------------------------------

    def _proveedor(self, s: _Sesion, proposito: str) -> Any:
        proveedor = self._proveedor_para(proposito)
        observar_usos(proveedor)
        return proveedor

    def _llamar(
        self,
        s: _Sesion,
        proveedor: Any,
        *,
        fase: str,
        miembro: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> _Respuesta:
        if s.presupuesto is not None:
            return _Respuesta(error=str(s.presupuesto), presupuesto=True)
        from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

        usos: list[Usage] = []
        token = _USOS.set(usos)
        try:
            datos = proveedor.complete_json(system=system, user=user, schema=schema, schema_name=schema_name)
            return _Respuesta(datos=datos)
        except PresupuestoAgotadoError as exc:
            s.marcar_presupuesto(exc)
            return _Respuesta(error=str(exc), presupuesto=True)
        except ProviderError as exc:
            return _Respuesta(error=str(exc) or type(exc).__name__, invalida=exc.salida_invalida)
        except Exception as exc:  # a provider bug never breaks the session: this call failed, the others go on
            logger.warning("council call %s/%s failed", fase, miembro, exc_info=True)
            return _Respuesta(error=f"{type(exc).__name__}: {exc}")
        finally:
            _USOS.reset(token)
            s.contabilidad.anotar(fase, miembro, usos, caracteres=len(system) + len(user), proveedor=proveedor)

    def _pedir(
        self,
        s: _Sesion,
        proveedor: Any,
        *,
        fase: str,
        miembro: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        validar: Callable[[Any], T],
    ) -> _Resultado:
        """One call, strictly validated, with ONE repair attempt for an invalid output."""
        respuesta = self._llamar(
            s, proveedor, fase=fase, miembro=miembro, system=system, user=user, schema=schema, schema_name=schema_name
        )
        if respuesta.presupuesto:
            return self._anotado(s, fase, miembro, _Resultado(None, "presupuesto", respuesta.error))
        if respuesta.datos is not None:
            try:
                return self._anotado(s, fase, miembro, _Resultado(validar(respuesta.datos), "ok"))
            except SalidaInvalidaError as exc:
                motivo, anterior = str(exc), respuesta.datos
        elif respuesta.invalida:
            motivo, anterior = "no era un objeto JSON válido.", None
        else:
            return self._anotado(s, fase, miembro, _Resultado(None, "error", respuesta.error))
        reparacion = self._llamar(
            s,
            proveedor,
            fase=fase,
            miembro=miembro,
            system=system,
            user=prompts.mensaje_reparacion(user, motivo, anterior),
            schema=schema,
            schema_name=schema_name,
        )
        if reparacion.presupuesto:
            return self._anotado(s, fase, miembro, _Resultado(None, "presupuesto", reparacion.error))
        if reparacion.datos is None:
            return self._anotado(s, fase, miembro, _Resultado(None, "descartado", reparacion.error or motivo))
        try:
            return self._anotado(s, fase, miembro, _Resultado(validar(reparacion.datos), "reparado"))
        except SalidaInvalidaError as exc:
            return self._anotado(s, fase, miembro, _Resultado(None, "descartado", str(exc)))

    @staticmethod
    def _anotado(s: _Sesion, fase: str, miembro: str, resultado: _Resultado) -> _Resultado:
        with s.lock:
            s.registro.eventos_llamada.append({"fase": fase, "miembro": miembro, "estado": resultado.estado})
        return resultado

    def _en_paralelo(self, tareas: Sequence[tuple[str, Callable[[], T]]]) -> dict[str, T]:
        """Run ``tareas`` with at most ``max_paralelo`` threads, each in a copy of the caller's context; results by key,
        in the given order whatever the completion order."""
        if not tareas:
            return {}
        hilos = min(self._max_paralelo, len(tareas))
        if hilos == 1:
            return {clave: contextvars.copy_context().run(tarea) for clave, tarea in tareas}
        with ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="consejo") as pool:
            futuros = [(clave, pool.submit(contextvars.copy_context().run, tarea)) for clave, tarea in tareas]
            return {clave: futuro.result() for clave, futuro in futuros}

    # -- budget and cost cap -------------------------------------------------------------------------------------

    def _comprobar_presupuesto(self, s: _Sesion, siguiente: FaseConsejo) -> None:
        if s.presupuesto is not None:
            raise _ParadaError(
                siguiente,
                f"Se agotó el presupuesto de uso de modelos antes de {_NOMBRE_FASE[siguiente]}: {s.presupuesto}",
            )
        if s.tope is not None and s.contabilidad.usd >= s.tope:
            raise _ParadaError(
                siguiente, f"Se alcanzó el tope de coste de la sesión antes de {_NOMBRE_FASE[siguiente]}."
            )

    def _comprobar_tope(self, s: _Sesion, fase: FaseConsejo, previstas: Sequence[tuple[str, str, int]]) -> None:
        """Stop BEFORE ``fase`` when its projected cost would take the session over the cap."""
        self._comprobar_presupuesto(s, fase)
        if s.tope is None or not previstas:
            return
        proyeccion = s.contabilidad.proyectar(previstas, FACTOR_SALIDA.get(fase, Decimal(1)))
        if proyeccion is None:
            s.proyeccion_desconocida = True
            return
        gastado = s.contabilidad.usd
        if gastado + proyeccion > s.tope:
            raise _ParadaError(
                fase,
                f"Se detuvo antes de {_NOMBRE_FASE[fase]} para no superar el tope de coste que aceptaste "
                f"(gastado {gastado:.4f} USD, previsto para esa fase {proyeccion:.4f} USD, tope {s.tope:.4f} USD).",
            )

    def _prevista(self, s: _Sesion, miembro: str, proveedor: Any, system: str, user: str) -> tuple[str, str, int]:
        modelo = s.contabilidad.modelo_de(miembro) or str(getattr(proveedor, "model", "") or "")
        return str(getattr(proveedor, "name", "") or ""), modelo, len(system) + len(user)

    # -- phase 1 -------------------------------------------------------------------------------------------------

    def _ronda_propuestas(self, s: _Sesion) -> dict[str, tuple[Propuesta, list[str]]]:
        mensaje = prompts.mensaje_propuesta(s.expediente)

        def tarea(rol: Rol) -> Callable[[], _Resultado]:
            def ejecutar() -> _Resultado:
                s.emitir(EVENTO_MIEMBRO, miembro_id=rol.id, fase="propuestas", estado="inicio")
                resultado = self._pedir(
                    s,
                    s.proveedores[rol.id],
                    fase="propuestas",
                    miembro=rol.id,
                    system=prompts.sistema_miembro(rol),
                    user=mensaje,
                    schema=prompts.PROPUESTA_SCHEMA,
                    schema_name=prompts.SCHEMA_PROPUESTA,
                    validar=lambda datos: validar_propuesta(datos, miembro_id=rol.id, ronda=1, expediente=s.expediente),
                )
                s.emitir(EVENTO_MIEMBRO, miembro_id=rol.id, fase="propuestas", estado=resultado.estado)
                return resultado

            return ejecutar

        sentados = [rol for rol in self._roles if rol.id in s.proveedores]
        resultados = self._en_paralelo([(rol.id, tarea(rol)) for rol in sentados])
        validas: dict[str, tuple[Propuesta, list[str]]] = {}
        for rol in sentados:
            resultado = resultados[rol.id]
            if resultado.valor is not None:
                validas[rol.id] = resultado.valor
                continue
            motivo = resultado.motivo or "sin respuesta"
            s.registro.descartados[rol.id] = motivo
            if resultado.estado == "presupuesto":
                s.avisar(
                    f"«{rol.nombre}» no llegó a presentar su propuesta: se agotó el presupuesto de uso de modelos."
                )
            else:
                s.avisar(f"«{rol.nombre}» no presentó una propuesta válida y quedó fuera de la deliberación.")
        return validas

    def _puerta(
        self, s: _Sesion, propuesta: Propuesta, desconocidas: Sequence[str], *, con_preferencia: bool
    ) -> ResultadoPuerta:
        partes = [
            propuesta.razonamiento,
            propuesta.condicion or "",
            *propuesta.supuestos,
            *propuesta.riesgos,
            *propuesta.que_me_haria_cambiar,
            propuesta.cambios or "",
        ]
        resultado = revisar_cifras_consejo(
            "\n".join(p for p in partes if p),
            s.expediente,
            con_preferencia=con_preferencia,
            cifras_desconocidas=desconocidas,
        )
        s.registro.puertas[f"{propuesta.miembro_id}:{propuesta.ronda}"] = resultado
        return resultado

    # -- phase 2 -------------------------------------------------------------------------------------------------

    def _ronda_criticas(
        self,
        s: _Sesion,
        propuestas: Mapping[str, Propuesta],
        puertas: Mapping[str, ResultadoPuerta],
    ) -> tuple[dict[str, list[Critica]], dict[str, Ranking], dict[str, dict[str, str]]]:
        asientos = self._orden_asientos(propuestas)
        roles = {r.id: r for r in self._roles}
        if self._equilibrado:
            diseno = diseno_equilibrado(asientos, s.semilla, s.sesion_id)
        else:
            diseno = {a: orden_para(a, asientos, s.semilla, s.sesion_id) for a in asientos}
        lista = [propuestas[a] for a in asientos]
        planes: dict[str, tuple[str, str, dict[str, str]]] = {}
        for evaluador in asientos:
            anonimas, mapa = anonimizar_para(
                evaluador, lista, s.semilla, sesion_id=s.sesion_id, orden=diseno[evaluador], limpiador=self._limpiador
            )
            comprobaciones = {e: describir_violaciones(puertas.get(a)) for e, a in mapa.items()}
            sistema = prompts.sistema_critica(roles[evaluador])
            planes[evaluador] = (sistema, prompts.mensaje_criticas(s.expediente, anonimas, comprobaciones), mapa)
            s.registro.presentaciones[evaluador] = dict(mapa)
        self._comprobar_tope(
            s,
            "criticas",
            [
                self._prevista(s, ev, s.proveedores[ev], sistema, mensaje)
                for ev, (sistema, mensaje, _m) in planes.items()
            ],
        )
        s.emitir(EVENTO_FASE, fase="criticas", estado="inicio")

        def tarea(evaluador: str) -> Callable[[], _Resultado]:
            sistema, mensaje, mapa = planes[evaluador]

            def ejecutar() -> _Resultado:
                s.emitir(EVENTO_MIEMBRO, miembro_id=evaluador, fase="criticas", estado="inicio")
                resultado = self._pedir(
                    s,
                    s.proveedores[evaluador],
                    fase="criticas",
                    miembro=evaluador,
                    system=sistema,
                    user=mensaje,
                    schema=prompts.CRITICAS_SCHEMA,
                    schema_name=prompts.SCHEMA_CRITICAS,
                    validar=lambda datos: validar_criticas(datos, evaluador_id=evaluador, etiquetas=list(mapa)),
                )
                s.emitir(EVENTO_MIEMBRO, miembro_id=evaluador, fase="criticas", estado=resultado.estado)
                return resultado

            return ejecutar

        resultados = self._en_paralelo([(ev, tarea(ev)) for ev in asientos])
        criticas: dict[str, list[Critica]] = {}
        rankings: dict[str, Ranking] = {}
        for evaluador in asientos:
            resultado = resultados[evaluador]
            if resultado.valor is None:
                if resultado.estado == "presupuesto":
                    s.avisar(
                        f"Las críticas de «{roles[evaluador].nombre}» no llegaron a hacerse: se agotó el presupuesto "
                        "de uso de modelos."
                    )
                else:
                    s.avisar(
                        f"Las críticas de «{roles[evaluador].nombre}» no se pudieron usar; su propuesta sí cuenta."
                    )
                continue
            criticas[evaluador], rankings[evaluador] = resultado.valor
        s.emitir(EVENTO_FASE, fase="criticas", estado="fin", validas=len(rankings))
        return criticas, rankings, {ev: planes[ev][2] for ev in asientos}

    # -- phase 3 -------------------------------------------------------------------------------------------------

    def _ronda_revision(
        self,
        s: _Sesion,
        propuestas: Mapping[str, Propuesta],
        puertas: Mapping[str, ResultadoPuerta],
        criticas: Mapping[str, Sequence[Critica]],
        mapas: Mapping[str, Mapping[str, str]],
        roles: Mapping[str, Rol],
    ) -> tuple[dict[str, Propuesta], dict[str, ResultadoPuerta]]:
        asientos = self._orden_asientos(propuestas)
        votacion = consenso_de([propuestas[a].recomendacion for a in asientos])
        lider = votacion.lideres[0] if len(votacion.lideres) == 1 else None
        planes: dict[str, tuple[str, str]] = {}
        for asiento in asientos:
            recibidas = [
                self._vista_critica(c)
                for evaluador in self._orden_asientos(criticas)
                for c in criticas[evaluador]
                if mapas[evaluador].get(c.etiqueta) == asiento
            ]
            generador(s.semilla, s.sesion_id, "revision", asiento).shuffle(recibidas)
            propia = self._vista_propia(propuestas[asiento])
            mensaje = prompts.mensaje_revision(
                s.expediente,
                propia,
                recibidas,
                describir_violaciones(puertas.get(asiento)),
                opcion_lider=lider if asiento in ROLES_CONTRARIOS else None,
            )
            planes[asiento] = (prompts.sistema_revision(roles[asiento]), mensaje)
        self._comprobar_tope(
            s,
            "revision",
            [self._prevista(s, a, s.proveedores[a], sistema, mensaje) for a, (sistema, mensaje) in planes.items()],
        )
        s.emitir(EVENTO_FASE, fase="revision", estado="inicio")

        def tarea(asiento: str) -> Callable[[], _Resultado]:
            sistema, mensaje = planes[asiento]

            def ejecutar() -> _Resultado:
                s.emitir(EVENTO_MIEMBRO, miembro_id=asiento, fase="revision", estado="inicio")
                resultado = self._pedir(
                    s,
                    s.proveedores[asiento],
                    fase="revision",
                    miembro=asiento,
                    system=sistema,
                    user=mensaje,
                    schema=prompts.PROPUESTA_REVISADA_SCHEMA,
                    schema_name=prompts.SCHEMA_PROPUESTA_REVISADA,
                    validar=lambda datos: validar_propuesta(
                        datos, miembro_id=asiento, ronda=2, expediente=s.expediente
                    ),
                )
                s.emitir(EVENTO_MIEMBRO, miembro_id=asiento, fase="revision", estado=resultado.estado)
                return resultado

            return ejecutar

        resultados = self._en_paralelo([(a, tarea(a)) for a in asientos])
        revisadas: dict[str, Propuesta] = {}
        puertas_r2: dict[str, ResultadoPuerta] = {}
        for asiento in asientos:
            resultado = resultados[asiento]
            if resultado.valor is None:
                if resultado.estado == "presupuesto":
                    s.avisar(
                        f"«{roles[asiento].nombre}» no llegó a revisar su propuesta (se agotó el presupuesto de uso de "
                        "modelos): cuenta la de la primera ronda."
                    )
                else:
                    s.avisar(f"«{roles[asiento].nombre}» no pudo revisar su propuesta: cuenta la de la primera ronda.")
                continue
            propuesta, desconocidas = resultado.valor
            revisadas[asiento] = propuesta
            puertas_r2[asiento] = self._puerta(s, propuesta, desconocidas, con_preferencia=True)
        s.emitir(EVENTO_FASE, fase="revision", estado="fin", validas=len(revisadas))
        return revisadas, puertas_r2

    def _vista_critica(self, critica: Critica) -> dict[str, Any]:
        """A critique as its addressee reads it: no reviewer, no label, identity markers stripped."""
        limpiar = self._limpiador
        return {
            "fortalezas": [t for t in (limpiar(x, 400) for x in critica.fortalezas) if t],
            "debilidades": [t for t in (limpiar(x, 400) for x in critica.debilidades) if t],
            "errores": [{"tipo": e.tipo, "texto": limpiar(e.texto, 500)} for e in critica.errores],
            "puntuacion": critica.puntuacion.model_dump(),
        }

    @staticmethod
    def _vista_propia(propuesta: Propuesta) -> dict[str, Any]:
        return {
            "recomendacion": propuesta.recomendacion,
            "condicion": propuesta.condicion or "",
            "confianza": propuesta.confianza,
            "razonamiento": propuesta.razonamiento,
            "supuestos": list(propuesta.supuestos),
            "riesgos": list(propuesta.riesgos),
            "que_me_haria_cambiar": list(propuesta.que_me_haria_cambiar),
            "cifras_usadas": list(propuesta.cifras_usadas),
        }

    # -- phase 4: the ruling -------------------------------------------------------------------------------------

    def _dictamen(
        self,
        s: _Sesion,
        posiciones: Mapping[str, Propuesta],
        agregacion: Agregacion | None,
        criticas: Mapping[str, Sequence[Critica]],
        mapas: Mapping[str, Mapping[str, str]],
        puertas: Mapping[str, ResultadoPuerta],
        revisadas: Mapping[str, Propuesta],
        *,
        parada: _ParadaError | None,
    ) -> tuple[Any, _ParadaError | None]:
        """The ruling, and the stop that made it partial (if any)."""
        expediente = s.expediente
        roles = {r.id: r for r in self._roles}
        asientos = self._orden_asientos(posiciones)
        votacion = consenso_de([posiciones[a].recomendacion for a in asientos])
        acuerdo = agregacion.acuerdo if agregacion is not None else None
        lider = votacion.lideres[0] if votacion.lideres else votacion.recomendacion
        confianza = confianza_del_consejo(
            [posiciones[a].confianza for a in asientos if posiciones[a].recomendacion == lider],
            votacion.cuota,
            acuerdo,
        )
        calculado = ResultadoCalculado(
            votacion=votacion,
            confianza=confianza,
            acuerdo=acuerdo,
            posiciones={a: posiciones[a] for a in asientos},
            orden_agregado=list(agregacion.orden) if agregacion is not None else [],
            atipicos=list(agregacion.atipicos) if agregacion is not None else [],
            empate_en_cabeza=bool(agregacion.empate_en_cabeza) if agregacion is not None else False,
        )
        # The ruling's public labels: the chair's view (also when there is no chair, for the stored critiques).
        anonimas, mapa_publico = anonimizar_para(
            None, [posiciones[a] for a in asientos], s.semilla, sesion_id=s.sesion_id, limpiador=self._limpiador
        )
        s.registro.etiquetas_publicas = dict(mapa_publico)
        errores = {
            asiento: [
                e
                for evaluador in self._orden_asientos(criticas)
                for c in criticas[evaluador]
                if mapas[evaluador].get(c.etiqueta) == asiento
                for e in c.errores
            ]
            for asiento in asientos
        }
        comprobaciones = {a: describir_violaciones(puertas.get(a)) for a in asientos}
        avisos = self._avisos_de_deliberacion(s, posiciones, puertas, calculado, roles)

        miembros = [s.registro.asientos[a] for a in self._orden_asientos(s.registro.asientos)]
        redaccion: RedaccionPresidente | None = None
        proveedor: Any | None = None
        mensaje = ""
        sistema = prompts.sistema_presidente()
        if parada is None:
            mensaje = mensaje_presidente(
                expediente,
                anonimas,
                mapa_publico,
                calculado,
                cambios={a: revisadas[a].cambios or "" for a in revisadas},
                errores=errores,
                comprobaciones=comprobaciones,
            )
            try:
                proveedor = self._proveedor(s, self._presidente.proposito)
                self._comprobar_tope(s, "sintesis", [self._prevista(s, "presidente", proveedor, sistema, mensaje)])
            except _ParadaError as exc:
                parada = exc
                s.registro.parada = exc.motivo
            except Exception:
                logger.exception("council chair provider unavailable")
                avisos.append("No se pudo contactar con el presidente del consejo.")
                proveedor = None
        primeros: list[str] = []
        if parada is not None:
            primeros.append(f"Dictamen parcial: {parada.motivo}")
            redaccion = redaccion_determinista(
                expediente,
                calculado,
                motivo=(
                    "Este dictamen es parcial: el consejo se detuvo antes de "
                    f"{_NOMBRE_FASE.get(parada.fase, 'terminar')} y lo resume el código a partir de las propuestas, "
                    "sin la síntesis del presidente."
                ),
                limpiar=self._limpiador,
            )
        else:
            if proveedor is not None:
                miembros.append(miembro_de(self._presidente, proveedor))
                redaccion, avisos_presidente = self._presidir(s, proveedor, sistema, mensaje, calculado)
                avisos += avisos_presidente
            if redaccion is None:
                redaccion = redaccion_determinista(
                    expediente,
                    calculado,
                    motivo=(
                        "El presidente no pudo redactar la síntesis: este resumen lo escribe el código a partir de las "
                        "propuestas finales de los miembros."
                    ),
                    limpiar=self._limpiador,
                )
        redaccion, puerta, avisos_puerta = self._puerta_dictamen(s, redaccion)
        avisos += avisos_puerta
        if s.contabilidad.sin_coste:
            avisos.append(
                f"El coste de {s.contabilidad.sin_coste} llamadas no se conoce (el proveedor no informó del uso o el "
                "modelo no tiene precio en la tabla): el tope de coste no las cuenta."
            )
        elif s.proyeccion_desconocida:
            avisos.append("No se pudo prever el coste de alguna fase: el tope solo se comprobó con lo ya gastado.")
        if parada is None:
            s.emitir(EVENTO_FASE, fase="sintesis", estado="fin")
        dictamen = componer_dictamen(
            s.sesion_id,
            expediente,
            calculado,
            redaccion,
            orden_asientos=[r.id for r in self._roles],
            miembros=miembros,
            autoria=mapa_publico,
            puerta=puerta,
            avisos=[*primeros, *s.avisos, *avisos],
        )
        return dictamen, parada

    def _avisos_de_deliberacion(
        self,
        s: _Sesion,
        posiciones: Mapping[str, Propuesta],
        puertas: Mapping[str, ResultadoPuerta],
        calculado: ResultadoCalculado,
        roles: Mapping[str, Rol],
    ) -> list[str]:
        avisos: list[str] = []
        for clave, puerta in s.registro.puertas.items():
            asiento, _sep, ronda = clave.partition(":")
            if puerta.violaciones and asiento in roles:
                literales = ", ".join(f"«{v.literal}»" for v in puerta.violaciones[:5])
                avisos.append(
                    f"La propuesta de «{roles[asiento].nombre}» (ronda {ronda}) usaba números que no salen del "
                    f"expediente: {literales}."
                )
        for atipico in calculado.atipicos:
            if atipico.evaluador_id in roles:
                avisos.append(
                    f"Las valoraciones de «{roles[atipico.evaluador_id].nombre}» se apartan mucho de las del resto "
                    f"(contradice a la mayoría en {round(atipico.distancia * atipico.pares)} de {atipico.pares} "
                    "comparaciones); se han tenido en cuenta igualmente."
                )
        if calculado.empate_en_cabeza:
            avisos.append("En el orden de las propuestas según las críticas cruzadas hay un empate en cabeza.")
        return avisos

    def _presidir(
        self, s: _Sesion, proveedor: Any, sistema: str, mensaje: str, calculado: ResultadoCalculado
    ) -> tuple[RedaccionPresidente | None, list[str]]:
        """The chair's call (one repair for an invalid output, one correction for the figures gate); None when it
        fails."""
        s.emitir(EVENTO_FASE, fase="sintesis", estado="inicio")
        s.emitir(EVENTO_MIEMBRO, miembro_id=self._presidente.id, fase="sintesis", estado="inicio")
        avisos: list[str] = []

        def validar(datos: Any) -> tuple[RedaccionPresidente, list[str], Any]:
            redaccion, ignorados = validar_redaccion(datos, expediente=s.expediente, calculado=calculado)
            return redaccion, ignorados, datos

        resultado = self._pedir(
            s,
            proveedor,
            fase="sintesis",
            miembro=self._presidente.id,
            system=sistema,
            user=mensaje,
            schema=prompts.DICTAMEN_SCHEMA,
            schema_name=prompts.SCHEMA_DICTAMEN,
            validar=validar,
        )
        s.emitir(EVENTO_MIEMBRO, miembro_id=self._presidente.id, fase="sintesis", estado=resultado.estado)
        if resultado.valor is None:
            return None, avisos
        redaccion, ignorados, datos = resultado.valor
        avisos += ignorados
        # The figures gate on the chair's draft: one correction round before anything is withdrawn.
        puerta = revisar_cifras_consejo("\n".join(redaccion.textos()), s.expediente, con_preferencia=True)
        if puerta.ok or s.presupuesto is not None:
            return redaccion, avisos
        correccion = self._pedir(
            s,
            proveedor,
            fase="sintesis",
            miembro=self._presidente.id,
            system=sistema,
            user=mensaje_correccion_presidente(mensaje, datos, describir_violaciones(puerta)),
            schema=prompts.DICTAMEN_SCHEMA,
            schema_name=prompts.SCHEMA_DICTAMEN,
            validar=validar,
        )
        if correccion.valor is not None:
            redaccion, ignorados, _datos = correccion.valor
            avisos += ignorados
        return redaccion, avisos

    def _puerta_dictamen(
        self, s: _Sesion, redaccion: RedaccionPresidente
    ) -> tuple[RedaccionPresidente, ResultadoPuerta, list[str]]:
        """The figures gate on the ruling's texts; what still fails is withdrawn (and the ruling says so)."""
        puerta = revisar_cifras_consejo("\n".join(redaccion.textos()), s.expediente, con_preferencia=True)
        if puerta.ok:
            return redaccion, puerta, []
        literales = ", ".join(f"«{v.literal}»" for v in puerta.violaciones[:6])
        retirada = redaccion.retirando(puerta.violaciones)
        final = revisar_cifras_consejo("\n".join(retirada.textos()), s.expediente, con_preferencia=True)
        return retirada, final, [f"Se retiraron del dictamen números que no salen del expediente: {literales}."]


def semilla_configurada() -> int | str | None:
    """``CONSEJO_SEMILLA`` (an integer, or any text), or None when empty."""
    texto = str(getattr(settings, "CONSEJO_SEMILLA", "") or "").strip()
    if not texto:
        return None
    return int(texto) if texto.lstrip("-").isdigit() else texto


def construir_motor() -> Deliberacion:
    """The production engine: the seats of ``CONSEJO_MIEMBROS``, providers per family (``CONSEJO_FAMILIAS``),
    metered per purpose, ``CONSEJO_MAX_PARALELO`` calls at once, ``CONSEJO_SEMILLA`` (or a per-session seed)."""
    roles = roles_configurados()
    return Deliberacion(
        roles,
        proveedor_para=FabricaProveedores(roles, presidente=PRESIDENTE),
        presidente=PRESIDENTE,
        max_paralelo=int(getattr(settings, "CONSEJO_MAX_PARALELO", 3) or 3),
        semilla=semilla_configurada(),
    )


__all__ = [
    "FACTOR_SALIDA",
    "MARGEN_PROYECCION",
    "Contabilidad",
    "Deliberacion",
    "RegistroDeliberacion",
    "construir_motor",
    "observar_usos",
    "semilla_configurada",
    "validar_criticas",
    "validar_propuesta",
]

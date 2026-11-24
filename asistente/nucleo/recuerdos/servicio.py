"""The conversation memory, on Hindsight, as the brain, the API and the account erasure use it.

Whose memory: one Hindsight bank per owner — (case, account), ``''`` for an absent part — named by a hash of both
(:func:`banco_de`), so no request can reach another owner's bank and Hindsight never sees a case or user id.

* The brain (:class:`ServicioMemoria` as :class:`~asistente.nucleo.cerebro.contrato.ProveedorMemoria`):
  ``recuperar`` is one Hindsight recall (facts, experiences and consolidated observations, ranked by its own
  semantic, BM25, graph and temporal arms and reranker); ``registrar_turno`` retains the USER's words of the turn,
  asynchronously (Hindsight extracts the facts with its own model in the background). Neither ever raises nor
  waits for the embedded daemon to start: a memory that is not ready degrades to «no memory» for that turn.
* What is retained: only what the user wrote, after the deterministic privacy gates of
  :mod:`asistente.nucleo.privacidad` (no credentials, identifiers masked, nothing pasted, no special-category data
  unless the user asked to remember it). The assistant's answers are never retained, so nothing the assistant said
  becomes «something you told me», and every fact's source chunk is the user's own words.
* The user (``/recuerdos``, ``/conversaciones``): list what is remembered with its origin, correct a fact (the
  correction is retained as the user's words and the old fact is invalidated), forget one (its source message and
  everything extracted from it are deleted), forget everything (the bank is deleted), pause (nothing retained or
  recalled; nothing deleted). Each user message is its own Hindsight document, tagged with its conversation.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
import hashlib
import logging
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from asistente.nucleo import privacidad
from asistente.nucleo.cerebro.contrato import (
    BloqueMemoria,
    MemoriaNula,
    OrigenRecuerdo,
    ProveedorMemoria,
    RecuerdoMemoria,
    TipoRecuerdo,
    TurnoConversacion,
)
from asistente.nucleo.recuerdos import daemon
from asistente.nucleo.recuerdos.cliente import (
    TIPO_OBSERVACION,
    TIPOS_HECHO,
    ClienteHindsight,
    HindsightError,
    NoEncontradoError,
)
from asistente.nucleo.recuerdos.pausa import AlmacenPausa
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

# A message shorter than this (after the gates) carries no fact worth a model call.
MIN_MENSAJE = 12
MAX_CITA = 400
MAX_TEXTO = 600
ETIQUETA_CONVERSACION = "conversacion:"
CONTEXTO_MENSAJE = "Mensaje que la persona usuaria escribió a su asistente personal en una conversación."
CONTEXTO_CORRECCION = "Corrección que la persona usuaria hizo de algo que el asistente recordaba de ella."

TIPO_DE_HINDSIGHT: dict[str, TipoRecuerdo] = {"world": "hecho", "experience": "vivencia", "observation": "observacion"}

PERFIL_BANCO: dict[str, Any] = {
    "name": "Memoria de conversaciones",
    "mission": (
        "Memoria de una persona y su hogar para su asistente personal: su situación, su dinero, su casa, su "
        "familia, sus planes y cómo prefiere las cosas. Todo en español."
    ),
    "retain_mission": (
        "Extrae solo lo que la persona dice de sí misma, de su hogar y de su vida: datos, situaciones, planes, "
        "decisiones y preferencias, con fechas y cifras exactas tal como las escribe. No extraigas preguntas "
        "genéricas sin datos personales ni opiniones del asistente. Escribe los hechos en español."
    ),
    "observations_mission": (
        "Consolida lo que se sabe de la persona: si un dato nuevo sustituye a uno anterior (otro sueldo, otro "
        "banco, otra casa), deja claro cuál está vigente y desde cuándo."
    ),
    "enable_observations": True,
}
# Hindsight's own regex scrubber, on top of the gates applied here before anything is sent.
CONFIG_BANCO: dict[str, Any] = {
    "memory_defense": {"enabled": True, "rules": [{"on": "sensitive_data", "action": "redact"}]},
}


class CorreccionInvalidaError(ValueError):
    """The correction cannot be stored (nothing left after the privacy gates, or the item is not a fact)."""


# -- owners --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Titular:
    """Whose memory: (case, account); ``''`` for an absent part."""

    case_id: str
    user_id: str

    @classmethod
    def de(cls, case_id: str | None, user_id: str | None) -> Titular:
        return cls(case_id or "", user_id or "")

    @property
    def vacio(self) -> bool:
        return not self.case_id and not self.user_id

    @property
    def banco(self) -> str:
        return banco_de(self.case_id, self.user_id)


def banco_de(case_id: str | None, user_id: str | None) -> str:
    """The Hindsight bank of an owner: a hash, so the bank id reveals neither the case nor the account."""
    huella = hashlib.sha256(f"{case_id or ''}\x1f{user_id or ''}".encode()).hexdigest()[:40]
    return f"cerebro-{huella}"


# -- what the user sees --------------------------------------------------------------------------------------


@dataclass
class Recuerdo:
    id: str
    tipo: TipoRecuerdo
    texto: str
    conversacion_id: str | None
    fecha: datetime | None
    documento_id: str | None
    corregido: bool = False


@dataclass
class Mensaje:
    documento_id: str
    texto: str
    fecha: datetime | None


@dataclass
class Conversacion:
    id: str
    titulo: str
    creada: datetime | None
    actualizada: datetime | None
    mensajes: list[Mensaje] = field(default_factory=list)


def _fecha(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _dia(valor: Any) -> date | None:
    momento = _fecha(valor)
    return momento.date() if momento else None


def _recortar(texto: str, maximo: int) -> str:
    texto = " ".join(str(texto).split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def preparar(texto: str) -> str | None:
    """The user's own words of a message as the memory may keep them, or None when nothing should be retained."""
    propias = " ".join(privacidad.quitar_pegado(texto).split())
    if privacidad.tiene_credencial(propias):
        # Conservative on purpose: a message that carries a password, PIN or key is not remembered at all.
        return None
    if privacidad.es_inyeccion(propias):
        propias = privacidad.neutralizar(propias)
    limpio = privacidad.limpiar(propias)
    if len(limpio) < MIN_MENSAJE or not limpio.replace(privacidad.SECRETO, "").strip(" .,;:"):
        return None
    if privacidad.categoria_especial(limpio) is not None and not privacidad.pidio_recordar(limpio, [limpio]):
        return None
    return limpio


def _conversacion_de(fila: dict[str, Any]) -> str | None:
    metadatos = fila.get("metadata") or fila.get("document_metadata") or {}
    if isinstance(metadatos, dict) and metadatos.get("conversacion_id"):
        return str(metadatos["conversacion_id"])
    for etiqueta in fila.get("tags") or []:
        if isinstance(etiqueta, str) and etiqueta.startswith(ETIQUETA_CONVERSACION):
            return etiqueta[len(ETIQUETA_CONVERSACION) :]
    return None


def _recuerdo(fila: dict[str, Any]) -> Recuerdo | None:
    tipo = TIPO_DE_HINDSIGHT.get(str(fila.get("fact_type") or fila.get("type") or ""))
    texto = str(fila.get("text") or "").strip()
    if tipo is None or not texto:
        return None
    return Recuerdo(
        id=str(fila["id"]),
        tipo=tipo,
        texto=_recortar(texto, MAX_TEXTO),
        conversacion_id=_conversacion_de(fila),
        fecha=_fecha(fila.get("mentioned_at")) or _fecha(fila.get("date")),
        documento_id=fila.get("document_id"),
        corregido=bool((fila.get("metadata") or {}).get("corrige")),
    )


# -- the service ---------------------------------------------------------------------------------------------


class ServicioMemoria:
    """The memory of every owner, over one Hindsight server. Thread-safe; one per process (:func:`servicio`)."""

    def __init__(
        self,
        db_path: str,
        *,
        fabrica_cliente: Callable[[], ClienteHindsight] | None = None,
        listo: Callable[[], bool] | None = None,
    ) -> None:
        self.db_path = str(Path(db_path))
        self.pausa = AlmacenPausa(self.db_path)
        self._fabrica = fabrica_cliente or (
            lambda: ClienteHindsight(daemon.url(), timeout=float(settings.HINDSIGHT_TIMEOUT_S))
        )
        self._listo = listo or daemon.listo
        self._cliente: ClienteHindsight | None = None
        self._bancos: set[str] = set()
        self._lock = threading.Lock()

    # -- plumbing ----------------------------------------------------------------------------------------------

    def cliente(self) -> ClienteHindsight:
        """The Hindsight client. Never waits for the embedded daemon: until it is up this raises HindsightError (the
        API answers 503, a turn is not retained) after asking for it to start in the background."""
        with self._lock:
            if self._cliente is not None:
                return self._cliente
        if not self._disponible_ya():
            raise HindsightError("Hindsight todavía no está listo")
        try:
            nuevo = self._fabrica()
        except HindsightError:
            raise
        except Exception as exc:
            raise HindsightError(f"Hindsight no se pudo usar ({type(exc).__name__}: {exc})") from exc
        with self._lock:
            if self._cliente is None:
                self._cliente = nuevo
            return self._cliente

    def _asegurar_banco(self, titular: Titular) -> ClienteHindsight:
        cliente = self.cliente()
        if titular.banco not in self._bancos:
            cliente.crear_banco(titular.banco, perfil=PERFIL_BANCO, config=CONFIG_BANCO)
            with self._lock:
                self._bancos.add(titular.banco)
        return cliente

    def _disponible_ya(self) -> bool:
        """Hindsight answers now; if the embedded daemon is not up yet, start it in the background and say no."""
        if self._listo():
            return True
        daemon.calentar()
        return False

    # -- the brain (ProveedorMemoria) ----------------------------------------------------------------------------

    def recuperar(
        self,
        *,
        case_id: str | None,
        user_id: str | None,
        consulta: str,
        conversacion_id: str | None = None,
        limite: int = 8,
    ) -> BloqueMemoria:
        if not settings.MEMORIA_CONVERSACIONES:
            return BloqueMemoria(vacio_por="desactivada")
        titular = Titular.de(case_id, user_id)
        if titular.vacio:
            return BloqueMemoria(vacio_por="sin_cuenta")
        try:
            if self.pausa.pausada(case_id, user_id):
                return BloqueMemoria(vacio_por="desactivada")
            if not self._disponible_ya():
                return BloqueMemoria(vacio_por="no_disponible")
            datos = self.cliente().recordar(
                titular.banco,
                consulta[:4000],
                presupuesto=settings.HINDSIGHT_PRESUPUESTO,
                max_tokens=2048,
                momento=datetime.now(UTC),
                timeout=float(settings.HINDSIGHT_TIMEOUT_S),
            )
        except NoEncontradoError:
            return BloqueMemoria(vacio_por="sin_resultados")
        except Exception:
            logger.exception("conversation memory: recall failed")
            return BloqueMemoria(vacio_por="no_disponible")
        recuerdos = _de_recall(datos, excluir=conversacion_id, limite=max(1, min(limite, 30)))
        return BloqueMemoria(recuerdos=recuerdos, vacio_por=None if recuerdos else "sin_resultados")

    def registrar_turno(self, turno: TurnoConversacion) -> None:
        if not settings.MEMORIA_CONVERSACIONES:
            return
        titular = Titular.de(turno.case_id, turno.user_id)
        if titular.vacio:
            return
        try:
            if self.pausa.pausada(turno.case_id, turno.user_id):
                return
            self.sembrar(
                titular,
                texto=turno.pregunta,
                conversacion_id=turno.conversacion_id,
                fecha=turno.fecha,
                documento_id=f"msg-{turno.conversacion_id}-{turno.fecha.strftime('%Y%m%dT%H%M%S%f')}",
                asincrono=True,
            )
        except Exception:
            logger.exception("conversation memory: the turn could not be retained")

    def sembrar(
        self,
        titular: Titular,
        *,
        texto: str,
        conversacion_id: str,
        fecha: datetime,
        documento_id: str,
        asincrono: bool = False,
    ) -> bool:
        """Retain one user message (after the privacy gates) as its own document. False when nothing was kept."""
        limpio = preparar(texto)
        if limpio is None:
            return False
        self._asegurar_banco(titular).retener(
            titular.banco,
            contenido=limpio,
            documento_id=documento_id,
            fecha=fecha,
            contexto=CONTEXTO_MENSAJE,
            etiquetas=[ETIQUETA_CONVERSACION + conversacion_id],
            metadatos={"conversacion_id": conversacion_id},
            asincrono=asincrono,
        )
        return True

    def olvidar_banco(self, banco: str) -> None:
        """Forget that a bank was set up (after deleting it elsewhere), so the next write creates it again."""
        with self._lock:
            self._bancos.discard(banco)

    # -- the user ----------------------------------------------------------------------------------------------

    def activa(self, titular: Titular) -> bool:
        return not self.pausa.pausada(titular.case_id, titular.user_id)

    def fijar_activa(self, titular: Titular, activa: bool) -> None:
        self.pausa.fijar(titular.case_id, titular.user_id, pausada=not activa)

    def listar(self, titular: Titular) -> list[Recuerdo]:
        """What is remembered about the owner: the facts and experiences, newest first (observations are derived
        from them and change with them, so they are not listed one by one)."""
        cliente = self.cliente()
        filas = [f for tipo in TIPOS_HECHO for f in cliente.listar_memorias(titular.banco, tipo=tipo)]
        recuerdos = [r for r in map(_recuerdo, filas) if r is not None]
        return sorted(recuerdos, key=lambda r: r.fecha or datetime.min.replace(tzinfo=UTC), reverse=True)

    def corregir(self, titular: Titular, recuerdo_id: str, *, texto: str) -> list[Recuerdo] | None:
        """Retain the user's correction as their own words and invalidate the fact it corrects. Returns the facts
        Hindsight extracted from the correction; None when the item does not exist in this owner's bank."""
        cliente = self.cliente()
        fila = cliente.memoria(titular.banco, recuerdo_id)
        if fila is None:
            return None
        if str(fila.get("fact_type") or "") == TIPO_OBSERVACION:
            raise CorreccionInvalidaError("Eso es una conclusión de la memoria, no algo que dijeras: corrige el dato.")
        limpio = preparar(texto)
        if limpio is None:
            raise CorreccionInvalidaError("La corrección está vacía o solo contenía datos que no se guardan.")
        documento = f"corr-{uuid4().hex}"
        conversacion = _conversacion_de(fila)
        etiquetas = [ETIQUETA_CONVERSACION + conversacion] if conversacion else []
        metadatos = {"corrige": recuerdo_id, **({"conversacion_id": conversacion} if conversacion else {})}
        cliente.retener(
            titular.banco,
            contenido=limpio,
            documento_id=documento,
            fecha=datetime.now(UTC),
            contexto=CONTEXTO_CORRECCION,
            etiquetas=etiquetas,
            metadatos=metadatos,
            asincrono=False,
        )
        cliente.invalidar(titular.banco, recuerdo_id, motivo="corregido por la persona usuaria")
        nuevos = [r for r in map(_recuerdo, cliente.listar_memorias(titular.banco, documento_id=documento)) if r]
        for r in nuevos:
            r.corregido = True
        return nuevos

    def olvidar(self, titular: Titular, recuerdo_id: str) -> bool:
        """Forget an item: delete the message it was extracted from, with everything Hindsight learnt from it."""
        cliente = self.cliente()
        fila = cliente.memoria(titular.banco, recuerdo_id)
        if fila is None or str(fila.get("fact_type") or "") == TIPO_OBSERVACION:
            return False
        documento = fila.get("document_id")
        if documento:
            cliente.borrar_documento(titular.banco, str(documento))
        else:
            cliente.invalidar(titular.banco, recuerdo_id, motivo="olvidado por la persona usuaria")
        return True

    def olvidar_todo(self, titular: Titular) -> dict[str, int]:
        cliente = self.cliente()
        recuerdos = len(self.listar(titular))
        conversaciones = len(self.conversaciones(titular))
        cliente.borrar_banco(titular.banco)
        with self._lock:
            self._bancos.discard(titular.banco)
        return {"recuerdos": recuerdos, "conversaciones": conversaciones}

    def conversaciones(self, titular: Titular) -> list[Conversacion]:
        """The owner's conversations, rebuilt from the retained messages (one Hindsight document each)."""
        por_id: dict[str, Conversacion] = {}
        for doc in self.cliente().listar_documentos(titular.banco):
            conversacion_id = _conversacion_de(doc)
            if not conversacion_id:
                continue
            creado = _fecha(doc.get("created_at"))
            c = por_id.setdefault(conversacion_id, Conversacion(conversacion_id, "", creado, creado))
            c.mensajes.append(Mensaje(str(doc["id"]), "", creado))
            if creado and (c.creada is None or creado < c.creada):
                c.creada = creado
            if creado and (c.actualizada is None or creado > c.actualizada):
                c.actualizada = creado
        return sorted(por_id.values(), key=lambda c: c.actualizada or datetime.min.replace(tzinfo=UTC), reverse=True)

    def conversacion(self, titular: Titular, conversacion_id: str) -> tuple[Conversacion, list[Recuerdo]] | None:
        """One conversation with the user's retained messages and what was learnt from them."""
        cliente = self.cliente()
        docs = cliente.listar_documentos(titular.banco, etiquetas=[ETIQUETA_CONVERSACION + conversacion_id])
        if not docs:
            return None
        mensajes: list[Mensaje] = []
        recuerdos: list[Recuerdo] = []
        for doc in docs:
            detalle = cliente.documento(titular.banco, str(doc["id"])) or {}
            mensajes.append(
                Mensaje(str(doc["id"]), str(detalle.get("original_text") or ""), _fecha(doc.get("created_at")))
            )
            for fila in cliente.listar_memorias(titular.banco, documento_id=str(doc["id"])):
                r = _recuerdo(fila)
                if r is not None and r.tipo != "observacion":
                    recuerdos.append(r)
        mensajes.sort(key=lambda m: m.fecha or datetime.min.replace(tzinfo=UTC))
        primera = next((m.texto for m in mensajes if m.texto), "")
        conversacion = Conversacion(
            id=conversacion_id,
            titulo=_recortar(primera, 80) or "Conversación",
            creada=mensajes[0].fecha if mensajes else None,
            actualizada=mensajes[-1].fecha if mensajes else None,
            mensajes=mensajes,
        )
        return conversacion, recuerdos

    def borrar_conversacion(self, titular: Titular, conversacion_id: str) -> bool:
        cliente = self.cliente()
        docs = cliente.listar_documentos(titular.banco, etiquetas=[ETIQUETA_CONVERSACION + conversacion_id])
        for doc in docs:
            cliente.borrar_documento(titular.banco, str(doc["id"]))
        return bool(docs)

    def titulos(self, titular: Titular, conversaciones: Sequence[Conversacion]) -> dict[str, str]:
        """A title per conversation: the start of its first message (one document read each)."""
        cliente = self.cliente()
        salida: dict[str, str] = {}
        for c in conversaciones:
            primero = min(c.mensajes, key=lambda m: m.fecha or datetime.min.replace(tzinfo=UTC), default=None)
            detalle = cliente.documento(titular.banco, primero.documento_id) if primero else None
            salida[c.id] = _recortar(str((detalle or {}).get("original_text") or ""), 80) or "Conversación"
        return salida


def _de_recall(datos: dict[str, Any], *, excluir: str | None, limite: int) -> list[RecuerdoMemoria]:
    """Hindsight's ranked results as the brain's items: the quote is the source chunk (the user's words)."""
    trozos = datos.get("chunks") or {}
    filas = [f for f in datos.get("results") or [] if isinstance(f, dict)]
    salida: list[RecuerdoMemoria] = []
    for fila in filas:
        recuerdo = _recuerdo(fila)
        if recuerdo is None or (excluir and recuerdo.conversacion_id == excluir):
            continue
        cita = None
        if recuerdo.tipo != "observacion":
            trozo = trozos.get(str(fila.get("chunk_id") or "")) or {}
            cita = _recortar(str(trozo.get("text") or ""), MAX_CITA) or None
        salida.append(
            RecuerdoMemoria(
                id=recuerdo.id,
                tipo=recuerdo.tipo,
                texto=recuerdo.texto,
                origen=OrigenRecuerdo(
                    conversacion_id=recuerdo.conversacion_id,
                    fecha=recuerdo.fecha.date() if recuerdo.fecha else None,
                    cita=cita,
                ),
                relevancia=round(1.0 - len(salida) / max(len(filas), 1), 3),
            )
        )
        if len(salida) >= limite:
            break
    return salida


# -- process-wide ----------------------------------------------------------------------------------------------

_servicios: dict[str, ServicioMemoria] = {}
_servicios_lock = threading.Lock()
# Where the process-wide services reach Hindsight: None = the configured server or the embedded daemon. Tests install
# an in-memory fake here (``configurar_hindsight``).
_fabrica_global: Callable[[], ClienteHindsight] | None = None
_listo_global: Callable[[], bool] | None = None


def configurar_hindsight(
    fabrica: Callable[[], ClienteHindsight] | None, *, listo: Callable[[], bool] | None = None
) -> None:
    """Make every process-wide service use ``fabrica`` for its Hindsight client (None: back to the default)."""
    global _fabrica_global, _listo_global
    with _servicios_lock:
        _fabrica_global = fabrica
        _listo_global = listo
        _servicios.clear()


def servicio(db_path: str | None = None) -> ServicioMemoria:
    """The process-wide service for ``db_path`` (default: the cases' database), whatever the switch says."""
    ruta = str(Path(db_path or settings.DECISION_STATE_DB_PATH))
    with _servicios_lock:
        if ruta not in _servicios:
            _servicios[ruta] = ServicioMemoria(ruta, fabrica_cliente=_fabrica_global, listo=_listo_global)
        return _servicios[ruta]


def servicio_memoria(db_path: str | None = None) -> ProveedorMemoria:
    """What the brain gets: the Hindsight memory, or :class:`MemoriaNula` with ``MEMORIA_CONVERSACIONES`` off."""
    if not settings.MEMORIA_CONVERSACIONES:
        return MemoriaNula()
    if _fabrica_global is None:
        daemon.calentar()
    return servicio(db_path)


def calentar_memoria() -> None:
    """Start the embedded daemon in the background (the API's start-up). Nothing with a test client installed."""
    if _fabrica_global is None:
        daemon.calentar()


def olvidar_servicios() -> None:
    """Drop the process-wide services (tests)."""
    with _servicios_lock:
        _servicios.clear()


def exportar_recuerdos(db_path: str, *, case_id: str | None, user_id: str | None) -> dict[str, Any]:
    """Everything the memory holds about one owner, for the data export (``GET /me/export``): the retained messages,
    the facts and the observations Hindsight consolidated. If Hindsight cannot be reached the rest of the export still
    goes out, and this part says so instead of pretending to be empty."""
    titular = Titular.de(case_id, user_id)
    s = servicio(db_path)
    salida: dict[str, Any] = {"motor": "hindsight", "pausada": not s.activa(titular)}
    try:
        conversaciones = []
        for c in s.conversaciones(titular):
            detalle = s.conversacion(titular, c.id)
            if detalle is not None:
                conv, _ = detalle
                conversaciones.append(
                    {
                        "id": conv.id,
                        "mensajes": [
                            {"texto": m.texto, "fecha": m.fecha.isoformat() if m.fecha else None} for m in conv.mensajes
                        ],
                    }
                )
        observaciones = [
            r for r in map(_recuerdo, s.cliente().listar_memorias(titular.banco, tipo=TIPO_OBSERVACION)) if r
        ]
        salida["recuerdos"] = [
            {
                "id": r.id,
                "tipo": r.tipo,
                "texto": r.texto,
                "conversacion_id": r.conversacion_id,
                "fecha": r.fecha.isoformat() if r.fecha else None,
            }
            for r in [*s.listar(titular), *observaciones]
        ]
        salida["conversaciones"] = conversaciones
    except HindsightError:
        logger.exception("conversation memory: export failed")
        salida["error"] = (
            "La memoria no respondía al exportar: vuelve a pedir la exportación en unos minutos para incluirla."
        )
    return salida


def borrar_de_usuario(db_path: str, *, user_id: str, case_ids: Sequence[str]) -> int:
    """Delete every bank of an account: the one outside any case and one per case (how many were deleted; deleting
    a bank that never existed is harmless). Raises if Hindsight cannot be reached, so the account erasure stops before
    the account goes and can be asked for again."""
    s = servicio(db_path)
    cliente = s.cliente()
    bancos = [banco_de(case_id, user_id) for case_id in [None, *case_ids]]
    for banco in bancos:
        cliente.borrar_banco(banco)
        s.olvidar_banco(banco)
    s.pausa.borrar_de_usuario(user_id)
    return len(bancos)


__all__ = [
    "CONFIG_BANCO",
    "PERFIL_BANCO",
    "Conversacion",
    "CorreccionInvalidaError",
    "Mensaje",
    "Recuerdo",
    "ServicioMemoria",
    "Titular",
    "banco_de",
    "borrar_de_usuario",
    "calentar_memoria",
    "configurar_hindsight",
    "exportar_recuerdos",
    "olvidar_servicios",
    "preparar",
    "servicio",
    "servicio_memoria",
]

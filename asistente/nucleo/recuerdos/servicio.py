"""The conversation memory as the brain, the API and the jobs use it.

* The brain: :func:`servicio_memoria` returns the :class:`~asistente.nucleo.cerebro.contrato.ProveedorMemoria` —
  :class:`MemoriaNula` when ``MEMORIA_CONVERSACIONES`` is off. ``recuperar`` ranks the owner's memory for one
  question (no model call: a few SQLite reads and a BM25); ``registrar_turno`` appends the turn's two messages
  (one write). Both NEVER raise: a failing memory degrades to «no memory», it never fails the user's turn.
* The user (``/recuerdos``, ``/conversaciones``): list with origins, correct (the user's wording wins), forget one
  (its earlier versions too), forget everything (the conversations too: nothing is left to learn it from again),
  pause (nothing new is stored, summarised or retrieved; what is there stays visible and erasable).
* The jobs (``python -m asistente.nucleo.recuerdos resumir``): :meth:`ServicioMemoria.resumir_pendientes`
  summarises idle conversations (through the provider's batch API when it has one) and
  :meth:`ServicioMemoria.purgar_caducados` applies the retention of the raw messages. With
  ``MEMORIA_RESUMEN_EN_SEGUNDO_PLANO`` the API process also summarises, in a background thread that carries the
  request's context (``contextvars.copy_context``), when a user starts a new conversation.

Whose memory: the owner is ``(case_id, user_id)`` exactly as the caller passes them (``''`` for None). The API
and the brain must resolve ``user_id`` the same way: the signed-in account's id, else the case's ``user_id``
(what ``api.py`` already uses for ``contexto_consumo``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
import contextvars
from datetime import datetime, timedelta
import logging
from pathlib import Path
import threading
from typing import Any

from asistente.nucleo.cerebro.contrato import (
    BloqueMemoria,
    MemoriaNula,
    OrigenRecuerdo,
    ProveedorMemoria,
    RecuerdoMemoria,
    TurnoConversacion,
)
from asistente.nucleo.recuerdos import almacen as almacen_mod
from asistente.nucleo.recuerdos import privacidad, recuperacion, resumen
from asistente.nucleo.recuerdos.almacen import (
    TIPOS_GUARDADOS,
    AlmacenRecuerdos,
    Conversacion,
    FilaLote,
    Mensaje,
    Recuerdo,
    Titular,
    fecha_local,
    tabla_existe,
)
from asistente.nucleo.recuerdos.recuperacion import Documento, Incrustador
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

MAX_LIMITE = 30
MAX_INTENTOS = 3
MIN_MENSAJE_RECUPERABLE = 15
MAX_CITA_MENSAJE = 400


class CorreccionInvalidaError(ValueError):
    """The user's correction cannot be stored as written (the message is Spanish, for the user)."""


def _retencion_dias() -> int:
    return max(1, int(getattr(settings, "MEMORIA_RETENCION_MENSAJES_DIAS", 30)))


def _max_recuerdos() -> int:
    return max(1, int(getattr(settings, "MEMORIA_MAX_RECUERDOS", 200)))


def _minutos_inactiva() -> int:
    return max(0, int(getattr(settings, "MEMORIA_RESUMEN_TRAS_MINUTOS", 30)))


def _recortar(texto: str, maximo: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------------------------------------------
# Providers and embedder (optional)
# --------------------------------------------------------------------------------------------------------------

_fabrica: list[Callable[[], Any]] = []


def configurar_proveedor(fabrica: Callable[[], Any] | None) -> None:
    """Register (None: remove) the factory of the provider the background summariser uses (default:
    :func:`proveedor_por_defecto`, which the job uses too)."""
    _fabrica.clear()
    if fabrica is not None:
        _fabrica.append(fabrica)


def proveedor_por_defecto() -> Any:
    """The configured provider, metered (and routed) as purpose ``memoria:resumen``."""
    from asistente.nucleo.agent.providers import create_provider
    from asistente.plataforma.ops.consumo import medir

    return medir(create_provider(), resumen.prompts.PROPOSITO)


_incrustador: list[Incrustador | None] = []
_incrustador_lock = threading.Lock()


def incrustador_configurado() -> Incrustador | None:
    """The project's embedder when ``MEMORIA_EMBEDDINGS`` is on (built once; None if it cannot be built)."""
    if not getattr(settings, "MEMORIA_EMBEDDINGS", False):
        return None
    with _incrustador_lock:
        if not _incrustador:
            try:
                from asistente.conocimiento.llm.embeddings import create_embeddings

                _incrustador.append(create_embeddings())
            except Exception:
                logger.warning("The memory's embedder could not be built: lexical ranking only.", exc_info=True)
                _incrustador.append(None)
        return _incrustador[0]


# --------------------------------------------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------------------------------------------


class ServicioMemoria:
    """The conversation memory over the cases' SQLite file (implements ``ProveedorMemoria``)."""

    def __init__(
        self,
        db_path: str,
        *,
        incrustador: Incrustador | None = None,
        usar_incrustador_configurado: bool = True,
    ) -> None:
        self.db_path = str(Path(db_path))
        self._incrustador = incrustador
        self._configurado = usar_incrustador_configurado and incrustador is None
        self._fondo_lock = threading.Lock()
        self._en_curso: set[Titular] = set()

    @property
    def store(self) -> AlmacenRecuerdos:
        return almacen_mod.almacen(self.db_path)

    @property
    def incrustador(self) -> Incrustador | None:
        return self._incrustador if not self._configurado else incrustador_configurado()

    # ------------------------------------------------------------------ the brain's interface (never raises)

    def recuperar(
        self,
        *,
        case_id: str | None,
        user_id: str | None,
        consulta: str,
        conversacion_id: str | None = None,
        limite: int = 8,
    ) -> BloqueMemoria:
        """What the owner's memory holds that is relevant to ``consulta`` (empty, with the reason, otherwise)."""
        titular = Titular.de(case_id, user_id)
        if titular.vacio:
            return BloqueMemoria(vacio_por="sin_cuenta")
        try:
            store = self.store
            if not store.activa(titular):
                return BloqueMemoria(vacio_por="desactivada")
            documentos = self.documentos(titular, excluir=conversacion_id)
            if not documentos or not (consulta or "").strip():
                return BloqueMemoria(vacio_por="sin_resultados")
            vector = None
            incrustador = self.incrustador
            if incrustador is not None and any(d.vector is not None for d in documentos):
                try:
                    vector = incrustador.embed_query(consulta)
                except Exception:
                    logger.warning("Could not embed the memory query; lexical ranking only.", exc_info=True)
            elegidos = recuperacion.recuperar(
                documentos,
                consulta,
                limite=max(1, min(int(limite), MAX_LIMITE)),
                hoy=fecha_local(almacen_mod.ahora()),
                vector_consulta=vector,
            )
            recuerdos = [
                _memoria(p.documento).model_copy(update={"relevancia": min(1.0, max(0.0, p.relevancia))})
                for p in elegidos
            ]
            return BloqueMemoria(recuerdos=recuerdos, vacio_por=None if recuerdos else "sin_resultados")
        except Exception:
            logger.exception("The conversation memory could not be read; the turn goes on without it.")
            return BloqueMemoria(vacio_por="sin_resultados")

    def registrar_turno(self, turno: TurnoConversacion) -> None:
        """Store the turn's question and answer (masked) in its conversation. One write; never raises."""
        try:
            titular = Titular.de(turno.case_id, turno.user_id)
            if titular.vacio:
                return
            store = self.store
            if not store.activa(titular):
                return
            meta: dict[str, Any] = {"especialistas": list(turno.especialistas)}
            if turno.modo:
                meta["modo"] = turno.modo
            if turno.recuerdos_usados:
                meta["recuerdos_usados"] = list(turno.recuerdos_usados)
            total = store.registrar_turno(
                titular,
                turno.conversacion_id,
                fecha=turno.fecha,
                pregunta=privacidad.limpiar(turno.pregunta),
                respuesta=privacidad.limpiar(turno.respuesta),
                meta=meta,
            )
            usados = [i for i in turno.recuerdos_usados if i.startswith("r_")]
            if usados:
                store.registrar_usos(titular, usados, almacen_mod.ahora())
            if total == 2 and getattr(settings, "MEMORIA_RESUMEN_EN_SEGUNDO_PLANO", False):
                self._resumir_en_segundo_plano(titular, excluir=turno.conversacion_id)
        except Exception:
            logger.exception("A conversation turn could not be stored in the memory; the turn goes on without it.")

    # ------------------------------------------------------------------ retrieval documents

    def documentos(self, titular: Titular, *, excluir: str | None = None) -> list[Documento]:
        """Everything retrievable of the owner: active items, summaries of other conversations, and the user's
        own words in conversations not summarised yet (so the memory works before the summariser runs)."""
        store = self.store
        terminos_items, terminos_conv = store.terminos(titular)
        vectores = store.vectores(titular) if self.incrustador is not None else {}
        documentos: list[Documento] = []
        for r in store.recuerdos(titular):
            fecha = r.origen.fecha
            if r.corregido:
                fecha = max(fecha, fecha_local(r.actualizado))
            documentos.append(
                Documento(
                    id=r.id,
                    tipo=r.tipo,
                    terminos=terminos_items.get(r.id, "").split(),
                    fecha=fecha,
                    conversacion_id=r.origen.conversacion_id,
                    corregido=r.corregido,
                    usos=r.usos,
                    vector=vectores.get(r.id),
                    extra={"memoria": r.como_memoria()},
                )
            )
        for c in store.conversaciones(titular, limite=1000):
            if c.id == excluir or not c.resumen or not c.cita:
                continue
            documentos.append(
                Documento(
                    id=f"conv-{c.id}",
                    tipo="resumen",
                    terminos=terminos_conv.get(c.id, "").split(),
                    fecha=fecha_local(c.actualizada),
                    conversacion_id=c.id,
                    vector=vectores.get(f"c:{c.id}"),
                    es_conversacion=True,
                    extra={"memoria": _memoria_de_resumen(c)},
                )
            )
        for conversacion_id, mensaje in store.sin_resumir(titular, excluir=excluir):
            documento = _documento_de_mensaje(conversacion_id, mensaje)
            if documento is not None:
                documentos.append(documento)
        return documentos

    # ------------------------------------------------------------------ the user's operations

    def activa(self, titular: Titular) -> bool:
        return self.store.activa(titular)

    def fijar_activa(self, titular: Titular, activa: bool) -> None:
        """Pause or resume the memory. Pausing stores, summarises and retrieves nothing; it deletes nothing."""
        self.store.fijar_activa(titular, activa, almacen_mod.ahora())

    def listar(self, titular: Titular) -> tuple[list[Recuerdo], list[Recuerdo]]:
        """(active items, earlier versions they replaced), newest first."""
        store = self.store
        return store.recuerdos(titular), store.recuerdos(titular, estados=("sustituido",))

    def corregir(self, titular: Titular, recuerdo_id: str, *, texto: str, tipo: str | None = None) -> Recuerdo | None:
        """The user rewrites an item: their wording wins (``corregido``). None when it is not theirs / active."""
        actual = self.store.recuerdo(titular, recuerdo_id)
        if actual is None or actual.estado != "activo":
            return None
        limpio = " ".join((texto or "").split())
        if not limpio:
            raise CorreccionInvalidaError("Escribe cómo quieres que lo recuerde.")
        if len(limpio) > 600:
            raise CorreccionInvalidaError("Como mucho 600 caracteres.")
        if privacidad.tiene_credencial(limpio):
            raise CorreccionInvalidaError("No guardo contraseñas, PIN ni claves: quítalo del texto.")
        if tipo is not None and tipo not in TIPOS_GUARDADOS:
            raise CorreccionInvalidaError("El tipo debe ser «dato», «preferencia» o «contexto».")
        nuevo = actual.model_copy(
            update={
                "texto": privacidad.limpiar(limpio),
                "tipo": tipo or actual.tipo,
                "corregido": True,
                "actualizado": almacen_mod.ahora(),
            }
        )
        vectores = resumen._vectores(self.incrustador, {nuevo.id: nuevo.texto})
        self.store.guardar(titular, nuevo, vectores=vectores)
        return nuevo

    def olvidar(self, titular: Titular, recuerdo_id: str) -> bool:
        """Forget one item (an active one takes its earlier versions with it). False when it is not theirs."""
        store = self.store
        objetivo = store.recuerdo(titular, recuerdo_id)
        if objetivo is None:
            return False
        ids = {objetivo.id}
        if objetivo.estado == "activo":
            historia = store.recuerdos(titular, estados=("sustituido",))
            cambiado = True
            while cambiado:
                cambiado = False
                for r in historia:
                    if r.id not in ids and r.sustituido_por in ids:
                        ids.add(r.id)
                        cambiado = True
        return store.olvidar(titular, sorted(ids)) > 0

    def olvidar_todo(self, titular: Titular) -> dict[str, int]:
        """Every item, conversation and message of the owner (the switch stays as the user left it)."""
        return self.store.borrar_titular(titular)

    def conversaciones(self, titular: Titular) -> list[tuple[Conversacion, int]]:
        """The owner's conversations with how many messages are still retained."""
        store = self.store
        retenidos = store.mensajes_retenidos(titular)
        return [(c, retenidos.get(c.id, 0)) for c in store.conversaciones(titular)]

    def conversacion(
        self, titular: Titular, conversacion_id: str
    ) -> tuple[Conversacion, list[Mensaje], list[Recuerdo]] | None:
        """A conversation of the owner, its retained messages and the items learnt from it."""
        store = self.store
        conversacion = store.conversacion(titular, conversacion_id)
        if conversacion is None:
            return None
        derivados = [
            r
            for r in store.recuerdos(titular, estados=("activo", "sustituido"))
            if any(f.conversacion_id == conversacion_id for f in r.fuentes)
        ]
        return conversacion, store.mensajes(titular, conversacion_id), derivados

    def borrar_conversacion(self, titular: Titular, conversacion_id: str) -> bool:
        return self.store.borrar_conversacion(titular, conversacion_id)

    # ------------------------------------------------------------------ jobs

    def resumir_pendientes(
        self,
        proveedor: Any | None,
        *,
        lotes: bool | None = None,
        limite: int = 50,
        titular: Titular | None = None,
        excluir: str | None = None,
    ) -> dict[str, int]:
        """Summarise the conversations idle for ``MEMORIA_RESUMEN_TRAS_MINUTOS`` (of every owner, or of
        ``titular``). With a batch-capable provider (and ``MEMORIA_RESUMEN_LOTES``) finished batches are
        collected first and the new ones submitted as one batch; otherwise one call per conversation."""
        informe: Counter[str] = Counter()
        if proveedor is None:
            informe["sin_proveedor"] += 1
            return dict(informe)
        store = self.store
        momento = almacen_mod.ahora()
        quiere_lotes = bool(getattr(settings, "MEMORIA_RESUMEN_LOTES", True)) if lotes is None else lotes
        con_lotes = quiere_lotes and resumen.admite_lotes(proveedor)
        if resumen.admite_lotes(proveedor) and titular is None:
            informe.update(self._recoger(proveedor, momento))

        hasta = momento - timedelta(minutes=_minutos_inactiva())
        entradas: list[resumen.EntradaResumen] = []
        for dueno, conversacion in store.pendientes(hasta=hasta, limite=max(1, limite) + 1, titular=titular):
            if len(entradas) >= limite:
                break
            if conversacion.id == excluir:
                continue
            if not store.activa(dueno):
                informe["pausada"] += 1
                continue
            entrada = resumen.preparar(store, dueno, conversacion)
            if not entrada.del_usuario:
                store.fijar_estado(dueno, conversacion.id, "sin_resumen")
                informe["sin_mensajes"] += 1
                continue
            entradas.append(entrada)

        if con_lotes:
            enviables = [e for e in entradas if _hay_presupuesto(proveedor, e.titular)]
            if len(enviables) < len(entradas):
                informe["presupuesto"] += len(entradas) - len(enviables)
            try:
                if resumen.enviar_en_lote(store, proveedor, enviables, momento=momento):
                    informe["enviadas_en_lote"] += len(enviables)
            except Exception:
                logger.exception("The memory batch could not be submitted; it will be retried on the next run.")
                informe["error_lote"] += 1
            return dict(informe)

        for entrada in entradas:
            informe.update(self._resumir_una(proveedor, entrada, momento))
        return dict(informe)

    def _resumir_una(self, proveedor: Any, entrada: resumen.EntradaResumen, momento: datetime) -> Counter[str]:
        from asistente.plataforma.ops.consumo import PresupuestoAgotadoError, contexto_consumo

        informe: Counter[str] = Counter()
        titular = entrada.titular
        # Each conversation is its own «question» for the usage log and the per-question cap.
        with contexto_consumo(user_id=titular.user_id or None, case_id=titular.case_id or None):
            try:
                resultado = resumen.resumir(proveedor, entrada)
            except PresupuestoAgotadoError:
                informe["presupuesto"] += 1
                return informe
            except Exception:
                logger.warning("A conversation could not be summarised (attempt recorded).", exc_info=True)
                self.store.fallo_de_resumen(titular, entrada.conversacion.id, max_intentos=MAX_INTENTOS)
                informe["error"] += 1
                return informe
        informe.update(
            resumen.aplicar(
                self.store,
                entrada,
                resultado,
                momento=momento,
                max_recuerdos=_max_recuerdos(),
                incrustador=self.incrustador,
            )
        )
        informe["resumidas"] += 1
        return informe

    def _recoger(self, proveedor: Any, momento: datetime) -> Counter[str]:
        store = self.store

        def procesar(fila: FilaLote, datos: dict[str, Any]) -> bool:
            conversacion = store.conversacion(fila.titular, fila.conversacion_id)
            if conversacion is None or conversacion.mensajes != fila.mensajes or not store.activa(fila.titular):
                return False  # deleted, continued or paused since it was sent: summarised again later (or never)
            entrada = resumen.preparar(store, fila.titular, conversacion)
            if entrada.cubiertos != fila.mensajes:
                return False
            resumen.aplicar(
                store,
                entrada,
                resumen.puerta(datos, entrada),
                momento=momento,
                max_recuerdos=_max_recuerdos(),
                incrustador=self.incrustador,
            )
            return True

        def registrar_uso(fila: FilaLote, uso: Any) -> None:
            registrar = getattr(proveedor, "registrar_externo", None)
            if not callable(registrar):
                return
            from dataclasses import replace

            from asistente.plataforma.ops.consumo import contexto_consumo

            ruta = getattr(proveedor, "ruta", None)
            with contexto_consumo(user_id=fila.titular.user_id or None, case_id=fila.titular.case_id or None):
                try:
                    registrar(
                        replace(
                            uso,
                            purpose=resumen.prompts.PROPOSITO,
                            tier=str(getattr(ruta, "nivel", "") or ""),
                            batch=True,
                        )
                    )
                except Exception:
                    logger.warning("Could not record the usage of a memory batch result.", exc_info=True)

        return resumen.recoger_lotes(
            store, proveedor, momento=momento, procesar=procesar, registrar_uso=registrar_uso, max_intentos=MAX_INTENTOS
        )

    def purgar_caducados(self) -> dict[str, int]:
        """Retention: raw messages older than ``MEMORIA_RETENCION_MENSAJES_DIAS`` go (summaries and items stay)."""
        return self.store.purgar_mensajes(antes_de=almacen_mod.ahora() - timedelta(days=_retencion_dias()))

    def _resumir_en_segundo_plano(self, titular: Titular, *, excluir: str) -> None:
        """A user started a new conversation: summarise their idle ones now, off the request path."""
        with self._fondo_lock:
            if titular in self._en_curso:
                return
            self._en_curso.add(titular)
        fabrica = _fabrica[0] if _fabrica else proveedor_por_defecto
        contexto = contextvars.copy_context()

        def trabajo() -> None:
            try:
                self.resumir_pendientes(fabrica(), lotes=False, limite=3, titular=titular, excluir=excluir)
            except Exception:
                logger.exception("Background summarising of the memory failed.")
            finally:
                with self._fondo_lock:
                    self._en_curso.discard(titular)

        threading.Thread(target=contexto.run, args=(trabajo,), name="memoria-resumen", daemon=True).start()


def _hay_presupuesto(proveedor: Any, titular: Titular) -> bool:
    comprobar = getattr(proveedor, "comprobar_presupuesto", None)
    if not callable(comprobar):
        return True
    from asistente.plataforma.ops.consumo import PresupuestoAgotadoError, contexto_consumo

    with contexto_consumo(user_id=titular.user_id or None, case_id=titular.case_id or None):
        try:
            comprobar()
        except PresupuestoAgotadoError:
            return False
    return True


def _memoria(documento: Documento) -> RecuerdoMemoria:
    item = documento.extra["memoria"]
    assert isinstance(item, RecuerdoMemoria)
    return item


def _memoria_de_resumen(c: Conversacion) -> RecuerdoMemoria:
    fecha = fecha_local(c.actualizada)
    texto = _recortar(f"Conversación del {fecha.strftime('%d/%m/%Y')} «{c.titulo}»: {c.resumen or ''}", 600)
    return RecuerdoMemoria(
        id=f"conv-{c.id}",
        tipo="resumen",
        texto=texto,
        origen=OrigenRecuerdo(conversacion_id=c.id, fecha=fecha, cita=_recortar(c.cita or c.titulo, 400)),
    )


def _documento_de_mensaje(conversacion_id: str, mensaje: Mensaje) -> Documento | None:
    """A user message of a conversation not summarised yet, as a retrievable item — through the same privacy
    rules as a summary (own words only, no injection bait, no special-category data)."""
    propias = " ".join(privacidad.quitar_pegado(mensaje.texto).split())
    if len(propias) < MIN_MENSAJE_RECUPERABLE or privacidad.es_inyeccion(propias):
        return None
    if privacidad.categoria_especial(propias) is not None or privacidad.SECRETO in propias:
        return None
    cita = _recortar(propias, MAX_CITA_MENSAJE)
    fecha = fecha_local(mensaje.fecha)
    item = RecuerdoMemoria(
        id=f"msg-{conversacion_id}-{mensaje.n}",
        tipo="contexto",
        texto=_recortar(
            f"En una conversación del {fecha.strftime('%d/%m/%Y')} (aún sin resumir) dijiste: «{cita}»", 600
        ),
        origen=OrigenRecuerdo(conversacion_id=conversacion_id, fecha=fecha, cita=cita),
    )
    return Documento(
        id=item.id,
        tipo="contexto",
        terminos=recuperacion.analizar(propias),
        fecha=fecha,
        conversacion_id=conversacion_id,
        es_conversacion=True,
        extra={"memoria": item},
    )


# --------------------------------------------------------------------------------------------------------------
# Module-level entry points
# --------------------------------------------------------------------------------------------------------------

_servicios: dict[str, ServicioMemoria] = {}
_servicios_lock = threading.Lock()


def servicio(db_path: str | None = None) -> ServicioMemoria:
    """The process-wide service for ``db_path`` (default: the cases' database), whatever the switch says."""
    ruta = str(Path(db_path or settings.DECISION_STATE_DB_PATH))
    with _servicios_lock:
        actual = _servicios.get(ruta)
        if actual is None:
            actual = _servicios[ruta] = ServicioMemoria(ruta)
        return actual


def servicio_memoria(db_path: str | None = None) -> ProveedorMemoria:
    """THE interface of the brain: the conversation memory, or :class:`MemoriaNula` when it is switched off."""
    if not getattr(settings, "MEMORIA_CONVERSACIONES", True):
        return MemoriaNula()
    return servicio(db_path)


def olvidar_servicios() -> None:
    """Forget the cached services and stores (tests that recreate their database files)."""
    with _servicios_lock:
        _servicios.clear()
    almacen_mod.olvidar_almacenes()


def exportar_recuerdos(db_path: str, *, case_id: str | None, user_id: str | None) -> dict[str, Any]:
    """The account export's entry: a case's memory (every owner key stored under the case, which is the
    user's), or the user's memory without a case. Never creates the tables."""
    vacio: dict[str, Any] = {"memoria_activa": True, "conversaciones": [], "recuerdos": []}
    if not tabla_existe(db_path):
        return vacio
    store = almacen_mod.almacen(db_path)
    titulares: Sequence[Titular]
    if case_id:
        titulares = store.titulares_de_caso(case_id) or [Titular.de(case_id, user_id)]
    else:
        titulares = [Titular.de(None, user_id)]
    salida = dict(vacio, conversaciones=[], recuerdos=[])
    salida["memoria_activa"] = store.activa(Titular.de(case_id, user_id))
    for titular in titulares:
        if titular.vacio:
            continue
        datos = store.exportar(titular)
        salida["conversaciones"].extend(datos["conversaciones"])
        salida["recuerdos"].extend(datos["recuerdos"])
    return salida


def borrar_de_usuario(db_path: str, *, user_id: str, case_ids: Sequence[str]) -> int:
    """Account erasure: the user's memory and their cases' (the triggers would do it too; this also
    checkpoints the WAL so no erased word is left in it). Never creates the tables."""
    if not tabla_existe(db_path):
        return 0
    return almacen_mod.almacen(db_path).borrar_de_usuario(user_id, list(case_ids))


__all__ = [
    "CorreccionInvalidaError",
    "ServicioMemoria",
    "borrar_de_usuario",
    "configurar_proveedor",
    "exportar_recuerdos",
    "incrustador_configurado",
    "olvidar_servicios",
    "proveedor_por_defecto",
    "servicio",
    "servicio_memoria",
]

"""Incremental sync: Gmail -> parse in memory -> classify -> store ONLY the derived item -> propose.

* First sync (or when Gmail says the history is too old, HTTP 404): the last ``ventana_dias`` days
  (``newer_than:Nd``, 30 by default, ``correo_ventana_dias`` / ``CORREO_VENTANA_DIAS``), at most
  ``CORREO_MAX_MENSAJES`` messages. The mailbox ``historyId`` is read BEFORE listing, so nothing that arrives
  meanwhile is lost.
* Later syncs: ``history.list`` from the stored ``historyId`` (``messageAdded`` only): no crawl.
* Each new message is fetched with ``format=metadata`` first; promotions, social and forum mail (Gmail's
  category labels) are classified from their headers and their bodies are never downloaded. The others are
  fetched with ``format=full``, parsed and classified; the raw message is dropped after that.
* Spam, trash, drafts, sent mail and chats are skipped.
* New items emit ``correo.clasificado`` (ids and category counts only) so subscribed modules wake up.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging
import os
from typing import Any

from hacienda_gpt.capacidades.correo import almacen, clasificador_llm, decisiones, oauth, propuestas
from hacienda_gpt.capacidades.correo.almacen import CampoGuardado, Elemento
from hacienda_gpt.capacidades.correo.clasificacion import Clasificacion, clasificar
from hacienda_gpt.capacidades.correo.config import VENTANA_DIAS_MAX, configuracion
from hacienda_gpt.capacidades.correo.dominios import Remitentes
from hacienda_gpt.capacidades.correo.dominios import cargar as cargar_remitentes
from hacienda_gpt.capacidades.correo.extraccion import enmascarar
from hacienda_gpt.capacidades.correo.gmail import ClienteGmail, HistorialCaducadoError
from hacienda_gpt.capacidades.correo.http import CorreoError, Transporte, transporte
from hacienda_gpt.capacidades.correo.mime import Mensaje, leer_mensaje

logger = logging.getLogger(__name__)

EVENTO = "correo.clasificado"
OMITIR_ETIQUETAS = frozenset({"SPAM", "TRASH", "DRAFT", "SENT", "CHAT"})
SOLO_METADATOS = frozenset({"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"})
CATEGORIAS_CON_ASUNTO = frozenset({"administracion", "factura_luz", "compra", "cargo", "viaje", "accion", "phishing"})


def conservacion_dias() -> int:
    try:
        return max(30, min(730, int(os.environ.get("CORREO_CONSERVACION_DIAS", "") or 180)))
    except ValueError:
        return 180


@dataclass
class ResultadoSincronizacion:
    modo: str  # "ventana" | "historial"
    leidos: int = 0
    nuevos: int = 0
    completos: int = 0
    categorias: dict[str, int] = field(default_factory=dict)
    propuestas: int = 0
    purgados: int = 0


def elemento_de(mensaje: Mensaje, clas: Clasificacion, fijados: set[str]) -> Elemento:
    """The derived item: what is kept of a message (see ``almacen``)."""
    return Elemento(
        id=mensaje.id,
        thread_id=mensaje.thread_id,
        fecha=mensaje.fecha,
        dominio=mensaje.remitente_dominio,
        categoria=clas.categoria,
        categoria_aparente=clas.categoria_aparente,
        sospechoso=clas.sospechoso,
        motivos=[s.motivo for s in clas.senales][:20] if clas.sospechoso else [],
        asunto=(
            enmascarar(mensaje.asunto, "")[: almacen.MAX_ASUNTO] or None
            if clas.categoria in CATEGORIAS_CON_ASUNTO
            else None
        ),
        campos=[
            CampoGuardado(
                campo=c.campo,
                valor=c.valor[:200],
                literal=c.literal[:200],
                fragmento=enmascarar(c.fragmento, c.literal)[:200],
            )
            for c in clas.campos
        ][:12],
        plazo=clas.plazo,
        requiere_accion=clas.requiere_accion,
        organismo=clas.organismo,
        organismo_verificado=clas.organismo_verificado,
        area=clas.area,
        comercio=clas.comercio,
        remitente=mensaje.remitente_direccion if mensaje.remitente_direccion in fijados else None,
        origen="llm" if clas.origen == "llm" else "reglas",
    )


def procesar(
    recurso_metadatos: dict[str, Any],
    obtener_completo: Any,
    *,
    remitentes: Remitentes,
    fijados: set[str],
    desde: date,
    permitir_llm: bool = False,
) -> tuple[Elemento | None, bool]:
    """(derived item or None if out of the window / skipped, whether the full message was downloaded)."""
    mensaje = leer_mensaje(recurso_metadatos, solo_metadatos=True)
    if mensaje.etiquetas & OMITIR_ETIQUETAS or mensaje.fecha < desde:
        return None, False
    completo = not (mensaje.etiquetas & SOLO_METADATOS)
    if completo:
        mensaje = leer_mensaje(obtener_completo(), solo_metadatos=False)
    clas = clasificar(mensaje, remitentes)
    clas = clasificador_llm.refinar(mensaje, clas, permitido=permitir_llm)
    return elemento_de(mensaje, clas, fijados), completo


def sincronizar(
    case_id: str,
    *,
    db_path: str | None = None,
    http: Transporte | None = None,
    hoy: date | None = None,
    ventana_dias: int | None = None,
    permitir_llm: bool = False,
) -> ResultadoSincronizacion:
    """Sync one case's mailbox. Raises CorreoError (Spanish) when it cannot; the error is also recorded."""
    conexion = almacen.conexion_de(case_id, db_path)
    if conexion is None:
        raise CorreoError("Gmail no está conectado para este expediente.")
    conf = configuracion()
    http = http or transporte()
    hoy = hoy or datetime.now(UTC).date()
    dias = max(1, min(VENTANA_DIAS_MAX, ventana_dias or conf.ventana_dias))
    desde = hoy - timedelta(days=dias)
    cliente = ClienteGmail(conf.api_url, lambda: oauth.token_de_acceso(conexion, db_path=db_path, http=http), http)
    try:
        resultado, nuevo_history = _sincronizar(
            case_id,
            cliente,
            conexion,
            db_path=db_path,
            hoy=hoy,
            desde=desde,
            dias=dias,
            maximo=conf.max_mensajes,
            permitir_llm=permitir_llm,
        )
    except CorreoError as exc:
        if almacen.conexion_de(case_id, db_path) is not None:
            almacen.actualizar_conexion(case_id, db_path, ultimo_error=str(exc)[:300])
        raise
    almacen.actualizar_conexion(
        case_id,
        db_path,
        history_id=nuevo_history,
        ultima_sincronizacion=datetime.now(UTC),
        ultimo_error=None,
        estado="conectada",
    )
    if resultado.nuevos:
        _emitir(db_path or almacen.ruta_db(), case_id, resultado, nuevo_history)
    return resultado


def _sincronizar(
    case_id: str,
    cliente: ClienteGmail,
    conexion: almacen.Conexion,
    *,
    db_path: str | None,
    hoy: date,
    desde: date,
    dias: int,
    maximo: int,
    permitir_llm: bool,
) -> tuple[ResultadoSincronizacion, str | None]:
    ids: list[str]
    nuevo_history: str | None
    resultado = ResultadoSincronizacion(modo="historial")
    try:
        if not conexion.history_id:
            raise HistorialCaducadoError("sin historial")
        anadidos, nuevo_history = cliente.historial(conexion.history_id, maximo)
        ids = [i for i, etiquetas in anadidos if not etiquetas & OMITIR_ETIQUETAS]
        nuevo_history = nuevo_history or conexion.history_id
    except HistorialCaducadoError:
        resultado.modo = "ventana"
        nuevo_history = str(cliente.perfil().get("historyId") or "") or None
        ids = cliente.listar(f"newer_than:{dias}d -in:chats -in:sent -in:drafts", maximo)
    guardados = almacen.ids_guardados(case_id, db_path)
    fijados = set(almacen.contactos(case_id, db_path))
    remitentes = cargar_remitentes()
    nuevos: list[Elemento] = []
    for mensaje_id in ids:
        if mensaje_id in guardados:
            continue
        resultado.leidos += 1
        try:
            item, completo = procesar(
                cliente.mensaje(mensaje_id, completo=False),
                lambda mid=mensaje_id: cliente.mensaje(mid, completo=True),
                remitentes=remitentes,
                fijados=fijados,
                desde=desde,
                permitir_llm=permitir_llm,
            )
        except ValueError:
            logger.warning("Skipped a malformed Gmail message")
            continue
        resultado.completos += int(completo)
        if item is not None:
            nuevos.append(item)
    almacen.guardar_elementos(case_id, nuevos, db_path)
    resultado.nuevos = len(nuevos)
    resultado.categorias = dict(Counter(e.categoria for e in nuevos))
    vigentes = decisiones.vigentes(db_path or almacen.ruta_db(), case_id, hoy)
    for item in nuevos:
        if decisiones.suprimido(item, vigentes):
            continue
        propuesta = propuestas.generar(case_id, item)
        if propuesta is not None and almacen.anadir_propuesta(propuesta, db_path):
            resultado.propuestas += 1
    resultado.purgados = almacen.purgar_anteriores(case_id, hoy - timedelta(days=conservacion_dias()), db_path)
    return resultado, nuevo_history


def _emitir(db_path: str, case_id: str, resultado: ResultadoSincronizacion, history: str | None) -> None:
    """Wake the subscribed modules (ids and counts only; never a subject or an address)."""
    try:
        from hacienda_gpt.eventos import emitir

        emitir(
            db_path,
            EVENTO,
            case_id=case_id,
            payload={"nuevos": resultado.nuevos, "categorias": dict(sorted(resultado.categorias.items()))},
            clave=f"{EVENTO}:{case_id}:{history or datetime.now(UTC).isoformat()}",
        )
    except Exception:  # noqa: BLE001 - the bus never blocks a sync; the daily run is the safety net
        logger.exception("Could not emit %s", EVENTO)


__all__ = ["EVENTO", "ResultadoSincronizacion", "elemento_de", "procesar", "sincronizar"]

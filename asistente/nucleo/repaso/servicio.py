"""The pass for one case (:func:`repasar`), for every case with batches (:func:`repasar_todos`), and the inbox hook.

**One notification per case and period**, of the weekly digest's kind (``resumen_semanal``) and key
(``resumen_semanal:2026-W47``; ``resumen_semanal:2026-11-18`` in daily mode), so the inbox, the delivery and the
decision memory treat it as the digest they know: it is never held back by a decision, and a decision that removes
a notice it cites removes it too (it lists its sources by their titles). It supersedes a deterministic digest of
the same period; a pass already written for the period is never written twice.

**Idempotent per period.** The row in :mod:`~asistente.nucleo.repaso.almacen` says what was written. If the user's
decision removed the digest, the next call rebuilds it from that row (:func:`adaptar`: the texts of the points that
are still there, the deterministic text for the rest) without calling the model again: at most one model call per
case and period (plus one repair).

**Nothing executes.** Proposals become buttons that call the existing confirmation endpoints (pending actions,
tasks, mail proposals); the text says that nothing was done. **Push** only when a point is urgent: the digest is
then ``urgente`` and the inbox's write path pushes it (``push.envio.avisar_al_escribir``); otherwise ``info``.

**Batches.** :func:`repasar_todos` (the ``repaso`` job) sends the requests of every case due as ONE batch of the
provider's batch API, stores each case's row as ``pendiente_lote`` with its snapshot of points, and collects the
results on its later runs (:func:`recoger_lotes`): each result goes through the same gate against the snapshot the
model read, is applied once (atomic claim of the row), and its usage is recorded at batch prices. A provider
without batches, a failed submission, an errored result or a batch still running after :data:`MAX_ESPERA_LOTE` all
fall back to the synchronous call (and that, on any provider error, to the deterministic rendering).

**Never fails other jobs.** :func:`repasar_todos` isolates each case; :func:`desde_bandeja` (the weekly digest's
hook) returns None on any error so the inbox writes the deterministic digest exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
import logging
import os
from typing import Any, Literal
from zoneinfo import ZoneInfo

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.repaso.almacen import Pasada, RepasoStore, custom_id_de
from asistente.nucleo.repaso.priorizacion import MAX_PUNTOS_POR_DEFECTO, Omitido, Priorizacion, Punto, priorizar
from asistente.nucleo.repaso.puerta import (
    ItemRedactado,
    Redaccion,
    Violacion,
    permitidos_de,
    permitidos_titular,
    revisar_texto,
)
from asistente.nucleo.repaso.redaccion import (
    Periodo,
    ResultadoRedaccion,
    cuerpo,
    determinista,
    item_determinista,
    periodo_de,
    peticion_lote,
    prompt_repaso,
    redactar,
    revisar_y_reparar,
    titular_determinista,
    titulo_notificacion,
)
from asistente.nucleo.repaso.senales import Marca, Recogida, recoger
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

PROPOSITO = "cerebro:repaso"
ZONA = ZoneInfo("Europe/Madrid")
# A batch still running after this long: its cases are written with the synchronous call instead.
MAX_ESPERA_LOTE = timedelta(hours=12)
# Names of the domains that are not modules, for the digest's labels.
NOMBRES_PROPIOS = {
    "fiscal": "Impuestos",
    "tareas": "Tareas",
    "expediente": "Tu expediente",
    "modulos": "Módulos",
    "memoria": "Tus decisiones",
}
ACCIONES_DIGEST = [
    "Confirma o descarta cada propuesta con su botón: no se hará nada sin tu confirmación.",
    "Abre cada aviso para ver el detalle, las fuentes y los pasos que propone.",
]

EstadoRepaso = Literal["escrito", "ya_hecho", "sin_novedades", "en_lote", "simulado", "error"]


# --------------------------------------------------------------------------- #
# Settings and the provider
# --------------------------------------------------------------------------- #


def activo() -> bool:
    return bool(getattr(settings, "REPASO_ACTIVO", False))


def frecuencia() -> str:
    valor = str(getattr(settings, "REPASO_FRECUENCIA", "semanal") or "semanal").strip().lower()
    return "diaria" if valor in ("diaria", "diario", "dia", "daily") else "semanal"


def max_puntos() -> int:
    try:
        return int(getattr(settings, "REPASO_MAX_PUNTOS", MAX_PUNTOS_POR_DEFECTO))
    except TypeError, ValueError:
        return MAX_PUNTOS_POR_DEFECTO


def hora() -> time:
    texto = str(getattr(settings, "REPASO_HORA", "07:00") or "07:00").strip()
    try:
        if ":" in texto:
            horas, minutos = texto.split(":", 1)
            return time(int(horas), int(minutos))
        return time(int(texto))
    except ValueError:
        logger.warning("REPASO_HORA=%r is not HH:MM; 07:00 is used", texto)
        return time(7, 0)


def dia_semana() -> int:
    try:
        return max(0, min(6, int(getattr(settings, "REPASO_DIA", 0))))
    except TypeError, ValueError:
        return 0


def toca(ahora: datetime) -> bool:
    """Whether this period's pass is due at ``ahora`` (Spanish time). A weekly pass missed on its day (no worker)
    is still due later that week; idempotency per period makes running it twice harmless."""
    local = ahora.astimezone(ZONA)
    if frecuencia() == "diaria":
        return local.time() >= hora()
    dia = dia_semana()
    return local.weekday() > dia or (local.weekday() == dia and local.time() >= hora())


def modelo_configurado() -> bool:
    """Whether the configured provider can be called at all (mirrors ``cli.proactive.hay_credenciales_llm``)."""
    proveedor = str(getattr(settings, "LLM_PROVIDER", "anthropic") or "").strip().lower()
    if proveedor in ("anthropic", "claude"):
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if proveedor == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return bool(getattr(settings, "LOCAL_LLM_MODEL", ""))


def proveedor_configurado() -> Any | None:
    """The metered provider for ``cerebro:repaso`` (routed to the small tier), or None without a model."""
    if not modelo_configurado():
        return None
    try:
        from asistente.nucleo.agent.providers import create_provider
        from asistente.plataforma.ops.consumo import medir

        return medir(create_provider(), PROPOSITO)
    except Exception:
        logger.exception("The pass's provider could not be built")
        return None


def nativo_de(proveedor: Any | None) -> Any | None:
    """The provider-native object that speaks the batch API, or None (no provider, or no batches)."""
    if proveedor is None:
        return None
    from asistente.nucleo.agent.providers.lotes import admite_lotes

    return getattr(proveedor, "wrapped", proveedor) if admite_lotes(proveedor) else None


def _contexto(case_id: str) -> Any:
    """Attribute the model calls of a case's pass to that case (and count them in its budget)."""
    try:
        from asistente.plataforma.ops.consumo import contexto_consumo

        return contexto_consumo(case_id=case_id)
    except Exception:  # pragma: no cover - metering is optional for the pass
        return nullcontext()


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass
class ResultadoRepaso:
    case_id: str
    periodo: str
    key: str
    estado: EstadoRepaso
    origen: str | None = None
    notification_id: str | None = None
    urgente: bool = False
    sustituido: bool = False
    puntos: list[Punto] = field(default_factory=list)
    restantes: int = 0
    redaccion: Redaccion | None = None
    violaciones: list[Violacion] = field(default_factory=list)
    omitidos: list[Omitido] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    intentos: int = 0
    titulo: str | None = None
    cuerpo: str | None = None

    def resumen(self) -> dict[str, Any]:
        """For the CLI and the logs: ids and counts, never the text."""
        return {
            "case_id": self.case_id,
            "periodo": self.periodo,
            "estado": self.estado,
            "origen": self.origen,
            "puntos": len(self.puntos),
            "urgente": self.urgente,
            "sustituido": self.sustituido,
            "violaciones": len(self.violaciones),
            "omitidos": len(self.omitidos),
            "errores": self.errores,
            "intentos": self.intentos,
        }


# --------------------------------------------------------------------------- #
# Reusing what was written for the period
# --------------------------------------------------------------------------- #


def _mismo(a: Punto, b: Punto) -> bool:
    """The same matter with the same facts and proposals (its text is still true)."""
    return (
        a.clave == b.clave
        and [s.id for s in a.senales] == [s.id for s in b.senales]
        and [s.hechos for s in a.senales] == [s.hechos for s in b.senales]
        and [p.id for p in a.propuestas] == [p.id for p in b.propuestas]
    )


def _item_valido(item: ItemRedactado, punto: Punto) -> bool:
    permitidos = permitidos_de(punto)
    if item.propuesta_id is not None and item.propuesta_id not in {p.id for p in punto.propuestas}:
        return False
    return not revisar_texto(item.texto, permitidos, campo="texto", minimo=10, maximo=420) and not revisar_texto(
        item.por_que, permitidos, campo="por_que", minimo=5, maximo=300
    )


def adaptar(redaccion: Redaccion, antes: Priorizacion, ahora: Priorizacion, periodo: Periodo) -> Redaccion:
    """A text written for ``antes`` applied to the points of ``ahora``: each point that is the same matter with the
    same facts keeps its accepted text (checked again by the gate); the others get the deterministic one. The
    headline is kept only when the points are exactly the same ones."""
    previos = {p.clave: p for p in antes.puntos}
    items_previos = {i.punto_id: i for i in redaccion.items}
    items: list[ItemRedactado] = []
    for punto in ahora.puntos:
        previo = previos.get(punto.clave)
        item = items_previos.get(previo.id) if previo is not None else None
        if previo is not None and item is not None and item.origen == "modelo" and _mismo(previo, punto):
            nuevo = item.model_copy(update={"punto_id": punto.id})
            if _item_valido(nuevo, punto):
                items.append(nuevo)
                continue
        items.append(item_determinista(punto))
    mismos = [p.clave for p in antes.puntos] == [p.clave for p in ahora.puntos] and all(
        _mismo(a, b) for a, b in zip(antes.puntos, ahora.puntos, strict=True)
    )
    titular_ok = (
        mismos
        and redaccion.titular_origen == "modelo"
        and not revisar_texto(
            redaccion.titular,
            permitidos_titular(ahora.puntos, extra=periodo.permitidos(), restantes=ahora.restantes),
            campo="titular",
            minimo=10,
            maximo=220,
        )
    )
    del_modelo = sum(1 for i in items if i.origen == "modelo") + int(titular_ok)
    total = len(items) + 1
    return Redaccion(
        titular=redaccion.titular if titular_ok else titular_determinista(ahora, periodo),
        items=items,
        origen="modelo" if del_modelo == total else "determinista" if del_modelo == 0 else "mixto",
        titular_origen="modelo" if titular_ok else "determinista",
    )


# --------------------------------------------------------------------------- #
# One case
# --------------------------------------------------------------------------- #


@dataclass
class _Contexto:
    case: Any
    hoy: date
    db_path: str
    store: Any
    almacen: RepasoStore
    periodo: Periodo
    nombres: dict[str, str]
    acciones: Any | None = None
    modulos_activos: Sequence[str] | None = None


def _nombres(extra: Mapping[str, str] | None) -> dict[str, str]:
    from asistente.nucleo.repaso.senales import _nombres_modulos

    return {**NOMBRES_PROPIOS, **_nombres_modulos(), **dict(extra or {})}


def _modulos_activos(case_id: str) -> list[str] | None:
    try:
        from asistente.nucleo.modulos.preferencias import modulos_activos_para_caso

        return [m.clave for m in modulos_activos_para_caso(case_id)]
    except Exception:
        logger.info("Active modules of case %s unknown for the pass", case_id, exc_info=True)
        return None


def _preparar(ctx: _Contexto, marca: Marca) -> tuple[Priorizacion, Recogida]:
    recogida = recoger(
        ctx.case,
        hoy=ctx.hoy,
        db_path=ctx.db_path,
        store=ctx.store,
        acciones=ctx.acciones,
        marca=marca,
        nombres=ctx.nombres,
        modulos_activos=ctx.modulos_activos,
    )
    prio = priorizar(
        recogida.senales,
        hoy=ctx.hoy,
        decisiones=recogida.decisiones,
        hechos_caso=recogida.hechos_caso,
        max_puntos=max_puntos(),
        nombres=ctx.nombres,
    )
    return prio, recogida


def _digest_actual(store: Any, case_id: str, key: str) -> Any | None:
    return next((n for n in store.list(case_id) if n.key == key), None)


def _notificacion(ctx: _Contexto, prio: Priorizacion, redaccion: Redaccion) -> Any:
    from asistente.nucleo.proactive.store import KIND_RESUMEN, Notification

    vences = [p.vence for p in prio.puntos if p.vence is not None and p.vence >= ctx.hoy]
    return Notification(
        case_id=ctx.case.case_id,
        kind=KIND_RESUMEN,
        key=ctx.periodo.key,
        title=titulo_notificacion(prio, ctx.periodo),
        body=cuerpo(prio, redaccion, nombres=ctx.nombres),
        severity="urgente" if any(p.urgente for p in prio.puntos) else "info",
        actions=list(ACCIONES_DIGEST),
        sources=[],
        due_date=min(vences) if vences else None,
    )


def _escribir(ctx: _Contexto, prio: Priorizacion, redaccion: Redaccion) -> tuple[str, bool]:
    """Write the digest, replacing a digest of the same period that is not this pass's. Returns (id, replaced)."""
    notificacion = _notificacion(ctx, prio, redaccion)
    case_id = ctx.case.case_id
    sustituido = False
    if _digest_actual(ctx.store, case_id, ctx.periodo.key) is not None:
        sustituido = ctx.store.remove_superseded(case_id, key_prefix=ctx.periodo.key, keep_key="\x00") > 0
    if ctx.store.add(notificacion):
        return notificacion.id, sustituido
    actual = _digest_actual(ctx.store, case_id, ctx.periodo.key)
    if actual is None:  # pragma: no cover - the inbox refused a digest (it never holds one back)
        raise RuntimeError("the digest could not be written")
    return actual.id, sustituido


def _contexto_de(
    case: Any,
    *,
    hoy: date,
    db_path: str | None,
    store: Any | None,
    acciones: Any | None,
    nombres: Mapping[str, str] | None,
    modulos_activos: Sequence[str] | None,
    frecuencia_: str | None,
) -> _Contexto:
    from asistente.nucleo.proactive.store import NotificationStore

    ruta = db_path or getattr(store, "db_path", None) or str(settings.DECISION_STATE_DB_PATH)
    return _Contexto(
        case=case,
        hoy=hoy,
        db_path=ruta,
        store=store or NotificationStore(ruta),
        almacen=RepasoStore(ruta),
        periodo=periodo_de(hoy, frecuencia_ or frecuencia()),
        nombres=_nombres(nombres),
        acciones=acciones,
        modulos_activos=modulos_activos if modulos_activos is not None else _modulos_activos(case.case_id),
    )


def _resultado(ctx: _Contexto, estado: EstadoRepaso, prio: Priorizacion | None = None, **extra: Any) -> ResultadoRepaso:
    return ResultadoRepaso(
        case_id=ctx.case.case_id,
        periodo=ctx.periodo.clave,
        key=ctx.periodo.key,
        estado=estado,
        puntos=list(prio.puntos) if prio is not None else [],
        restantes=prio.restantes if prio is not None else 0,
        omitidos=list(prio.omitidos) if prio is not None else [],
        urgente=any(p.urgente for p in prio.puntos) if prio is not None else False,
        **extra,
    )


def _aplicar(
    ctx: _Contexto,
    prio: Priorizacion,
    recogida: Recogida,
    texto: ResultadoRedaccion,
    *,
    previa: Pasada | None,
) -> ResultadoRepaso:
    notification_id, sustituido = _escribir(ctx, prio, texto.redaccion)
    ctx.almacen.guardar(
        Pasada(
            case_id=ctx.case.case_id,
            periodo=ctx.periodo.clave,
            key=ctx.periodo.key,
            frecuencia=ctx.periodo.frecuencia,
            hoy=ctx.hoy,
            estado="aplicada",
            origen=texto.redaccion.origen,
            notification_id=notification_id,
            evento_hasta=recogida.evento_hasta,
            creada=previa.creada if previa is not None else datetime.now(UTC),
            prioridad=prio,
            redaccion=texto.redaccion,
            violaciones=texto.violaciones,
            intentos=texto.intentos + (previa.intentos if previa is not None else 0),
            errores=recogida.errores,
        )
    )
    return _resultado(
        ctx,
        "escrito",
        prio,
        origen=texto.redaccion.origen,
        notification_id=notification_id,
        sustituido=sustituido,
        redaccion=texto.redaccion,
        violaciones=texto.violaciones,
        errores=recogida.errores,
        intentos=texto.intentos,
    )


_SIN_PROVEEDOR = object()


def repasar(
    case: Any,
    *,
    hoy: date,
    db_path: str | None = None,
    store: Any | None = None,
    acciones: Any | None = None,
    proveedor: Any = _SIN_PROVEEDOR,
    frecuencia_: str | None = None,
    nombres: Mapping[str, str] | None = None,
    modulos_activos: Sequence[str] | None = None,
    simular: bool = False,
) -> ResultadoRepaso:
    """The pass of one case for the period of ``hoy``: written once (see the module docstring).

    ``proveedor``: the metered provider (default: :func:`proveedor_configurado`; None: deterministic text only).
    ``simular``: compute and render everything, write nothing (the CLI's dry run). Unexpected errors propagate
    (the callers decide: the job counts them, the inbox hook falls back to the deterministic digest).
    """
    ctx = _contexto_de(
        case,
        hoy=hoy,
        db_path=db_path,
        store=store,
        acciones=acciones,
        nombres=nombres,
        modulos_activos=modulos_activos,
        frecuencia_=frecuencia_,
    )
    case_id = case.case_id
    previa = ctx.almacen.get(case_id, ctx.periodo.clave)
    actual = _digest_actual(ctx.store, case_id, ctx.periodo.key)
    if not simular and previa is not None:
        if previa.estado in ("pendiente_lote", "aplicando"):
            return _resultado(ctx, "en_lote", previa.prioridad)
        if actual is not None and previa.notification_id == actual.id:
            return _resultado(ctx, "ya_hecho", previa.prioridad, notification_id=actual.id, origen=previa.origen)
    marca = ctx.almacen.marca(case_id, antes_de=ctx.periodo.clave)
    prio, recogida = _preparar(ctx, marca)
    if not prio.hay_algo:
        return _resultado(ctx, "sin_novedades", prio, errores=recogida.errores)
    if previa is not None and previa.redaccion is not None:
        # A rebuild (the user's decision removed the digest): the accepted texts, no new model call.
        texto = ResultadoRedaccion(adaptar(previa.redaccion, previa.prioridad, prio, ctx.periodo))
    else:
        elegido = proveedor_configurado() if proveedor is _SIN_PROVEEDOR else proveedor
        with _contexto(case_id):
            texto = redactar(elegido, prio, periodo=ctx.periodo, hoy=hoy, nombres=ctx.nombres)
    if simular:
        return _resultado(
            ctx,
            "simulado",
            prio,
            origen=texto.redaccion.origen,
            redaccion=texto.redaccion,
            violaciones=texto.violaciones,
            errores=recogida.errores,
            intentos=texto.intentos,
            titulo=titulo_notificacion(prio, ctx.periodo),
            cuerpo=cuerpo(prio, texto.redaccion, nombres=ctx.nombres),
        )
    return _aplicar(ctx, prio, recogida, texto, previa=previa)


# --------------------------------------------------------------------------- #
# The inbox's hook (the weekly digest)
# --------------------------------------------------------------------------- #


def desde_bandeja(
    case: Any,
    *,
    hoy: date,
    store: Any,
    acciones: Any | None = None,
    nombres: Mapping[str, str] | None = None,
) -> int | None:
    """``bandeja.resumen_semanal``'s brain pass: 1 written, 0 not (already, pending, nothing open), None not handled.

    None — REPASO off, no model configured, or ANY error — and the inbox writes its deterministic digest exactly
    as before. A provider error inside the pass is not an error here: the pass writes its deterministic rendering.
    """
    if not activo():
        return None
    try:
        proveedor = proveedor_configurado()
        if proveedor is None:
            return None
        resultado = repasar(case, hoy=hoy, store=store, acciones=acciones, nombres=nombres, proveedor=proveedor)
    except Exception:
        logger.exception("The brain pass failed for case %s; the deterministic digest is used", case.case_id)
        return None
    return 1 if resultado.estado == "escrito" else 0


# --------------------------------------------------------------------------- #
# Every case, with batches
# --------------------------------------------------------------------------- #


@dataclass
class _EnCola:
    ctx: _Contexto
    prio: Priorizacion
    recogida: Recogida
    previa: Pasada | None
    params: Any
    custom_id: str


def _registrar_uso(proveedor: Any, case_id: str, usage: Any) -> None:
    registrar = getattr(proveedor, "registrar_externo", None)
    if usage is None or not callable(registrar):
        return
    with _contexto(case_id):
        registrar(replace(usage, purpose=PROPOSITO, batch=True))


def _sincrono(
    ctx: _Contexto, prio: Priorizacion, recogida: Recogida, proveedor: Any | None, previa: Pasada | None
) -> ResultadoRepaso:
    with _contexto(ctx.case.case_id):
        texto = redactar(proveedor, prio, periodo=ctx.periodo, hoy=ctx.hoy, nombres=ctx.nombres)
    return _aplicar(ctx, prio, recogida, texto, previa=previa)


def _contexto_de_pasada(pasada: Pasada, case: Any, *, db_path: str, store: Any, acciones: Any | None) -> _Contexto:
    ctx = _contexto_de(
        case,
        hoy=pasada.hoy,
        db_path=db_path,
        store=store,
        acciones=acciones,
        nombres=None,
        modulos_activos=None,
        frecuencia_=pasada.frecuencia,
    )
    return ctx


def aplicar_resultado(
    pasada: Pasada,
    resultado: Any | None,
    *,
    case: Any,
    db_path: str,
    store: Any,
    proveedor: Any | None,
    acciones: Any | None = None,
) -> ResultadoRepaso | None:
    """Apply one batch result (or its absence) to a pending row, once. None: another run applied it."""
    if not RepasoStore(db_path).reclamar_lote(pasada.case_id, pasada.periodo):
        return None
    ctx = _contexto_de_pasada(pasada, case, db_path=db_path, store=store, acciones=acciones)
    marca = ctx.almacen.marca(pasada.case_id, antes_de=pasada.periodo)
    prio, recogida = _preparar(ctx, marca)
    try:
        if not prio.hay_algo:
            ctx.almacen.guardar(pasada.model_copy(update={"estado": "descartada"}))
            return _resultado(ctx, "sin_novedades", prio)
        datos = getattr(resultado, "datos", None) if resultado is not None else None
        if datos is None:
            # No answer (errored, expired, or the batch took too long): the synchronous call.
            return _sincrono(ctx, prio, recogida, proveedor, pasada)
        system = prompt_repaso()
        from asistente.nucleo.repaso.redaccion import datos_para_modelo

        user = datos_para_modelo(pasada.prioridad, periodo=ctx.periodo, hoy=pasada.hoy, nombres=ctx.nombres)
        with _contexto(pasada.case_id):
            # Gated against the snapshot the model read; one synchronous repair when it fails.
            texto = revisar_y_reparar(proveedor, datos, pasada.prioridad, ctx.periodo, system=system, user=user)
        adaptada = adaptar(texto.redaccion, pasada.prioridad, prio, ctx.periodo)
        return _aplicar(
            ctx,
            prio,
            recogida,
            ResultadoRedaccion(adaptada, intentos=texto.intentos, violaciones=texto.violaciones, error=texto.error),
            previa=pasada,
        )
    except Exception:
        # Never leave the row claimed: the deterministic digest of the inbox (or the next run) takes over.
        ctx.almacen.guardar(pasada.model_copy(update={"estado": "descartada"}))
        raise


def recoger_lotes(
    db_path: str,
    *,
    proveedor: Any | None,
    ahora: datetime | None = None,
    store: Any | None = None,
    acciones: Any | None = None,
) -> dict[str, int]:
    """Collect the batches in flight: apply each finished result once; fall back to the synchronous call for the
    cases whose batch failed or is still running after :data:`MAX_ESPERA_LOTE`."""
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.proactive.store import NotificationStore

    ahora = ahora or datetime.now(UTC)
    almacen = RepasoStore(db_path)
    pendientes = almacen.en_lote(ahora=ahora)
    cuenta = {"en_curso": 0, "aplicados": 0, "sincronos": 0, "errores": 0}
    if not pendientes:
        return cuenta
    store = store or NotificationStore(db_path)
    casos = SQLiteCaseStateStore(db_path)
    nativo = nativo_de(proveedor)
    por_lote: dict[str, list[Pasada]] = {}
    for pasada in pendientes:
        por_lote.setdefault(pasada.lote_id or "", []).append(pasada)
    for lote_id, filas in por_lote.items():
        resultados: dict[str, Any] | None = None
        enviado = min((p.lote_enviado or p.actualizada) for p in filas)
        # Without a batch-capable provider nobody can collect it: the synchronous fallback at once.
        caducado = nativo is None or ahora - enviado >= MAX_ESPERA_LOTE
        if lote_id and nativo is not None:
            try:
                estado = nativo.estado_lote(lote_id)
                if estado.terminado:
                    resultados = {r.custom_id: r for r in nativo.resultados_lote(lote_id)}
            except ProviderError as exc:
                logger.warning("Batch %s of the pass could not be read: %s", lote_id, exc)
        if resultados is None and not caducado:
            cuenta["en_curso"] += len(filas)
            continue
        for pasada in filas:
            resultado = (resultados or {}).get(pasada.custom_id or "")
            try:
                case = casos.get_case(pasada.case_id)
                if case is None:
                    almacen.borrar_caso(pasada.case_id)
                    continue
                if resultado is not None:
                    _registrar_uso(proveedor, pasada.case_id, getattr(resultado, "usage", None))
                hecho = aplicar_resultado(
                    pasada, resultado, case=case, db_path=db_path, store=store, proveedor=proveedor, acciones=acciones
                )
                if hecho is None:
                    continue
                if resultado is not None and getattr(resultado, "datos", None) is not None:
                    cuenta["aplicados"] += 1
                else:
                    cuenta["sincronos"] += 1
            except Exception:
                logger.exception("Batch result of the pass failed for case %s", pasada.case_id)
                cuenta["errores"] += 1
    return cuenta


def _encolar(ctx: _Contexto, nativo: Any, *, previa: Pasada | None) -> _EnCola | ResultadoRepaso:
    marca = ctx.almacen.marca(ctx.case.case_id, antes_de=ctx.periodo.clave)
    prio, recogida = _preparar(ctx, marca)
    if not prio.hay_algo:
        return _resultado(ctx, "sin_novedades", prio, errores=recogida.errores)
    if previa is not None and previa.redaccion is not None:
        texto = ResultadoRedaccion(adaptar(previa.redaccion, previa.prioridad, prio, ctx.periodo))
        return _aplicar(ctx, prio, recogida, texto, previa=previa)
    params = peticion_lote(nativo, prio, periodo=ctx.periodo, hoy=ctx.hoy, nombres=ctx.nombres)
    return _EnCola(ctx, prio, recogida, previa, params, custom_id_de(ctx.case.case_id, ctx.periodo.clave))


def repasar_todos(
    db_path: str,
    *,
    ahora: datetime | None = None,
    hoy: date | None = None,
    proveedor: Any = _SIN_PROVEEDOR,
    por_lotes: bool | None = None,
    forzar: bool = False,
    acciones: Any | None = None,
    casos_ids: Sequence[str] | None = None,
    modulos_activos_de: Callable[[str], Sequence[str] | None] | None = None,
    frecuencia_: str | None = None,
) -> dict[str, Any]:
    """The ``repaso`` job: collect the batches in flight, then (when the period's pass is due, or ``forzar``) run
    the pass of every open case — one batch for all of them when the provider has a batch API."""
    from asistente.nucleo.decision.schemas import CaseStatus
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.proactive.store import NotificationStore

    ahora = ahora or datetime.now(UTC)
    hoy = hoy or ahora.astimezone(ZONA).date()
    elegido = proveedor_configurado() if proveedor is _SIN_PROVEEDOR else proveedor
    lotes = bool(getattr(settings, "REPASO_POR_LOTES", True)) if por_lotes is None else por_lotes
    periodo = periodo_de(hoy, frecuencia_ or frecuencia())
    resumen: dict[str, Any] = {
        "activo": True,
        "periodo": periodo.clave,
        "modelo": elegido is not None,
        "toca": forzar or toca(ahora),
        "casos": 0,
        "escritos": 0,
        "ya_hechos": 0,
        "sin_novedades": 0,
        "en_lote": 0,
        "errores": 0,
    }
    store = NotificationStore(db_path)
    almacen = RepasoStore(db_path)
    resumen["lotes"] = recoger_lotes(db_path, proveedor=elegido, ahora=ahora, store=store, acciones=acciones)
    resumen["purgadas"] = almacen.purgar(ahora=ahora)
    if not resumen["toca"]:
        return resumen
    nativo = nativo_de(elegido) if lotes else None
    cola: list[_EnCola] = []
    casos = SQLiteCaseStateStore(db_path)
    for case in casos.list_all_cases():
        if case.status == CaseStatus.CLOSED or (casos_ids is not None and case.case_id not in casos_ids):
            continue
        resumen["casos"] += 1
        try:
            if nativo is None:
                resultado = repasar(
                    case,
                    hoy=hoy,
                    db_path=db_path,
                    store=store,
                    acciones=acciones,
                    proveedor=elegido,
                    modulos_activos=modulos_activos_de(case.case_id) if modulos_activos_de else None,
                    frecuencia_=periodo.frecuencia,
                )
            else:
                ctx = _contexto_de(
                    case,
                    hoy=hoy,
                    db_path=db_path,
                    store=store,
                    acciones=acciones,
                    nombres=None,
                    modulos_activos=modulos_activos_de(case.case_id) if modulos_activos_de else None,
                    frecuencia_=periodo.frecuencia,
                )
                previa = almacen.get(case.case_id, ctx.periodo.clave)
                actual = _digest_actual(store, case.case_id, ctx.periodo.key)
                if previa is not None and previa.estado in ("pendiente_lote", "aplicando"):
                    resumen["en_lote"] += 1
                    continue
                if previa is not None and actual is not None and previa.notification_id == actual.id:
                    resumen["ya_hechos"] += 1
                    continue
                encolado = _encolar(ctx, nativo, previa=previa)
                if isinstance(encolado, _EnCola):
                    cola.append(encolado)
                    continue
                resultado = encolado
        except Exception:
            logger.exception("The pass failed for case %s", case.case_id)
            resumen["errores"] += 1
            continue
        clave = {"escrito": "escritos", "ya_hecho": "ya_hechos", "sin_novedades": "sin_novedades"}
        resumen[clave.get(resultado.estado, "en_lote" if resultado.estado == "en_lote" else "errores")] += 1
    if cola:
        enviado = _enviar(cola, nativo, elegido, ahora=ahora)
        resumen["lote"] = enviado.pop("lote")
        for nombre, n in enviado.items():
            resumen[nombre] += n
    return resumen


def _enviar(cola: list[_EnCola], nativo: Any, proveedor: Any, *, ahora: datetime) -> dict[str, Any]:
    """Submit the queued requests as ONE batch; on any failure, each case gets its synchronous pass now."""
    cuenta: dict[str, Any] = {"en_lote": 0, "escritos": 0, "errores": 0}
    lote_id: str | None = None
    try:
        comprobar = getattr(proveedor, "comprobar_presupuesto", None)
        if callable(comprobar):
            comprobar()
        lote_id = nativo.crear_lote([(e.custom_id, e.params) for e in cola])
        logger.info("Pass batch %s submitted: %d case(s)", lote_id, len(cola))
    except Exception as exc:  # budget, network, API: the synchronous fallback (deterministic on a provider error)
        logger.warning("The pass batch could not be submitted (%s); synchronous fallback", type(exc).__name__)
    for encolado in cola:
        ctx = encolado.ctx
        try:
            if lote_id is None:
                _sincrono(ctx, encolado.prio, encolado.recogida, proveedor, encolado.previa)
                cuenta["escritos"] += 1
                continue
            ctx.almacen.guardar(
                Pasada(
                    case_id=ctx.case.case_id,
                    periodo=ctx.periodo.clave,
                    key=ctx.periodo.key,
                    frecuencia=ctx.periodo.frecuencia,
                    hoy=ctx.hoy,
                    estado="pendiente_lote",
                    lote_id=lote_id,
                    custom_id=encolado.custom_id,
                    lote_enviado=ahora,
                    evento_hasta=encolado.recogida.evento_hasta,
                    prioridad=encolado.prio,
                    errores=encolado.recogida.errores,
                )
            )
            cuenta["en_lote"] += 1
        except Exception:
            logger.exception("The pass of case %s could not be queued", ctx.case.case_id)
            cuenta["errores"] += 1
    return {"lote": lote_id, **cuenta}


# --------------------------------------------------------------------------- #
# For the inbox card (asistente/plataforma/api/routers/repaso.py)
# --------------------------------------------------------------------------- #


def nombres_de_ambitos() -> dict[str, str]:
    """Display names of the domains (modules and the others), for labels and chips."""
    return _nombres(None)


def estado_propuestas(db_path: str, case_id: str, propuestas: Sequence[Any]) -> dict[str, str]:
    """Each proposal's state NOW, read from the layer that owns it: ``pendiente`` (its button still applies),
    ``decidida`` (confirmed, rejected or created since) or ``desconocido``. Read-only; never raises."""
    from asistente.nucleo.repaso.senales import _tabla_existe

    estados: dict[str, str] = {}
    acciones = tareas = None
    try:
        if _tabla_existe(db_path, "acciones_pendientes"):
            from asistente.nucleo.modulos.acciones import AccionStore

            acciones = AccionStore(db_path)
        if _tabla_existe(db_path, "tareas"):
            from asistente.nucleo.tareas.almacen import TareaStore

            tareas = TareaStore(db_path)
        abiertas_plantilla: set[str] | None = None
        for propuesta in propuestas:
            estado = "desconocido"
            try:
                if propuesta.tipo == "accion" and acciones is not None:
                    accion = acciones.get(propuesta.ref)
                    if accion is not None and accion.case_id == case_id:
                        estado = "pendiente" if accion.estado == "pendiente" else "decidida"
                elif propuesta.tipo in ("tarea", "paso") and tareas is not None:
                    tarea = tareas.get(propuesta.ref)
                    if tarea is not None and tarea.case_id == case_id:
                        if propuesta.tipo == "tarea":
                            estado = "pendiente" if tarea.estado == "propuesta" else "decidida"
                        else:
                            paso = tarea.paso(propuesta.paso or "")
                            espera = paso.espera_actual if paso is not None else None
                            esperando = (
                                paso is not None
                                and paso.estado == "esperando"
                                and espera is not None
                                and espera.motivo == "usuario"
                            )
                            estado = "pendiente" if esperando else "decidida"
                elif propuesta.tipo == "correo" and _tabla_existe(db_path, "correo_propuestas"):
                    from asistente.capacidades.correo import almacen as correo

                    encontrada = correo.propuesta(propuesta.ref, db_path)
                    if encontrada is not None and encontrada.case_id == case_id:
                        estado = "pendiente" if encontrada.estado == "pendiente" else "decidida"
                elif propuesta.tipo == "plantilla":
                    if abiertas_plantilla is None:
                        from asistente.nucleo.tareas.modelo import TERMINALES

                        abiertas_plantilla = {
                            t.plantilla
                            for t in (tareas.list(case_id) if tareas is not None else [])
                            if t.plantilla and t.estado not in TERMINALES
                        }
                    estado = "decidida" if propuesta.ref in abiertas_plantilla else "pendiente"
            except Exception:
                logger.info("State of proposal %s unknown", propuesta.id, exc_info=True)
            estados[propuesta.id] = estado
    finally:
        for store in (acciones, tareas):
            if store is not None:
                store.close()
    return estados


__all__ = [
    "MAX_ESPERA_LOTE",
    "PROPOSITO",
    "ResultadoRepaso",
    "activo",
    "adaptar",
    "aplicar_resultado",
    "desde_bandeja",
    "determinista",
    "estado_propuestas",
    "frecuencia",
    "modelo_configurado",
    "nativo_de",
    "nombres_de_ambitos",
    "proveedor_configurado",
    "recoger_lotes",
    "repasar",
    "repasar_todos",
    "toca",
]

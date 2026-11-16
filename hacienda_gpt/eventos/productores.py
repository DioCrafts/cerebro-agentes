"""Where events come from: one-line hooks in the places where something happens.

* **Profile facts** — every path that changes a case's facts (``PUT/DELETE /cases/{id}/facts``, the
  advisor's ``registrar_hechos``, a module tool, a document's facts the user accepted, a module switched
  off with its data) ends in ``SQLiteCaseStateStore.save_case``. :func:`instalar` hooks into it: the hook
  compares the facts being saved with the stored ones and writes ``perfil.cambiado`` (and
  ``cargos.importados`` for the detected charges) on the store's own connection, inside the transaction
  that saves the case. The event exists if and only if the change was committed. Only fact NAMES go in.
* **Erasure** — deleting a case deletes its events, deliveries and timers in the same transaction.
* **Global and module events** — :func:`emitir_novedades` (``boe-novedades``),
  :func:`emitir_cambio_normativo` (``boe-impact``, ``proactive.impacto.procesar_eventos``), :func:`emitir_indices`
  (``hipoteca.indices actualizar``) and :func:`emitir_cambio_modulo` (``PUT /me/modulos/{clave}``) write
  through their own connection. A producer never fails because of the bus: an error is logged and the
  daily run (the safety net) still covers what the event would have triggered.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import logging
import sqlite3
import threading
from typing import Any

from hacienda_gpt import settings
from hacienda_gpt.eventos import despacho
from hacienda_gpt.eventos.bus import borrar_caso_en, emitir_en
from hacienda_gpt.eventos.esquema import crear_tablas
from hacienda_gpt.eventos.rutas import HECHOS_DE_CARGOS
from hacienda_gpt.eventos.tipos import (
    CAMBIO_NORMATIVO,
    CARGOS_IMPORTADOS,
    INDICE_PUBLICADO,
    MODULO_ACTIVADO,
    MODULO_DESACTIVADO,
    NORMA_BOE,
    PERFIL_CAMBIADO,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rutas_con_tablas: set[str] = set()


def activos() -> bool:
    return bool(getattr(settings, "EVENTOS_ACTIVOS", True))


def _ruta(conn: sqlite3.Connection) -> str:
    fila = conn.execute("PRAGMA database_list").fetchone()
    return str(fila[2] or "") if fila is not None else ""


def _asegurar_tablas(conn: sqlite3.Connection, ruta: str) -> None:
    if ruta and ruta in _rutas_con_tablas:
        return
    crear_tablas(conn)
    if ruta:
        with _lock:
            _rutas_con_tablas.add(ruta)


def _valores(payload: Mapping[str, Any]) -> dict[str, str]:
    """Fact name -> canonical JSON of its value (only to compare; values never leave this function)."""
    return {
        str(f.get("name")): json.dumps(f.get("value"), sort_keys=True, ensure_ascii=False)
        for f in payload.get("facts", [])
        if isinstance(f, Mapping)
    }


def cambios(antes: Mapping[str, Any] | None, despues: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(facts set or changed, facts removed) between two case payloads; the fiscal year counts as a fact."""
    viejos = _valores(antes or {})
    nuevos = _valores(despues)
    cambiados = {n for n, v in nuevos.items() if viejos.get(n) != v}
    if antes is not None and antes.get("tax_period") != despues.get("tax_period"):
        cambiados.add("periodo_fiscal")
    return sorted(cambiados), sorted(set(viejos) - set(nuevos))


def al_guardar_caso(conn: sqlite3.Connection, case: Any) -> None:
    """``save_case`` hook: write the profile events of this save on the store's transaction."""
    if not activos():
        return
    ruta = ""
    try:
        fila = conn.execute("SELECT payload_json FROM case_states WHERE case_id = ?", (case.case_id,)).fetchone()
        antes = json.loads(fila[0]) if fila is not None else None
        cambiados, borrados = cambios(antes, json.loads(case.model_dump_json()))
        if not (cambiados or borrados):
            return
        ruta = _ruta(conn)
        _asegurar_tablas(conn, ruta)
        cargos = [n for n in cambiados if n in HECHOS_DE_CARGOS]
        if cargos:
            emitir_en(conn, CARGOS_IMPORTADOS, case_id=case.case_id, payload={"hechos": cargos})
        resto = [n for n in cambiados if n not in HECHOS_DE_CARGOS]
        if resto or borrados:
            emitir_en(conn, PERFIL_CAMBIADO, case_id=case.case_id, payload={"hechos": resto, "borrados": borrados})
    except Exception:
        # Never block the user's save: the daily run will catch up with what this event would have woken.
        logger.exception("Could not write the profile event of a case save")
        with _lock:
            _rutas_con_tablas.discard(ruta)
        return
    despacho.despertar(ruta)


def al_borrar_caso(conn: sqlite3.Connection, case_id: str) -> None:
    """``delete_case`` hook: the case's events go with it (an error aborts the erasure, to be retried)."""
    borrar_caso_en(conn, case_id)


def instalar() -> None:
    """Hook the bus into the case store (idempotent). The API calls it at start-up."""
    from hacienda_gpt.decision import state_store_sqlite as almacen

    if al_guardar_caso not in almacen.AL_GUARDAR:
        almacen.AL_GUARDAR.append(al_guardar_caso)
    if al_borrar_caso not in almacen.AL_BORRAR:
        almacen.AL_BORRAR.append(al_borrar_caso)


def desinstalar() -> None:
    from hacienda_gpt.decision import state_store_sqlite as almacen

    while al_guardar_caso in almacen.AL_GUARDAR:
        almacen.AL_GUARDAR.remove(al_guardar_caso)
    while al_borrar_caso in almacen.AL_BORRAR:
        almacen.AL_BORRAR.remove(al_borrar_caso)


def instalado() -> bool:
    from hacienda_gpt.decision import state_store_sqlite as almacen

    return al_guardar_caso in almacen.AL_GUARDAR


# --------------------------------------------------------------------------- #
# Producers with their own connection
# --------------------------------------------------------------------------- #


def emitir(
    db_path: str | None,
    tipo: str,
    *,
    case_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
    clave: str | None = None,
) -> int | None:
    """Write one event and commit. Never raises (logged): the producer's own work must go on.

    An event about a case is written only in a process where :func:`instalar` ran (the API), so the
    erasure hook that removes it with the case is always in place where such events exist.
    """
    if not activos() or (case_id is not None and not instalado()):
        return None
    ruta = db_path or settings.DECISION_STATE_DB_PATH
    try:
        conn = sqlite3.connect(ruta, timeout=30.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            crear_tablas(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                nuevo = emitir_en(conn, tipo, case_id=case_id, payload=payload, clave=clave)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()
    except Exception:
        logger.exception("Could not emit event %s", tipo)
        return None
    despacho.despertar(ruta)
    return nuevo


def emitir_novedades(db_path: str | None, novedades: Iterable[Any]) -> int:
    """``boe-novedades``: one global event per new norm of high relevance (what the tax module notifies)."""
    emitidos = 0
    for novedad in novedades:
        if getattr(novedad, "relevancia", None) != "alta":
            continue
        clave = f"{NORMA_BOE}:{novedad.boe_id}"
        if emitir(db_path, NORMA_BOE, payload={"novedad": novedad.model_dump(mode="json")}, clave=clave):
            emitidos += 1
    return emitidos


def emitir_cambio_normativo(db_path: str | None, cambio: Any) -> int | None:
    """``boe-impact``: one global event per analysed change of a consolidated law (public data only: the
    ``proactive.impacto.CambioNormativo``). Keyed by law and revision, so a re-run does not repeat it."""
    clave = f"{CAMBIO_NORMATIVO}:{cambio.boe_id}:{cambio.actualizado}"
    return emitir(db_path, CAMBIO_NORMATIVO, payload={"cambio": cambio.model_dump(mode="json")}, clave=clave)


def emitir_indices(boe_ids: Iterable[str], db_path: str | None = None) -> int | None:
    """``hipoteca.indices actualizar``: new official indices were read from the BOE."""
    ids = sorted({str(i) for i in boe_ids})
    if not ids:
        return None
    return emitir(db_path, INDICE_PUBLICADO, payload={"boe_ids": ids}, clave=f"{INDICE_PUBLICADO}:{','.join(ids)}")


def emitir_cambio_modulo(db_path: str | None, case_ids: Sequence[str], clave: str, activo: bool) -> int:
    """``PUT /me/modulos/{clave}``: the owner switched a module on or off (one event per case they own)."""
    tipo = MODULO_ACTIVADO if activo else MODULO_DESACTIVADO
    return sum(1 for case_id in case_ids if emitir(db_path, tipo, case_id=case_id, payload={"modulo": clave}))


__all__ = [
    "al_borrar_caso",
    "al_guardar_caso",
    "cambios",
    "desinstalar",
    "emitir",
    "emitir_cambio_modulo",
    "emitir_cambio_normativo",
    "emitir_indices",
    "emitir_novedades",
    "instalado",
    "instalar",
]

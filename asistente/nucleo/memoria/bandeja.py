"""The decisions on the inbox's write path: the one place that holds a proposal back (or lets it come back).

:func:`filtrar_al_escribir` is called by ``NotificationStore.add`` before every write, so the tax jobs, the
modules' notices and anything else that writes to the inbox go through it; the weekly digest is built from
what the inbox holds, so it never lists a proposal held back here. For a notice whose ámbito
(:func:`asistente.nucleo.memoria.ambitos.ambito_de_aviso`) is covered by a decision of the case:

* a decision in force (a rejection, a restriction, «ya lo hice») → the notice is not written; the decision
  counts it (``omisiones``), so the assistant can say why it did not insist;
* a decision whose reopening condition holds, or that expired → the notice is written ONCE, its body
  starting with what the user decided, when, and what changed; the decision is marked as reopened;
* otherwise the notice is written as it is.

The weekly digest itself is never held back. Any failure here is logged and the notice is written unchanged:
the memory may never lose a deadline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.almacen import almacen, tabla_existe
from asistente.nucleo.memoria.ambitos import ambito_de_aviso
from asistente.nucleo.memoria.modelo import BLOQUEAN_AVISOS, Decision, Evaluacion, cubre, evaluar, fecha_es

if TYPE_CHECKING:
    from asistente.nucleo.proactive.store import Notification

logger = logging.getLogger(__name__)

# Databases known to hold the decisions table (once created it stays), to skip the check on every write.
_CON_TABLA: set[str] = set()


def _hay_decisiones(db_path: str) -> bool:
    if db_path in _CON_TABLA:
        return True
    if tabla_existe(db_path):
        _CON_TABLA.add(db_path)
        return True
    return False


def _conectar(db_path: str) -> sqlite3.Connection | None:
    if not db_path or db_path == ":memory:" or not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def hechos_del_caso(db_path: str, case_id: str) -> dict[str, Any] | None:
    """The case's facts (name -> value) read from the cases table; None when unknown."""
    conn = _conectar(db_path)
    if conn is None:
        return None
    try:
        fila = conn.execute("SELECT payload_json FROM case_states WHERE case_id = ?", (case_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if fila is None:
        return None
    datos = json.loads(fila["payload_json"])
    return {str(f["name"]): f.get("value") for f in datos.get("facts", []) if isinstance(f, dict) and f.get("name")}


def aviso_existe(db_path: str, case_id: str, key: str) -> bool:
    conn = _conectar(db_path)
    if conn is None:
        return False
    try:
        fila = conn.execute("SELECT 1 FROM notifications WHERE case_id = ? AND key = ?", (case_id, key)).fetchone()
        return fila is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def nota_de_reapertura(decision: Decision, motivo: str | None) -> str:
    """The first paragraph of a proposal that comes back: what the user decided, when, and what changed."""
    cuando = fecha_es(decision.fecha.date())
    if decision.boton == "recuerdamelo":
        dijo = f"El {cuando} me pediste que te lo recordara más tarde"
    elif decision.tipo == "aceptacion":
        dijo = f"El {cuando} me dijiste que ya lo habías hecho: «{decision.razon}»"
    elif decision.tipo == "restriccion":
        dijo = f"El {cuando} me indicaste: «{decision.razon}»"
    else:
        dijo = f"El {cuando} me dijiste que no te interesaba: «{decision.razon}»"
    porque = f" Te lo vuelvo a proponer, una sola vez, porque {motivo}." if motivo else ""
    return (
        f"**Te lo vuelvo a proponer.** {dijo}.{porque} Si sigue sin interesarte, pulsa «No me interesa» "
        "y no volveré a insistir."
    )


def decisiones_que_cubren(decisiones: list[Decision], ambito: str, tipos: frozenset[str]) -> list[Decision]:
    return [d for d in decisiones if d.tipo in tipos and cubre(d.ambito, ambito)]


def _filtrar(db_path: str, notification: Notification) -> Notification | None:
    ambito = ambito_de_aviso(notification.kind, notification.key)
    if ambito is None or not _hay_decisiones(db_path):
        return notification
    store = almacen(db_path)
    relevantes = decisiones_que_cubren(store.list(notification.case_id), ambito.propuesta, BLOQUEAN_AVISOS)
    if not relevantes:
        return notification
    hoy = reloj.hoy()
    hechos = hechos_del_caso(db_path, notification.case_id)
    evaluadas: list[tuple[Decision, Evaluacion]] = [
        (d, evaluar(d, hoy=hoy, hechos=hechos, importe=notification.amount)) for d in relevantes
    ]
    vigente = next((d for d, e in evaluadas if e.vigente), None)
    if vigente is not None:
        store.contar_omision(notification.case_id, vigente.id, hoy)
        logger.info("Notice %s of case %s held back by decision %s", notification.key, notification.case_id, vigente.id)
        return None
    reabribles = [(d, e) for d, e in evaluadas if e.estado in ("reabrible", "caducada")]
    if not reabribles:
        return notification
    if aviso_existe(db_path, notification.case_id, notification.key):
        # The store will not write it again: nothing comes back, nothing is spent.
        return notification
    decision, evaluacion = reabribles[0]  # newest first
    store.update(
        decision.model_copy(
            update={
                "reabierta_el": hoy,
                "reabierta_por": evaluacion.por,
                "reabierta_motivo": evaluacion.motivo,
                "reabierta_clave": notification.key,
            }
        ),
        evento="reabierta",
        extra={"motivo": evaluacion.motivo, "aviso": notification.key},
    )
    return notification.model_copy(
        update={"body": nota_de_reapertura(decision, evaluacion.motivo) + "\n\n" + notification.body}
    )


def filtrar_al_escribir(store: Any, notification: Notification) -> Notification | None:
    """Hook of ``NotificationStore.add``: None to skip the write, else the notice to write (maybe annotated)."""
    try:
        return _filtrar(str(getattr(store, "db_path", "") or ""), notification)
    except Exception:  # the memory never loses a notice
        logger.exception("Decision filter failed for notice %s; written unchanged", notification.key)
        return notification


__all__ = ["decisiones_que_cubren", "filtrar_al_escribir", "hechos_del_caso", "nota_de_reapertura"]

"""Recording, changing and forgetting decisions — the same rules for every capture path.

* the agent tool ``registrar_decision`` (what the user said in the chat, with their literal words);
* the «No me interesa» / «Ya lo hice» / «Recuérdamelo más tarde» buttons on notices and proposals;
* «Lo que sé de tus preferencias» (the user writes, edits or deletes a decision);
* a pending action the user confirmed or rejected.

A new decision about exactly the same ámbito replaces the previous one (kept as ``sustituida``). A decision
that holds proposals back removes, at once, the stored notices it covers (so the inbox and the next weekly
digest no longer show them); one about a lever of the tax optimiser also removes the year-end plan, which
the next run of the jobs rebuilds with the decision as a constraint.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Literal

from pydantic import ValidationError

from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.almacen import almacen, tabla_existe
from asistente.nucleo.memoria.ambitos import ambito_de_accion, ambito_de_aviso, etiqueta
from asistente.nucleo.memoria.modelo import (
    BLOQUEAN_AVISOS,
    FACTOR_MEJORA_POR_DEFECTO,
    Boton,
    Condicion,
    Decision,
    Evaluacion,
    FuenteDecision,
    Propuesta,
    TipoDecision,
    ahora,
    caducidad_por_defecto,
    cubre,
    eur,
    evaluar,
    huella,
)

logger = logging.getLogger(__name__)

POR_DEFECTO: Literal["por_defecto"] = "por_defecto"
# Ámbitos whose decisions change what the tax optimiser may propose (the year-end plan is rebuilt).
_PALANCAS_FISCALES = ("fiscal:plan_pensiones", "fiscal:donativo", "fiscal:perdidas", "fiscal:tributacion")
_RAZON_BOTON: dict[str, str] = {
    "no_me_interesa": "No me interesa",
    "ya_lo_hice": "Ya lo hice",
    "recuerdamelo": "Recuérdamelo más tarde",
}
_TIPO_BOTON: dict[str, TipoDecision] = {
    "no_me_interesa": "rechazo",
    "ya_lo_hice": "aceptacion",
    "recuerdamelo": "rechazo",
}
DIAS_RECORDAR_POR_DEFECTO = 7


class DecisionInvalidaError(ValueError):
    """The decision cannot be stored as given (the message is Spanish, for the user or the model)."""


@dataclass
class Resultado:
    decision: Decision
    # Notices removed from the inbox because the decision covers them.
    avisos_retirados: list[str] = field(default_factory=list)
    # The year-end plan was removed and should be rebuilt (the optimiser's constraints changed).
    plan_retirado: bool = False
    # This week's digest was removed (it listed a notice that is gone) and should be rebuilt.
    resumen_retirado: bool = False


def afecta_al_optimizador(ambito: str) -> bool:
    return ambito == "fiscal" or any(cubre(p, ambito) or cubre(ambito, p) for p in _PALANCAS_FISCALES)


def decisiones_del_caso(db_path: str, case_id: str) -> list[Decision]:
    """The case's decisions, newest first; [] when the database has none (never creates the table)."""
    if not tabla_existe(db_path):
        return []
    return almacen(db_path).list(case_id)


def evaluadas(
    decisiones: Iterable[Decision], *, hoy: date, hechos: Mapping[str, Any] | None
) -> list[tuple[Decision, Evaluacion]]:
    return [(d, evaluar(d, hoy=hoy, hechos=dict(hechos) if hechos is not None else None)) for d in decisiones]


def _condiciones(crudas: Sequence[Condicion | Mapping[str, Any]], hechos: Mapping[str, Any] | None) -> list[Condicion]:
    salida: list[Condicion] = []
    for cruda in crudas:
        datos = cruda.model_dump() if isinstance(cruda, Condicion) else dict(cruda)
        if datos.get("tipo") == "hecho" and "huella_al_decidir" not in datos:
            # Only a fingerprint of the value at the time of deciding is kept, never the value.
            datos["huella_al_decidir"] = huella((hechos or {}).get(str(datos.get("hecho"))))
        try:
            salida.append(Condicion.model_validate(datos))
        except ValidationError as exc:
            raise DecisionInvalidaError(_problemas(exc)) from exc
    return salida


def _problemas(exc: ValidationError) -> str:
    return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())


# --------------------------------------------------------------------------- #
# The inbox (same SQLite file): remove what a decision covers
# --------------------------------------------------------------------------- #


def _avisos(db_path: str, case_id: str) -> list[tuple[str, str, str, str, str]]:
    """(id, kind, key, title, body) of the case's stored notices; [] without the table."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        filas = conn.execute("SELECT id, payload_json FROM notifications WHERE case_id = ?", (case_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    salida = []
    for id_, payload in filas:
        datos = json.loads(payload)
        salida.append((id_, datos.get("kind", ""), datos.get("key", ""), datos.get("title", ""), datos.get("body", "")))
    return salida


def _borrar_avisos(db_path: str, ids: Sequence[str]) -> None:
    if not ids:
        return
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        with conn:
            marcas = ",".join("?" * len(ids))
            hay_entregas = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notification_deliveries'"
            ).fetchone()
            if hay_entregas:
                conn.execute(f"DELETE FROM notification_deliveries WHERE notification_id IN ({marcas})", list(ids))
            conn.execute(f"DELETE FROM notifications WHERE id IN ({marcas})", list(ids))
    finally:
        conn.close()


def retirar_avisos(
    db_path: str, case_id: str, ambito: str, *, plan: bool, incluir_ids: Sequence[str] = ()
) -> tuple[list[str], bool, bool]:
    """Remove the notices ``ambito`` covers (and, with ``plan``, the year-end plan) from the inbox.

    Returns (removed notice keys, whether the year-end plan went, whether this week's digest went).
    """
    avisos = _avisos(db_path, case_id)
    borrar: list[str] = []
    claves: list[str] = []
    titulos: list[str] = []
    plan_retirado = False
    for id_, kind, key, titulo, _body in avisos:
        if kind == "resumen_semanal":
            continue
        propio = ambito_de_aviso(kind, key)
        es_plan = plan and kind == "fin_de_ejercicio" and not key.endswith(":faltan_datos")
        if id_ in incluir_ids or es_plan or (propio is not None and cubre(ambito, propio.propuesta)):
            borrar.append(id_)
            claves.append(key)
            titulos.append(titulo)
            plan_retirado = plan_retirado or kind == "fin_de_ejercicio"
    resumen_retirado = False
    if titulos:
        semana = reloj.hoy().isocalendar()
        clave_semana = f"resumen_semanal:{semana[0]}-W{semana[1]:02d}"
        for id_, kind, key, _titulo, body in avisos:
            if kind == "resumen_semanal" and key == clave_semana and any(t and t in body for t in titulos):
                borrar.append(id_)
                resumen_retirado = True
    _borrar_avisos(db_path, borrar)
    return claves, plan_retirado, resumen_retirado


# --------------------------------------------------------------------------- #
# Record / change / forget
# --------------------------------------------------------------------------- #


def registrar(
    db_path: str,
    *,
    case_id: str,
    ambito: str,
    tipo: TipoDecision,
    razon: str,
    fuente: FuenteDecision,
    user_id: str | None = None,
    boton: Boton | None = None,
    caducidad: date | None | Literal["por_defecto"] = POR_DEFECTO,
    condiciones: Sequence[Condicion | Mapping[str, Any]] = (),
    propuesta: Propuesta | Mapping[str, Any] | None = None,
    hechos: Mapping[str, Any] | None = None,
    avisos_db: str | None = None,
    retirar_ids: Sequence[str] = (),
) -> Resultado:
    """Store a decision (replacing an earlier one about the same ámbito) and apply it to the inbox."""
    hoy = reloj.hoy()
    fin = caducidad_por_defecto(tipo, hoy) if caducidad == POR_DEFECTO else caducidad
    try:
        decision = Decision(
            case_id=case_id,
            user_id=user_id,
            ambito=ambito,
            tipo=tipo,
            razon=razon,
            fuente=fuente,
            boton=boton,
            caducidad=fin if isinstance(fin, date) else None,
            condiciones_de_reapertura=_condiciones(condiciones, hechos),
            propuesta=Propuesta.model_validate(propuesta) if isinstance(propuesta, Mapping) else propuesta,
        )
    except ValidationError as exc:
        raise DecisionInvalidaError(_problemas(exc)) from exc
    if decision.caducidad is not None and decision.caducidad <= hoy:
        raise DecisionInvalidaError("La fecha hasta la que vale la decisión debe ser posterior a hoy.")
    store = almacen(db_path)
    for anterior in store.list(case_id):
        if anterior.ambito == decision.ambito and not anterior.sustituida_por:
            store.update(
                anterior.model_copy(update={"sustituida_por": decision.id, "actualizada": ahora()}),
                evento="sustituida",
                extra={"por": decision.id},
            )
    store.add(decision)
    return _aplicar(decision, db_path=avisos_db or db_path, retirar_ids=retirar_ids)


def _aplicar(decision: Decision, *, db_path: str, retirar_ids: Sequence[str] = ()) -> Resultado:
    resultado = Resultado(decision=decision)
    bloquea = decision.tipo in BLOQUEAN_AVISOS
    plan = afecta_al_optimizador(decision.ambito)
    if not bloquea and not plan and not retirar_ids:
        return resultado
    claves, plan_retirado, resumen_retirado = retirar_avisos(
        db_path,
        decision.case_id,
        decision.ambito if bloquea else "\x00",
        plan=plan,
        incluir_ids=retirar_ids,
    )
    resultado.avisos_retirados = claves
    resultado.plan_retirado = plan_retirado
    resultado.resumen_retirado = resumen_retirado
    return resultado


def editar(
    db_path: str,
    *,
    case_id: str,
    decision_id: str,
    cambios: Mapping[str, Any],
    hechos: Mapping[str, Any] | None = None,
    avisos_db: str | None = None,
) -> Resultado | None:
    """Change what the user may change (kind, words, expiry, reopening conditions). None: not found.

    A changed decision is in force again (it may hold back what it reopened before).
    """
    store = almacen(db_path)
    actual = store.get(case_id, decision_id)
    if actual is None:
        return None
    permitidos = {"tipo", "razon", "caducidad", "condiciones_de_reapertura"}
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise DecisionInvalidaError(f"No se puede cambiar: {', '.join(sorted(desconocidos))}.")
    datos = actual.model_dump()
    if "condiciones_de_reapertura" in cambios:
        datos["condiciones_de_reapertura"] = [
            c.model_dump() for c in _condiciones(cambios["condiciones_de_reapertura"] or [], hechos)
        ]
    for campo in ("tipo", "razon", "caducidad"):
        if campo in cambios:
            datos[campo] = cambios[campo]
    datos.update(
        actualizada=ahora(),
        reabierta_el=None,
        reabierta_por=None,
        reabierta_motivo=None,
        reabierta_clave=None,
        sustituida_por=None,
    )
    try:
        nueva = Decision.model_validate(datos)
    except ValidationError as exc:
        raise DecisionInvalidaError(_problemas(exc)) from exc
    if nueva.caducidad is not None and nueva.caducidad <= reloj.hoy():
        raise DecisionInvalidaError("La fecha hasta la que vale la decisión debe ser posterior a hoy.")
    store.update(nueva, evento="editada", extra={"cambios": sorted(cambios)})
    return _aplicar(nueva, db_path=avisos_db or db_path)


def borrar(db_path: str, *, case_id: str, decision_id: str, avisos_db: str | None = None) -> Resultado | None:
    """Forget a decision (its history is redacted). The year-end plan is rebuilt if it was a tax lever."""
    store = almacen(db_path)
    actual = store.get(case_id, decision_id)
    if actual is None or not store.delete(case_id, decision_id):
        return None
    resultado = Resultado(decision=actual)
    if afecta_al_optimizador(actual.ambito):
        _claves, resultado.plan_retirado, resultado.resumen_retirado = retirar_avisos(
            avisos_db or db_path, case_id, "\x00", plan=True
        )
    return resultado


# --------------------------------------------------------------------------- #
# Capture paths
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AvisoGuardado:
    id: str
    kind: str
    key: str
    title: str
    amount: Decimal | None


def leer_aviso(db_path: str, case_id: str, notification_id: str) -> AvisoGuardado | None:
    """A stored notice of ``case_id`` (None when it is not that case's)."""
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        fila = conn.execute(
            "SELECT payload_json FROM notifications WHERE case_id = ? AND id = ?", (case_id, notification_id)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if fila is None:
        return None
    datos = json.loads(fila[0])
    importe = datos.get("amount")
    return AvisoGuardado(
        id=notification_id,
        kind=datos.get("kind", ""),
        key=datos.get("key", ""),
        title=datos.get("title", ""),
        amount=Decimal(str(importe)) if importe is not None else None,
    )


def desde_aviso(
    db_path: str,
    *,
    case_id: str,
    notification_id: str,
    boton: Boton,
    razon: str | None = None,
    hasta: date | None = None,
    user_id: str | None = None,
    hechos: Mapping[str, Any] | None = None,
    avisos_db: str | None = None,
) -> Resultado | None:
    """The user answered a notice with a button. None when the notice is not the case's.

    «No me interesa» is a rejection for the default time, which a proposal worth more than twice as much may
    reopen once; «Ya lo hice» an acceptance; «Recuérdamelo más tarde» a rejection until ``hasta`` (a week by
    default). The notice leaves the inbox.
    """
    aviso = leer_aviso(avisos_db or db_path, case_id, notification_id)
    if aviso is None:
        return None
    ambito = ambito_de_aviso(aviso.kind, aviso.key)
    if ambito is None:
        raise DecisionInvalidaError("El resumen semanal no se puede responder: responde a cada aviso.")
    hoy = reloj.hoy()
    condiciones: list[Mapping[str, Any]] = []
    caducidad: date | None | Literal["por_defecto"] = POR_DEFECTO
    if boton == "recuerdamelo":
        caducidad = hasta or date.fromordinal(hoy.toordinal() + DIAS_RECORDAR_POR_DEFECTO)
    elif hasta is not None:
        caducidad = hasta
    if boton == "no_me_interesa" and aviso.amount is not None and aviso.amount > 0:
        condiciones.append(
            {
                "tipo": "mejora",
                "factor": FACTOR_MEJORA_POR_DEFECTO,
                "importe_referencia": aviso.amount,
                "descripcion": f"si aparece una propuesta que suponga más del doble ({eur(aviso.amount)})",
            }
        )
    texto = " ".join((razon or "").split())
    return registrar(
        db_path,
        case_id=case_id,
        ambito=ambito.respuesta,
        tipo=_TIPO_BOTON[boton],
        razon=f"{_RAZON_BOTON[boton]}: {texto}" if texto else _RAZON_BOTON[boton],
        fuente="boton",
        boton=boton,
        user_id=user_id,
        caducidad=caducidad,
        condiciones=condiciones,
        propuesta={"titulo": aviso.title[:300], "importe": aviso.amount, "clave_aviso": aviso.key[:300]},
        hechos=hechos,
        avisos_db=avisos_db,
        retirar_ids=[aviso.id],
    )


def desde_accion(
    db_path: str,
    *,
    case_id: str,
    modulo: str,
    herramienta: str,
    resumen: str,
    decision: Literal["rechazo", "aceptacion"],
    user_id: str | None = None,
) -> Decision | None:
    """The user confirmed or rejected a module's pending action. Never raises (logged instead)."""
    try:
        verbo = "Rechazaste" if decision == "rechazo" else "Confirmaste"
        return registrar(
            db_path,
            case_id=case_id,
            ambito=ambito_de_accion(modulo, herramienta),
            tipo=decision,
            razon=f"{verbo} en «Acciones pendientes»: {resumen}"[:500],
            fuente="accion",
            user_id=user_id,
            propuesta={"titulo": resumen[:300]},
        ).decision
    except Exception:
        logger.exception("Could not record the decision on action %s of case %s", herramienta, case_id)
        return None


def describir(decision: Decision, evaluacion: Evaluacion | None = None) -> dict[str, Any]:
    """A decision as the API and the agent show it: its data, its state and a Spanish label."""
    datos = decision.model_dump(mode="json")
    datos["etiqueta"] = etiqueta(decision.ambito)
    datos["modulo"] = decision.modulo
    if evaluacion is not None:
        datos["estado"] = evaluacion.estado
        datos["motivo_estado"] = evaluacion.motivo
    return datos


__all__ = [
    "POR_DEFECTO",
    "DecisionInvalidaError",
    "Resultado",
    "afecta_al_optimizador",
    "borrar",
    "decisiones_del_caso",
    "desde_accion",
    "desde_aviso",
    "describir",
    "editar",
    "evaluadas",
    "leer_aviso",
    "registrar",
    "retirar_avisos",
]

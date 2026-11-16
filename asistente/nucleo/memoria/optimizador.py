"""The user's decisions as constraints of the tax optimiser.

:func:`optimizar_con_memoria` is :func:`asistente.ambitos.fiscal.calculo.optimizador.optimizar_desde_hechos` for a case, with
the case's rejections, restrictions and «ya lo hice» in force turned into :class:`~asistente.ambitos.fiscal.calculo.optimizador.Restricciones`
(the optimiser itself is unchanged):

=======================================  ====================================================================
Decision in force about                   Constraint
=======================================  ====================================================================
``fiscal:plan_pensiones[:contribuyente]``  ``aportacion_maxima_pensiones = 0``
``fiscal:plan_pensiones[:conyuge]``        ``aportacion_maxima_pensiones_conyuge = 0``
``fiscal:donativo``                        no donation (``donativo_previsto = 0``, no budget, not recurrent)
``fiscal:perdidas[:contribuyente|conyuge]``  that holder's latent losses are left out
=======================================  ====================================================================

A decision covers its narrower ámbitos (``fiscal:plan_pensiones`` both spouses; ``fiscal`` every lever).
The plan's ``supuestos`` say, first, which lever was left out and why (the user's words and date), and the
optimiser's own «not available» entry for it is replaced by that reason: the plan never invites the user to
do what they rejected (the generic «si pensabas donar…» note and the «tell me your losses» request go).

A decision whose reopening condition holds, or that expired, is not applied: the plan considers the lever
again and its ``supuestos`` say what the user decided and what changed. A **preference** about the filing
mode is mentioned, not imposed (the plan compares both modes). A rejection or restriction about a filing mode
(``fiscal:tributacion:conjunta`` / ``…:individual``) goes to ``Restricciones.modalidades_excluidas``: the
optimiser does not compare that mode and the plan says why (when it is the only mode that can be computed,
the optimiser keeps it and says so in ``avisos``). «Ya lo hice» about a filing mode excludes nothing.

Rule for the run's own values: a decision in force always wins over the request (a figure in it never brings
a rejected lever back); the user lifts it by changing the decision (``registrar_decision`` in the chat, with
their literal words, or «Lo que sé de tus preferencias»). The request's own exclusions are kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from asistente.ambitos.fiscal.calculo.optimizador import (
    MODALIDAD_DESCARTADA,
    PalancaNoDisponible,
    PlanOptimo,
    Restricciones,
    normalizar_preferencias,
    optimizar_desde_hechos,
)
from asistente.ambitos.fiscal.profile import PREFERENCIAS_OPTIMIZACION, DatosInvalidosError
from asistente.nucleo.memoria import reloj
from asistente.nucleo.memoria.ambitos import etiqueta
from asistente.nucleo.memoria.modelo import BLOQUEAN_OPTIMIZADOR, Decision, Evaluacion, cubre, evaluar, fecha_es
from asistente.nucleo.memoria.servicio import decisiones_del_caso
from asistente.plataforma import settings

_PALANCAS = {
    "plan_pensiones_contribuyente": "fiscal:plan_pensiones:contribuyente",
    "plan_pensiones_conyuge": "fiscal:plan_pensiones:conyuge",
    "donativo": "fiscal:donativo",
    "perdidas_contribuyente": "fiscal:perdidas:contribuyente",
    "perdidas_conyuge": "fiscal:perdidas:conyuge",
}
# «Not available» entries of a blocked lever that would invite the user to do it anyway.
_QUITAR = {
    "donativo": ("donativo", "donativo_autonomico"),
    "perdidas_contribuyente": ("perdidas_contribuyente",),
    "perdidas_conyuge": ("perdidas_conyuge",),
}


@dataclass
class Ajuste:
    """What the decisions change in one optimiser run."""

    # Constraint fields to set over the run's own.
    restricciones: dict[str, Any] = field(default_factory=dict)
    # Lever id -> the decision that blocks it.
    bloqueadas: dict[str, Decision] = field(default_factory=dict)
    supuestos: list[str] = field(default_factory=list)
    # Filing modes rejected ("conjunta" / "individual") -> decision.
    modalidad_rechazada: dict[str, Decision] = field(default_factory=dict)

    @property
    def vacio(self) -> bool:
        return not (self.restricciones or self.supuestos or self.modalidad_rechazada)

    def no_se_propone(self, *, con_conyuge: bool) -> list[str]:
        """One line per decision that blocks levers (the spouse's only when the plan has a spouse)."""
        lineas: list[str] = []
        escritas: set[str] = set()
        for decision in self.bloqueadas.values():
            if decision.id in escritas:
                continue
            nombres = [
                etiqueta(_PALANCAS[p])
                for p, d in self.bloqueadas.items()
                if d.id == decision.id and (con_conyuge or not p.endswith("_conyuge"))
            ]
            if not nombres:
                continue
            escritas.add(decision.id)
            que = " ni ".join(f"«{n}»" for n in nombres)
            if decision.tipo == "aceptacion":
                lineas.append(
                    f"No se propone {que}: {_razon(decision)}, que ya lo habías hecho. Para que el cálculo lo tenga "
                    "en cuenta, indica el importe en tus datos (lo aportado, donado o vendido este año)."
                )
                continue
            lineas.append(
                f"No se propone {que}: {_razon(decision)}. "
                "Si has cambiado de idea, dilo o cámbialo en «Lo que sé de tus preferencias»."
            )
        return lineas


def _razon(decision: Decision) -> str:
    texto = f"el {fecha_es(decision.fecha.date())} nos dijiste «{decision.razon}»"
    if decision.caducidad is not None:
        texto += f" (lo tendré en cuenta hasta el {fecha_es(decision.caducidad)})"
    return texto


def _bloqueo(decisiones: list[tuple[Decision, Evaluacion]], ambito: str) -> tuple[Decision | None, list[str]]:
    """The decision in force that blocks ``ambito`` (if any) and notes about reopened ones."""
    notas: list[str] = []
    for decision, evaluacion in decisiones:
        if decision.tipo not in BLOQUEAN_OPTIMIZADOR or not cubre(decision.ambito, ambito):
            continue
        if evaluacion.vigente:
            return decision, []
        if evaluacion.estado in ("reabrible", "caducada"):
            notas.append(
                f"El {fecha_es(decision.fecha.date())} nos dijiste «{decision.razon}» sobre «{etiqueta(decision.ambito)}», "
                f"pero {evaluacion.motivo}: el plan vuelve a tenerlo en cuenta. Si sigue sin interesarte, dilo."
            )
    return None, notas


def _perdidas_efectivas(restricciones: Mapping[str, Any], hechos: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "perdidas_latentes" in restricciones:
        return [dict(p) if isinstance(p, Mapping) else p.model_dump() for p in restricciones["perdidas_latentes"] or []]
    if hechos.get(PREFERENCIAS_OPTIMIZACION) is None:
        return []
    try:
        return list(normalizar_preferencias(hechos[PREFERENCIAS_OPTIMIZACION]).get("perdidas_latentes", []))
    except DatosInvalidosError:
        return []


def ajuste_para_optimizador(
    decisiones: list[Decision],
    *,
    hoy: date,
    hechos: Mapping[str, Any] | None = None,
    restricciones: Mapping[str, Any] | None = None,
) -> Ajuste:
    """Turn the decisions of a case into constraints and assumptions for one optimiser run."""
    hechos = dict(hechos or {})
    pedidas = dict(restricciones or {})
    evaluadas = [(d, evaluar(d, hoy=hoy, hechos=hechos)) for d in decisiones]
    ajuste = Ajuste()
    notas_vistas: set[str] = set()
    for palanca, ambito in _PALANCAS.items():
        decision, notas = _bloqueo(evaluadas, ambito)
        for nota in notas:
            if nota not in notas_vistas:
                notas_vistas.add(nota)
                ajuste.supuestos.append(nota)
        if decision is None:
            continue
        ajuste.bloqueadas[palanca] = decision
    pensiones = {"plan_pensiones_contribuyente": "aportacion_maxima_pensiones"}
    pensiones["plan_pensiones_conyuge"] = "aportacion_maxima_pensiones_conyuge"
    for palanca, campo in pensiones.items():
        if palanca in ajuste.bloqueadas:
            ajuste.restricciones[campo] = 0
    if "donativo" in ajuste.bloqueadas:
        ajuste.restricciones.update(
            donativo_previsto=0, presupuesto_donativos=None, donativo_recurrente_importe_anterior=None
        )
    sin_perdidas = {t for t in ("contribuyente", "conyuge") if f"perdidas_{t}" in ajuste.bloqueadas}
    if sin_perdidas:
        ajuste.restricciones["perdidas_latentes"] = [
            p for p in _perdidas_efectivas(pedidas, hechos) if p.get("titular", "contribuyente") not in sin_perdidas
        ]
    # Filing mode: a preference is mentioned; a rejection or restriction excludes the mode.
    for modalidad in ("conjunta", "individual"):
        ambito = f"fiscal:tributacion:{modalidad}"
        decision, notas = _bloqueo(evaluadas, ambito)
        ajuste.supuestos.extend(n for n in notas if n not in notas_vistas)
        if decision is not None and decision.tipo in ("rechazo", "restriccion"):
            ajuste.modalidad_rechazada[modalidad] = decision
        preferida = next(
            (
                d
                for d, e in evaluadas
                if d.tipo == "preferencia" and d.ambito == ambito and e.estado not in ("sustituida",)
            ),
            None,
        )
        if preferida is not None:
            ajuste.supuestos.append(
                f"Prefieres «{etiqueta(ambito)}» ({_razon(preferida)}): el plan compara igualmente las dos "
                "modalidades y te dice cuál sale mejor; la elección es tuya al presentar."
            )
    if ajuste.modalidad_rechazada:
        pedidas_modos = list(pedidas.get("modalidades_excluidas") or [])
        ajuste.restricciones["modalidades_excluidas"] = [
            *pedidas_modos,
            *(m for m in ajuste.modalidad_rechazada if m not in pedidas_modos),
        ]
    return ajuste


def aplicar_al_plan(plan: PlanOptimo, ajuste: Ajuste) -> PlanOptimo:
    """The plan with the decisions' assumptions first and no invitation to do what the user rejected."""
    if ajuste.vacio and not ajuste.bloqueadas:
        return plan
    quitar = {i for palanca in ajuste.bloqueadas for i in _QUITAR.get(palanca, ())}
    no_disponibles: list[PalancaNoDisponible] = []
    for nd in plan.no_disponibles:
        if nd.id in quitar:
            continue
        decision = ajuste.bloqueadas.get(nd.id)
        if decision is not None and nd.causa == "restricciones":
            nd = nd.model_copy(update={"detalle": f"No se propone: {_razon(decision)}."})
        no_disponibles.append(nd)
    supuestos = [*ajuste.no_se_propone(con_conyuge=plan.con_conyuge), *ajuste.supuestos]
    # An excluded filing mode the optimiser could have compared: its generic line becomes the user's words.
    genericas: set[str] = set()
    for modalidad, decision in ajuste.modalidad_rechazada.items():
        generica = MODALIDAD_DESCARTADA[modalidad]
        genericas.add(generica)
        if generica in plan.supuestos:
            supuestos.append(
                f"No se propone «{etiqueta(f'fiscal:tributacion:{modalidad}')}»: {_razon(decision)}. "
                "Si has cambiado de idea, dilo o cámbialo en «Lo que sé de tus preferencias»."
            )
    return plan.model_copy(
        update={
            "supuestos": [*supuestos, *(s for s in plan.supuestos if s not in supuestos and s not in genericas)],
            "no_disponibles": no_disponibles,
        }
    )


def optimizar_con_memoria(
    *,
    case_id: str | None,
    db_path: str | None = None,
    hoy: date | None = None,
    **kwargs: Any,
) -> PlanOptimo:
    """``optimizar_desde_hechos(**kwargs)`` with the case's decisions as constraints (see the module docs).

    Without a case, or a case without decisions, it is exactly ``optimizar_desde_hechos``.
    """
    if case_id is None:
        return optimizar_desde_hechos(**kwargs)
    decisiones = decisiones_del_caso(db_path or settings.DECISION_STATE_DB_PATH, case_id)
    if not decisiones:
        return optimizar_desde_hechos(**kwargs)
    restricciones = kwargs.get("restricciones")
    if isinstance(restricciones, Restricciones):
        pedidas = restricciones.model_dump(include=set(restricciones.model_fields_set))
    else:
        pedidas = dict(restricciones or {})
    hechos = dict(kwargs.get("facts") or {})
    ajuste = ajuste_para_optimizador(decisiones, hoy=hoy or reloj.hoy(), hechos=hechos, restricciones=pedidas)
    if ajuste.restricciones:
        kwargs["restricciones"] = {**pedidas, **ajuste.restricciones}
    return aplicar_al_plan(optimizar_desde_hechos(**kwargs), ajuste)


__all__ = ["Ajuste", "ajuste_para_optimizador", "aplicar_al_plan", "optimizar_con_memoria"]

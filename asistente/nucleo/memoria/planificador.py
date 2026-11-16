"""The user's decisions as restrictions of the household planner.

:func:`planificar_con_memoria` is :func:`hacienda_gpt.planificador.motor.planificar` for a case, with the
case's rejections, restrictions and «ya lo hice» in force turned into
:class:`~hacienda_gpt.planificador.modelos.RestriccionesPlan` (the planner does not import this package):

=================================================  =================================================
Decision in force about                              Restriction of the plan
=================================================  =================================================
``fiscal:plan_pensiones[:contribuyente]``            ``excluidas`` += ``plan_pensiones_contribuyente``
``fiscal:plan_pensiones[:conyuge]``                  ``excluidas`` += ``plan_pensiones_conyuge``
``fiscal:donativo``                                  ``excluidas`` += ``donativo``
``hipoteca`` / ``hipoteca:amortizar``                ``excluidas`` += ``amortizar_hipoteca``
``hipoteca:amortizar:plazo`` / ``…:cuota``           ``modalidades_excluidas`` += that way of repaying
``fiscal:tributacion:conjunta`` / ``…:individual``   ``tributacion_excluida`` += that filing mode
=================================================  =================================================

(the planner's ``modalidades_excluidas`` are the ways of repaying the mortgage; the filing modes go to
``tributacion_excluida``, which the planner hands to the tax optimiser's ``Restricciones.modalidades_excluidas``).
A decision covers its narrower ámbitos (``fiscal`` every tax lever, ``hipoteca`` the repayment). Only
``rechazo`` and ``restriccion`` exclude a filing mode or a way of repaying: «ya lo hice» about a filing mode
means the user files that way, not that they reject it.

**Rule for the request's own values** (the same as :mod:`hacienda_gpt.memoria.optimizador`): the request can
only add restrictions; a decision in force always holds, even against a figure of the request (a cap, a
planned donation, a maximum contribution). The user lifts it by saying so now: in the chat the agent records
it with ``registrar_decision`` (checked against the user's literal words; the new decision about the same
ámbito replaces the old one), on the web in «Lo que sé de tus preferencias». A figure the model or a form puts
in the request is never taken as the user changing their mind.

The plan's ``supuestos`` say, first, which lever was left out and why (the user's words and date); a decision
whose reopening condition holds, or that expired, is not applied and the plan says what changed.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from hacienda_gpt import settings
from hacienda_gpt.memoria import reloj
from hacienda_gpt.memoria.modelo import Decision, evaluar
from hacienda_gpt.memoria.optimizador import _bloqueo, _razon
from hacienda_gpt.memoria.servicio import decisiones_del_caso
from hacienda_gpt.planificador.modelos import NOMBRES_PALANCAS, EntradaPlan, PlanHogar, RestriccionesPlan
from hacienda_gpt.planificador.motor import planificar
from hacienda_gpt.tax.optimizador import MODALIDAD_DESCARTADA

# Planner lever -> the ámbito a decision must cover to exclude it.
PALANCAS_PLANIFICADOR: dict[str, str] = {
    "plan_pensiones_contribuyente": "fiscal:plan_pensiones:contribuyente",
    "plan_pensiones_conyuge": "fiscal:plan_pensiones:conyuge",
    "donativo": "fiscal:donativo",
    "amortizar_hipoteca": "hipoteca:amortizar",
}
MODALIDADES_HIPOTECA: dict[str, str] = {
    "plazo": "hipoteca:amortizar:plazo",
    "cuota": "hipoteca:amortizar:cuota",
}
TRIBUTACION: dict[str, str] = {
    "conjunta": "fiscal:tributacion:conjunta",
    "individual": "fiscal:tributacion:individual",
}
_NOMBRES_MODALIDAD = {
    "plazo": "Amortizar reduciendo plazo",
    "cuota": "Amortizar reduciendo cuota",
    "conjunta": "Declaración conjunta",
    "individual": "Declaraciones individuales",
}
ORIGEN = "memoria de decisiones"
_MAX_MOTIVO = 300


@dataclass
class AjustePlan:
    """What the decisions change in one planner run."""

    excluidas: dict[str, Decision] = field(default_factory=dict)
    modalidades: dict[str, Decision] = field(default_factory=dict)
    tributacion: dict[str, Decision] = field(default_factory=dict)
    # Notes about decisions that no longer hold (reopened or expired).
    notas: list[str] = field(default_factory=list)

    @property
    def vacio(self) -> bool:
        return not (self.excluidas or self.modalidades or self.tributacion or self.notas)

    def supuestos(self, aplica: Callable[[str], bool]) -> list[str]:
        """One line per decision applied (several levers of one decision in one line) to what ``aplica`` says
        the plan could have proposed, then the notes."""
        lineas: list[str] = []
        grupos: dict[str, tuple[Decision, list[str]]] = {}
        for clave, decision in self.excluidas.items():
            if aplica(clave):
                grupos.setdefault(decision.id, (decision, []))[1].append(NOMBRES_PALANCAS[clave])
        for clave, decision in (*self.modalidades.items(), *self.tributacion.items()):
            if aplica(clave):
                grupos.setdefault(decision.id, (decision, []))[1].append(_NOMBRES_MODALIDAD[clave])
        for decision, nombres in grupos.values():
            que = " ni ".join(f"«{n}»" for n in nombres)
            if decision.tipo == "aceptacion":
                lineas.append(
                    f"No se propone {que}: {_razon(decision)}, que ya lo habías hecho. Para que el cálculo lo tenga "
                    "en cuenta, indica el importe en tus datos."
                )
            else:
                lineas.append(
                    f"No se propone {que}: {_razon(decision)}. "
                    "Si has cambiado de idea, dilo o cámbialo en «Lo que sé de tus preferencias»."
                )
        return [*lineas, *self.notas]


def ajuste_para_planificador(
    decisiones: list[Decision], *, hoy: date, hechos: Mapping[str, Any] | None = None
) -> AjustePlan:
    """Turn the decisions of a case into restrictions of one planner run."""
    evaluadas = [(d, evaluar(d, hoy=hoy, hechos=dict(hechos or {}))) for d in decisiones]
    ajuste = AjustePlan()

    def anotar(notas: list[str]) -> None:
        ajuste.notas.extend(n for n in notas if n not in ajuste.notas)

    for palanca, ambito in PALANCAS_PLANIFICADOR.items():
        decision, notas = _bloqueo(evaluadas, ambito)
        anotar(notas)
        if decision is not None:
            ajuste.excluidas[palanca] = decision
    for destino, catalogo in ((ajuste.modalidades, MODALIDADES_HIPOTECA), (ajuste.tributacion, TRIBUTACION)):
        for clave, ambito in catalogo.items():
            decision, notas = _bloqueo(evaluadas, ambito)
            anotar(notas)
            if decision is not None and decision.tipo in ("rechazo", "restriccion"):
                destino[clave] = decision
    if "amortizar_hipoteca" in ajuste.excluidas:
        ajuste.modalidades.clear()  # the whole repayment is out: its ways need no restriction of their own
    return ajuste


def aplicar_a_la_entrada(entrada: EntradaPlan, ajuste: AjustePlan) -> EntradaPlan:
    """The request with the decisions' restrictions added (never removing the request's own)."""
    if not (ajuste.excluidas or ajuste.modalidades or ajuste.tributacion):
        return entrada
    r = entrada.restricciones
    motivos: dict[str, str] = {str(clave): texto for clave, texto in r.motivos.items()}
    for palanca, decision in ajuste.excluidas.items():
        if palanca not in r.excluidas or palanca not in motivos:
            motivos[palanca] = _motivo(decision)
    pidio = bool(r.excluidas or r.modalidades_excluidas or r.tributacion_excluida or r.maximos)
    origen = ORIGEN if not pidio else f"{r.origen or 'usuario'} y {ORIGEN}"[:60]
    restricciones = RestriccionesPlan.model_validate(
        {
            **r.model_dump(),
            "excluidas": [*r.excluidas, *(p for p in ajuste.excluidas if p not in r.excluidas)],
            "modalidades_excluidas": [
                *r.modalidades_excluidas,
                *(m for m in ajuste.modalidades if m not in r.modalidades_excluidas),
            ],
            "tributacion_excluida": [
                *r.tributacion_excluida,
                *(t for t in ajuste.tributacion if t not in r.tributacion_excluida),
            ],
            "motivos": motivos,
            "origen": origen,
        }
    )
    return entrada.model_copy(update={"restricciones": restricciones})


def _motivo(decision: Decision) -> str:
    texto = _razon(decision) + (", que ya lo habías hecho" if decision.tipo == "aceptacion" else "")
    return texto if len(texto) <= _MAX_MOTIVO else texto[: _MAX_MOTIVO - 1] + "…"


def aplicar_al_plan(plan: PlanHogar, ajuste: AjustePlan) -> PlanHogar:
    """The plan with the decisions' reasons first in its assumptions (only for what the plan could propose:
    the spouse's plan with a spouse, the mortgage with a loan, a filing mode the optimiser could compare)."""
    if ajuste.vacio:
        return plan
    con_conyuge = plan.plan_fiscal is not None and plan.plan_fiscal.con_conyuge

    def aplica(clave: str) -> bool:
        if clave == "plan_pensiones_conyuge":
            return con_conyuge
        if clave in ("amortizar_hipoteca", *MODALIDADES_HIPOTECA):
            return plan.hipoteca is not None
        if clave in TRIBUTACION:
            return MODALIDAD_DESCARTADA[clave] in plan.supuestos
        return True

    primeros = ajuste.supuestos(aplica)
    genericas = {MODALIDAD_DESCARTADA[t] for t in ajuste.tributacion}
    resto = [s for s in plan.supuestos if s not in primeros and s not in genericas]
    return plan.model_copy(update={"supuestos": [*primeros, *resto]})


def planificar_con_memoria(
    entrada: EntradaPlan | Mapping[str, Any],
    *,
    case_id: str | None,
    facts: Mapping[str, Any] | None = None,
    modulos_activos: Collection[str] = (),
    hoy: date,
    db_path: str | None = None,
) -> PlanHogar:
    """``planificar(entrada, …)`` with the case's decisions as restrictions (see the module docs).

    Without a case, or a case without decisions, it is exactly ``planificar``. Invalid input raises the same
    ``DatosInvalidosError`` as the planner.
    """
    if case_id is None:
        return planificar(entrada, facts=facts, modulos_activos=modulos_activos, hoy=hoy)
    decisiones = decisiones_del_caso(db_path or settings.DECISION_STATE_DB_PATH, case_id)
    if not decisiones:
        return planificar(entrada, facts=facts, modulos_activos=modulos_activos, hoy=hoy)
    if not isinstance(entrada, EntradaPlan):
        # Validation errors are the planner's own (Spanish message).
        try:
            entrada = EntradaPlan.model_validate(dict(entrada))
        except ValueError:
            return planificar(entrada, facts=facts, modulos_activos=modulos_activos, hoy=hoy)
    ajuste = ajuste_para_planificador(decisiones, hoy=reloj.hoy(), hechos=facts)
    plan = planificar(aplicar_a_la_entrada(entrada, ajuste), facts=facts, modulos_activos=modulos_activos, hoy=hoy)
    return aplicar_al_plan(plan, ajuste)


__all__ = [
    "MODALIDADES_HIPOTECA",
    "PALANCAS_PLANIFICADOR",
    "TRIBUTACION",
    "AjustePlan",
    "ajuste_para_planificador",
    "aplicar_a_la_entrada",
    "aplicar_al_plan",
    "planificar_con_memoria",
]

"""The central agent's ``planificar_hogar`` tool: the model states the problem, the planner computes.

``agent/tools.py`` registers :data:`PLANIFICAR_TOOL` and calls :func:`planificar_para_agente` with the case and
the evidence ledger. The plan is registered as one citable fragment ``[F#]`` (source authority CALCULADORA), so
the verifier checks the figures of the answer against it like any other calculation.
"""

from __future__ import annotations

from collections.abc import Collection
from decimal import Decimal
import logging
from typing import Any

from hacienda_gpt.agent.evidence import EvidenceLedger
from hacienda_gpt.agent.providers.base import ToolSpec
from hacienda_gpt.decision.schemas import CaseState, parse_fiscal_year
from hacienda_gpt.planificador.modelos import PALANCAS, PlanHogar
from hacienda_gpt.planificador.motor import planificar
from hacienda_gpt.proactive.jobs import hoy_en_espana
from hacienda_gpt.tax.params import EjercicioNoDisponibleError
from hacienda_gpt.tax.profile import DatosInvalidosError

logger = logging.getLogger(__name__)

_NUMERO = {"type": "number", "minimum": 0}

PLANIFICAR_TOOL = ToolSpec(
    name="planificar_hogar",
    description=(
        "Planificador determinista del hogar: dado el dinero que el hogar puede destinar este año (y/o un excedente "
        "mensual) busca el MEJOR REPARTO entre amortizar la hipoteca (reducir plazo o cuota, con la comisión legal), "
        "aportar a planes de pensiones (cada cónyuge), un donativo dentro del presupuesto que diga el usuario y "
        "mantener liquidez (con un colchón de emergencia). Calcula las interacciones: el IRPF de cada combinación "
        "con el optimizador fiscal (incluida la deducción por vivienda anterior a 2013), los intereses y comisiones "
        "con el módulo de hipoteca, y si el plan cambia alguna ayuda pública (con el módulo de ayudas, si está "
        "activado). Objetivo: posición neta al final del horizonte frente a dejar el dinero líquido a la "
        "rentabilidad que diga el usuario; las pensiones se muestran como diferimiento y como ahorro definitivo. "
        "Úsala para «tengo X €, ¿amortizo, aporto al plan o los guardo?». Tú solo formulas el problema con lo que "
        "diga el usuario (importe, horizonte, colchón, rentabilidad alternativa, restricciones): NO inventes "
        "cifras ni supuestos, y no recomiendes inversiones. Traslada los supuestos y lo no modelado del resultado. "
        "Los datos fiscales, de la hipoteca y de las ayudas salen del expediente."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "importe_disponible": {**_NUMERO, "description": "Dinero que el hogar puede destinar ahora, en euros."},
            "excedente_mensual": {**_NUMERO, "description": "Lo que ahorra cada mes y se sumaría hasta diciembre."},
            "horizonte_anos": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Años del horizonte."},
            "rentabilidad_alternativa_pct": {
                "type": "number",
                "minimum": -20,
                "maximum": 30,
                "description": "Rentabilidad anual neta (en %) que el usuario obtendría con el dinero que no use. "
                "Solo la que él diga.",
            },
            "colchon_objetivo": {**_NUMERO, "description": "Colchón de emergencia que quiere mantener, en euros."},
            "gastos_mensuales": {**_NUMERO, "description": "Gastos del hogar al mes (para el colchón por meses)."},
            "meses_colchon": {"type": "integer", "minimum": 0, "maximum": 36},
            "liquidez_actual": {**_NUMERO, "description": "Ahorros líquidos que ya tiene aparte de ese dinero."},
            "penalizacion_liquidez": {
                **_NUMERO,
                "description": "Euros a restar por cada euro por debajo del colchón; sin ella el colchón es un límite.",
            },
            "tipo_rescate_pct": {
                **_NUMERO,
                "description": "Tipo marginal que espera pagar al rescatar el plan de pensiones, en %.",
            },
            "valorar_pensiones": {"type": "string", "enum": ["diferimiento", "definitivo"]},
            "aportacion_maxima_pensiones": {**_NUMERO, "description": "Máximo que el usuario aportaría a su plan."},
            "aportacion_maxima_pensiones_conyuge": {**_NUMERO, "description": "Lo mismo para el cónyuge."},
            "acepta_iliquidez": {"type": "boolean", "description": "false si no quiere inmovilizar dinero."},
            "donativo_previsto": {**_NUMERO, "description": "Donativo que quiere hacer con ese dinero."},
            "presupuesto_donativos": {**_NUMERO, "description": "Lo máximo que donaría."},
            "parte_hipoteca_conyuge": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Parte de la hipoteca que paga el cónyuge (0,5 si la pagan a medias).",
            },
            "excluidas": {
                "type": "array",
                "items": {"type": "string", "enum": list(PALANCAS)},
                "description": "Palancas que el usuario ha descartado.",
            },
            "modalidades_excluidas": {"type": "array", "items": {"type": "string", "enum": ["plazo", "cuota"]}},
            "proteger_ayudas": {
                "type": "boolean",
                "description": "false para permitir planes que reduzcan una ayuda que hoy le corresponde.",
            },
            "ejercicio": {"type": "integer", "description": "Año fiscal. Por defecto, el del expediente o el actual."},
            "datos": {"type": "object", "description": "Cifras fiscales del usuario que añadir o corregir."},
            "conyuge": {"type": "object", "description": "Cifras fiscales del cónyuge."},
        },
    },
)

_RESTRICCIONES = ("excluidas", "modalidades_excluidas")


def _eur(importe: Decimal) -> str:
    texto = f"{abs(importe):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if importe < 0 else ''}{texto} €"


def texto_plan(plan: PlanHogar) -> str:
    """The plan as plain Spanish text (what the agent reads and cites)."""
    v = plan.valoracion
    lineas = [f"Plan del hogar {plan.ejercicio} (planificador determinista)", plan.explicacion, "", "Reparto:"]
    lineas += [f"- {a.nombre}: {_eur(a.importe)}. {a.detalle}" for a in plan.asignacion]
    lineas += ["", f"Posición neta a {plan.fecha_horizonte:%d/%m/%Y} frente a dejarlo todo líquido:"]
    lineas += [f"- {c.concepto}: {_eur(c.importe)} [{c.naturaleza}; {c.modulo}]" for c in v.componentes]
    lineas.append(
        f"- Total ({plan.vista}): {_eur(v.total)}; vista diferimiento {_eur(v.total_diferimiento)}; vista "
        f"definitiva {_eur(v.total_definitivo)}; en euros de hoy {_eur(v.valor_actual)}."
    )
    lineas.append(f"- IRPF {plan.ejercicio} con el plan: {_eur(v.impuesto_ejercicio)} ({v.tributacion}).")
    if plan.palancas:
        lineas += ["", "Cada palanca (dentro del plan / sola):"]
        lineas += [
            f"- {p.nombre}: {_eur(p.valor_marginal)} / {_eur(p.valor_aislado)} (sola con {_eur(p.importe_aislado)}); "
            f"{p.naturaleza}."
            for p in plan.palancas
        ]
        lineas.append(
            f"- Suma por separado {_eur(plan.suma_aislados)}; el plan {_eur(v.total)}; diferencia {_eur(plan.interaccion)}."
        )
    if plan.explicacion_interaccion:
        lineas += ["", "Por qué la combinación difiere:"] + [f"- {x}" for x in plan.explicacion_interaccion]
    if plan.hipoteca is not None and plan.hipoteca.importe > 0:
        h = plan.hipoteca
        lineas += [
            "",
            f"Hipoteca: capital pendiente {_eur(h.capital_pendiente)}, cuota {_eur(h.cuota_actual)}"
            + (f" → {_eur(h.cuota_nueva)}" if h.cuota_nueva is not None else "")
            + f", fin {h.fin_actual:%d/%m/%Y}"
            + (f" → {h.fin_nuevo:%d/%m/%Y}" if h.fin_nuevo is not None else "")
            + f"; comisión {_eur(h.comision)} ({'máximo legal' if h.comision_es_maximo else 'según la ley'}); "
            f"intereses ahorrados en todo el préstamo {_eur(h.intereses_ahorrados_total)}.",
        ]
    cambios = [e for e in plan.ayudas]
    if cambios:
        lineas += ["", "Ayudas públicas (módulo de ayudas):"]
        lineas += [
            f"- {e.nombre}: {e.estado_antes} → {e.estado_despues}. Mide: {e.magnitud}. {e.explicacion}" for e in cambios
        ]
    if plan.no_usadas:
        lineas += ["", "No usadas:"] + [f"- {n.nombre} ({n.causa}): {n.detalle}" for n in plan.no_usadas]
    lineas += ["", "Supuestos:"] + [f"- {s}" for s in plan.supuestos]
    if plan.avisos:
        lineas += ["", "Avisos:"] + [f"- {a}" for a in plan.avisos]
    lineas += ["", "No modelado:"] + [f"- {n}" for n in plan.no_modelado]
    b = plan.busqueda
    lineas += ["", f"Búsqueda {b.metodo}: {b.combinaciones} combinaciones, paso {_eur(b.resolucion)}. {b.nota}"]
    return "\n".join(lineas)


def _modulos_activos(case: CaseState | None) -> Collection[str]:
    try:
        if case is not None:
            from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso

            return {m.clave for m in modulos_activos_para_caso(case.case_id)}
        from hacienda_gpt.modulos.preferencias import modulos_activos_para

        return {m.clave for m in modulos_activos_para(None, None)}
    except Exception:  # the plan still runs with the tax module alone
        logger.exception("could not read the active modules for the planner")
        return {"fiscal"}


def planificar_para_agente(
    arguments: dict[str, Any], *, case: CaseState | None, ledger: EvidenceLedger
) -> tuple[str, str, bool]:
    """(content, summary, is_error) of a ``planificar_hogar`` call: the plan registered as a citable fragment."""
    datos = dict(arguments)
    restricciones = {clave: datos.pop(clave) for clave in _RESTRICCIONES if clave in datos}
    if restricciones:
        datos["restricciones"] = {**restricciones, "origen": "usuario"}
    if datos.get("ejercicio") is None and case is not None:
        ano = parse_fiscal_year(case.tax_period)
        if ano is not None:
            datos["ejercicio"] = ano
    facts = {fact.name: fact.value for fact in case.facts} if case is not None else {}
    try:
        plan = planificar(datos, facts=facts, modulos_activos=_modulos_activos(case), hoy=hoy_en_espana())
    except (DatosInvalidosError, EjercicioNoDisponibleError) as exc:
        return str(exc), str(exc)[:120], True
    texto = texto_plan(plan)
    fragmento = ledger.register(
        text=texto,
        metadata={
            "title": f"Plan del hogar {plan.ejercicio}",
            "source": f"calculadora://planificador/{plan.ejercicio}/{len(ledger) + 1}",
            "source_authority": "CALCULADORA",
            "section": "Planificador del hogar",
        },
    )
    usadas = ", ".join(f"{a.nombre} {_eur(a.importe)}" for a in plan.asignacion if a.id != "liquidez" and a.importe)
    resumen = f"posición neta {_eur(plan.valoracion.total)} ({usadas or 'todo líquido'}): {fragmento.fragment_id}"
    return f"[{fragmento.fragment_id}] {texto}", resumen, False


__all__ = ["PLANIFICAR_TOOL", "planificar_para_agente", "texto_plan"]

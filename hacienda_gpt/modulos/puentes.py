"""Bridges from household modules to the tax profile: figures a module knows that a tax fact needs.

A bridge never writes anything. It PROPOSES a value for a tax fact the case does not have yet, with the
figures it comes from; the user accepts it (``PUT /cases/{id}/facts/{nombre}``) or ignores it. What the
calculator does with the value (limits, which payments enter the base) stays the tax module's job.

Bridges today:

* ``hipoteca`` → ``pagos_vivienda_habitual_pre2013`` (what the IRPF calculator uses for the transitional
  deduction for the main home bought before 2013, disposición transitoria 18.ª LIRPF): offered when the loan in
  the profile says it financed the main home (``vivienda_habitual``) bought before 2013
  (``adquisicion_antes_2013`` exactly true) and the fact is missing. The figure is what art. 68.1.1.º LIRPF, in
  its wording in force on 31-12-2012, puts in the base "en el caso de financiación ajena" and the module knows
  for the case's fiscal year: the amortisation (capital of the instalments due that year plus early
  repayments), the interest, and the fees paid for early repayments ("demás gastos derivados"). The legal basis,
  with literal quotes, what is left out (purchase expenses, hedging and its income, other loan costs) and the
  conditions nobody checks (the deduction applied before 2013, earlier homes, the wealth test of art. 70.1) live
  in ``modulos/hipoteca/data/deduccion_vivienda.json`` (``modulos/hipoteca/deduccion.py``). The rate and the cap
  stay the calculator's.

A proposal is about the ámbito ``fiscal:propuesta:<puente>`` of the decision memory (``hacienda_gpt/memoria``):
a «no» the user said about it holds it back (``modulos/propuestas_chat.py``). A fact recorded from one has the
source ``puente:<puente>``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import importlib
import logging
from typing import Any

from hacienda_gpt.decision.schemas import CaseState, parse_fiscal_year
from hacienda_gpt.modulos.contrato import ContextoModulo, Modulo
from hacienda_gpt.modulos.perfil import perfil_de

logger = logging.getLogger(__name__)

HECHO_VIVIENDA_PRE2013 = "pagos_vivienda_habitual_pre2013"


@dataclass(frozen=True)
class PropuestaHecho:
    nombre: str
    valor: float
    titulo: str
    explicacion: str
    modulo: str
    desglose: dict[str, str] = field(default_factory=dict)


def eur(amount: Decimal) -> str:
    """«1.234,50 €» (Spanish thousands and decimal separators)."""
    text = f"{abs(amount):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} €"


def _hipoteca(case: CaseState, perfil: dict[str, Any], hoy: date) -> PropuestaHecho | None:
    if HECHO_VIVIENDA_PRE2013 in perfil:
        return None
    prestamo = perfil.get("hipoteca_prestamo")
    if not isinstance(prestamo, dict) or prestamo.get("adquisicion_antes_2013") is not True:
        return None
    if prestamo.get("vivienda_habitual") is False:
        return None
    ejercicio = parse_fiscal_year(case.tax_period) or hoy.year
    pagos_del_ejercicio = importlib.import_module("hacienda_gpt.modulos.hipoteca").pagos_del_ejercicio
    pagos = pagos_del_ejercicio(ContextoModulo(perfil=perfil, hoy=hoy, case_id=case.case_id), ejercicio)
    if not pagos:
        return None
    from hacienda_gpt.modulos.hipoteca.deduccion import base_deducible, cargar_fundamento, importes_deducibles

    fundamento = cargar_fundamento()
    # Only what the law puts in the base and the module knows (``incluido`` concepts), each one with its quote.
    importes = importes_deducibles(pagos, fundamento)
    total = base_deducible(pagos, fundamento)
    if total <= 0:
        return None
    hasta = " (las cuotas que vencen hasta el 31 de diciembre, según el cuadro)" if ejercicio >= hoy.year else ""
    limite = _limite_irpf(ejercicio)
    fuera = ", ".join(c.etiqueta.lower() for c in fundamento.no_incluidos())
    return PropuestaHecho(
        nombre=HECHO_VIVIENDA_PRE2013,
        valor=float(total),
        titulo=f"Lo que pagas de hipoteca en {ejercicio}: {eur(total)}",
        explicacion=(
            f"Tu préstamo es de la vivienda habitual comprada antes del "
            f"{_fecha_larga(fundamento.fecha_limite_adquisicion)}. Según su cuadro de amortización, en {ejercicio} "
            f"pagas {eur(total)}{hasta}: es lo que la ley cuenta del préstamo para la deducción por vivienda "
            "habitual del régimen transitorio (la amortización, los intereses y los demás gastos de la financiación; "
            "art. 68.1 LIRPF en su redacción de 2012, al que remite la disposición transitoria 18.ª). Solo procede "
            "si ya te aplicaste esa deducción por esta vivienda en algún ejercicio anterior a 2013. Si lo "
            f"confirmas, la calculadora del IRPF lo usará y aplicará ella sus límites{limite}. No incluye lo que "
            f"el módulo no sabe ({fuera}); si el préstamo es de varios titulares, cada uno declara lo que pagó. "
            "Comprueba la cifra con el certificado de tu banco y corrígela si hace falta."
        ),
        modulo="hipoteca",
        desglose={
            clave: eur(importe)
            for clave, importe in importes.items()
            if importe or clave in ("capital", "intereses", "amortizaciones_anticipadas")
        },
    )


def _limite_irpf(ejercicio: int) -> str:
    """« (como máximo 9.040 € al año por declarante, al 15 %)» from the IRPF parameters, or "" without them."""
    try:
        from hacienda_gpt.tax.params import load_params

        v = load_params(ejercicio).vivienda_habitual_transitoria
    except Exception:  # no parameters for that year: the calculator will say so itself
        return ""
    porcentaje = format((v.tipo * 100).normalize(), "f").replace(".", ",")
    return f" (cuenta como máximo {eur(v.base_maxima)} al año por declarante, al {porcentaje} %)"


_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _fecha_larga(dia: date) -> str:
    return f"{dia.day} de {_MESES[dia.month - 1]} de {dia.year}"


Puente = Callable[[CaseState, dict[str, Any], date], PropuestaHecho | None]
PUENTES: dict[str, Puente] = {"hipoteca": _hipoteca}


def ambito_de(propuesta: PropuestaHecho) -> str:
    """The decision-memory ámbito of a proposal: a «no» about it is ``fiscal:propuesta:<puente>``."""
    return f"fiscal:propuesta:{propuesta.modulo}"


def fuente_de(propuesta: PropuestaHecho) -> str:
    """``Fact.source`` of a fact recorded from a proposal (the provenance: which module's bridge)."""
    return f"puente:{propuesta.modulo}"


def propuestas_para(case: CaseState, modulos: Sequence[Modulo], *, hoy: date) -> list[PropuestaHecho]:
    """Proposals for this case from the active modules' bridges. A failing bridge is logged and skipped."""
    perfil = perfil_de(case)
    out: list[PropuestaHecho] = []
    for modulo in modulos:
        puente = PUENTES.get(modulo.clave)
        if puente is None:
            continue
        try:
            propuesta = puente(case, perfil, hoy)
        except Exception:
            logger.exception("Tax bridge of module %s failed for case %s", modulo.clave, case.case_id)
            continue
        if propuesta is not None:
            out.append(propuesta)
    return out


__all__ = ["HECHO_VIVIENDA_PRE2013", "PUENTES", "PropuestaHecho", "ambito_de", "eur", "fuente_de", "propuestas_para"]

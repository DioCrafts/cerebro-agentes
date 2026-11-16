"""Legal passages the planner quotes when it explains an interaction between modules.

Copied literally from the consolidated texts on www.boe.es (LIRPF, BOE-A-2006-20764, «Última actualización,
publicada el 11/11/2026»; Ley 19/2021 del ingreso mínimo vital, BOE-A-2021-21007, «Última actualización publicada
el 03/10/2026»), read on 16/11/2026. They justify WHICH magnitude a rule uses; no figure in them feeds a
computation. ``tests/planificador/test_textos.py`` checks each one is verbatim in an excerpt of its law kept in
``tests/planificador/fixtures``.

The quotes of the aid rules themselves (bono social, IMV, CAPI, birth benefit) are not duplicated here: they are
read from the aid module's data (``modulos/ayudas/data``), where they are already checked against the BOE.
"""

from __future__ import annotations

from hacienda_gpt.tax.optimizador import TEXTOS_LEGALES as TEXTOS_OPTIMIZADOR
from hacienda_gpt.tax.optimizador import TextoLegal

LIRPF = "BOE-A-2006-20764"
LEY_IMV = "BOE-A-2021-21007"

TEXTOS: dict[str, TextoLegal] = {
    t.referencia: t
    for t in (
        TextoLegal(
            referencia="art. 50.1 LIRPF",
            boe_id=LIRPF,
            texto="1. La base liquidable general estará constituida por el resultado de practicar en la base "
            "imponible general, exclusivamente y por este orden, las reducciones a que se refieren los artículos "
            "51, 53, 54, 55 y disposición adicional undécima de esta Ley, sin que pueda resultar negativa como "
            "consecuencia de dichas disminuciones.",
        ),
        TextoLegal(
            referencia="art. 51 LIRPF",
            boe_id=LIRPF,
            texto="Podrán reducirse en la base imponible general las siguientes aportaciones y contribuciones a "
            "sistemas de previsión social: 1. Aportaciones y contribuciones a planes de pensiones.",
        ),
        TextoLegal(
            referencia="art. 20.1.a) Ley 19/2021",
            boe_id=LEY_IMV,
            texto="a) Con carácter general las rentas se computarán por su valor íntegro, excepto las procedentes "
            "de actividades económicas, de arrendamientos de inmuebles o de regímenes especiales, que se computarán "
            "por su rendimiento neto.",
        ),
        TextoLegal(
            referencia="art. 20.2 Ley 19/2021",
            boe_id=LEY_IMV,
            texto="2. Para el cómputo de ingresos se tendrán en cuenta los obtenidos por los beneficiarios durante "
            "el ejercicio anterior a la solicitud.",
        ),
        TextoLegal(
            referencia="art. 20.3 Ley 19/2021",
            boe_id=LEY_IMV,
            texto="A la suma de ingresos detallados anteriormente se restará el importe del impuesto sobre la renta "
            "devengada y las cotizaciones sociales.",
        ),
        TextoLegal(
            referencia="art. 20.4.b) Ley 19/2021",
            boe_id=LEY_IMV,
            texto="Los activos no societarios son la suma de los siguientes conceptos: 1.º Los inmuebles, excluida "
            "la vivienda habitual. 2.º Las cuentas bancarias y depósitos.",
        ),
        TextoLegal(
            referencia="art. 20.4.b).4.º Ley 19/2021",
            boe_id=LEY_IMV,
            texto="4.º Las participaciones en planes, fondos de pensiones y sistemas alternativos similares.",
        ),
        TextoLegal(
            referencia="art. 20.4.b), pasivos, Ley 19/2021",
            boe_id=LEY_IMV,
            texto="Los pasivos no societarios incluirán las deudas y créditos existentes sobre los activos no "
            "societarios a fecha de presentación de la solicitud, excluidos los asociados a la vivienda habitual.",
        ),
    )
}
# The optimiser's own quotes the planner repeats (redemption of pension plans, the tax period).
for _ref in ("art. 17.2.a).3.ª LIRPF", "art. 12 LIRPF"):
    TEXTOS[_ref] = TEXTOS_OPTIMIZADOR[_ref]


def cita(referencia: str) -> str:
    """«referencia (BOE id): "texto"», as the plan prints a quote."""
    t = TEXTOS[referencia]
    return f"{t.referencia} ({t.boe_id}): «{t.texto}»"


__all__ = ["LEY_IMV", "LIRPF", "TEXTOS", "cita"]

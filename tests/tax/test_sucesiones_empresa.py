"""Who may claim the family-business (and heritage) reductions of the ISD, by kinship.

The kinship scope of each reduction is data (``ReduccionBienes.adquirentes``) read from the law: the state
art. 20.2.c) Ley 29/1987 is for "cónyuges, descendientes o adoptados" and, "cuando no existan descendientes o
adoptados", for "ascendientes, adoptantes y colaterales, hasta el tercer grado"; the communities widen or reword
it. Expected figures are worked by hand in the comments from ``isd_2026.json``.
"""

from decimal import Decimal

from pydantic import ValidationError
import pytest

from hacienda_gpt.agent.tools import ISD_TOOL
from hacienda_gpt.tax.sucesiones import BeneficiarioISD, calcular_isd
from hacienda_gpt.tax.sucesiones_params import (
    AdquirenteBienes,
    ISDParams,
    comprobar_evidencias,
    load_isd_params,
)

D = Decimal
EMPRESA = dict(modo="sucesion", valor_recibido=500000, valor_empresa_familiar=500000, edad=40)


def _isd(**kwargs):
    return calcular_isd(BeneficiarioISD(**{**EMPRESA, **kwargs}))


def _empresa(result) -> D:
    return sum((ln.importe for ln in result.lineas if ln.concepto.startswith("Reducción por empresa")), D(0))


def _avisa(result, *fragmentos: str) -> bool:
    return any(all(f in aviso for f in fragmentos) for aviso in result.avisos)


# --- Before / after: a nephew inheriting a 500.000 € business in the Balearic Islands ----------------------


def test_baleares_nephew_only_when_the_deceased_leaves_no_descendants() -> None:
    sin = _isd(comunidad="baleares", parentesco="sobrino", hay_descendientes_del_causante=False)
    # Grupo III: 500.000 − 8.000 (art. 21) − 95 % × 500.000 = 17.000. Tarifa (art. 33.1): 1.292 + 1.000 × 9,35 %
    # = 1.385,50 × 1,2706 = 1.760,42; bonificación del grupo III 35 % = 616,15 → 1.144,27.
    assert _empresa(sin) == D("-475000.00") and sin.base_liquidable == D("17000.00")
    assert sin.cuota_a_ingresar == D("1144.27")
    assert _avisa(sin, "comprobando solo que el adquirente es de «ascendientes y colaterales, hasta el tercer grado»")
    con = _isd(comunidad="baleares", parentesco="sobrino", hay_descendientes_del_causante=True)
    # Without the reduction: 492.000 → 80.920 + 92.000 × 29,75 % = 108.290 × 1,2706 = 137.593,27; − 35 % → 89.435,63.
    # Before this change the calculator gave the reduction to any heir: 1.144,27 here too.
    assert _empresa(con) == 0 and con.cuota_a_ingresar == D("89435.63")
    assert _avisa(con, "(arts. 25 a 27 DLeg 1/2014 (Illes Balears)) no aplicada", "has indicado que los hay")
    sin_dato = _isd(comunidad="baleares", parentesco="sobrino")
    assert _empresa(sin_dato) == 0 and sin_dato.cuota_a_ingresar == D("89435.63")
    assert _avisa(sin_dato, "indica si los hay (hay_descendientes_del_causante)")


def test_baleares_stranger_never_and_son_always() -> None:
    extrano = _isd(comunidad="baleares", parentesco="extraño")
    # 499.000 → 80.920 + 99.000 × 29,75 % = 110.372,50 × 1,7 = 187.633,25 (before: 3.468,00 with the reduction).
    assert _empresa(extrano) == 0 and extrano.cuota_a_ingresar == D("187633.25")
    assert _avisa(extrano, "es solo para «cónyuge o a los descendientes del causante»", "«extraño»")
    hijo = _isd(comunidad="baleares", parentesco="hijo")
    assert _empresa(hijo) == D("-475000.00") and hijo.cuota_a_ingresar == 0
    # A registered partner is a spouse for the whole Balearic tax: the reduction applies.
    pareja = _isd(comunidad="baleares", parentesco="pareja de hecho")
    assert _empresa(pareja) == D("-475000.00")


# --- State law, art. 20.2.c) and 20.6 ------------------------------------------------------------------------


def test_state_scope_spouse_always_ascendants_and_collaterals_without_descendants() -> None:
    conyuge = _isd(parentesco="cónyuge", hay_descendientes_del_causante=True)  # "En todo caso, el cónyuge"
    # 500.000 − 15.956,87 − 475.000 = 9.043,13 → 7.993,46 × 7,65 % + 1.049,67 × 8,50 % = 700,72.
    assert _empresa(conyuge) == D("-475000.00") and conyuge.cuota_a_ingresar == D("700.72")
    padre = _isd(parentesco="padre", edad=70, hay_descendientes_del_causante=False)
    assert _empresa(padre) == D("-475000.00")
    sobrino = _isd(parentesco="sobrino", hay_descendientes_del_causante=True)
    assert _empresa(sobrino) == 0 and _avisa(sobrino, "(art. 20.2.c) Ley 29/1987) no aplicada")
    pareja = _isd(parentesco="pareja de hecho")  # a stranger for the state law
    assert _empresa(pareja) == 0 and _avisa(pareja, "es solo para", "«pareja de hecho»")
    primo = _isd(parentesco="primo", hay_descendientes_del_causante=False)  # fourth degree: out
    assert _empresa(primo) == 0


def test_state_relatives_by_marriage_are_left_out_and_told_why() -> None:
    cunado = _isd(parentesco="cuñado", hay_descendientes_del_causante=False)
    assert _empresa(cunado) == 0
    assert _avisa(cunado, "sin decir si incluye a los parientes por afinidad", "«colateral por afinidad»")
    hijastro = _isd(parentesco="hijastro")
    assert _empresa(hijastro) == 0
    assert _avisa(hijastro, "«cónyuges, descendientes o adoptados de la persona fallecida», sin decir si incluye")


def test_state_with_only_the_kinship_group() -> None:
    # Group I is only descendants under 21: the reduction applies.
    assert _empresa(_isd(grupo="I", edad=None)) == D("-475000.00")
    # Group II may be a child, a spouse or a parent: the exact relationship is needed.
    solo_grupo = _isd(grupo="II")
    assert _empresa(solo_grupo) == 0 and _avisa(solo_grupo, "falta indicar el parentesco exacto")


def test_state_gift_of_a_business_is_for_spouse_and_descendants_only() -> None:
    sobrino = _isd(modo="donacion", parentesco="sobrino", hay_descendientes_del_causante=False)
    assert _empresa(sobrino) == 0
    assert _avisa(sobrino, "(art. 20.6 Ley 29/1987) no aplicada: es solo para «cónyuge, descendientes o adoptados»")


def test_heritage_assets_follow_the_same_rule() -> None:
    caso = dict(modo="sucesion", valor_recibido=100000, valor_patrimonio_historico=100000, edad=40)
    assert calcular_isd(BeneficiarioISD(**caso, parentesco="hija")).reducciones > D("95000")
    sobrino = calcular_isd(BeneficiarioISD(**caso, parentesco="sobrino", hay_descendientes_del_causante=False))
    assert not any(ln.concepto.startswith("Reducción por bienes del patrimonio") for ln in sobrino.lineas)
    baleares = calcular_isd(BeneficiarioISD(**caso, parentesco="sobrino", comunidad="baleares"))
    assert _avisa(baleares, "(art. 30 DLeg 1/2014 (Illes Balears)) no aplicada")
    assert _avisa(baleares, "(art. 20.2.c) Ley 29/1987) no aplicada")


# --- Communities ----------------------------------------------------------------------------------------------


def test_aragon_99_for_nephews_only_without_descendants() -> None:
    sin = _isd(comunidad="aragon", parentesco="sobrino", hay_descendientes_del_causante=False)
    # Before, the Aragonese rule required spouse or descendants and the state 95 % was applied instead.
    assert _empresa(sin) == D("-495000.00")
    con = _isd(comunidad="aragon", parentesco="sobrino", hay_descendientes_del_causante=True)
    assert _empresa(con) == 0
    assert _avisa(con, "(arts. 131-3 y 131-4 DLeg 1/2005 (Aragón)) no aplicada", "has indicado que los hay")


def test_andalucia_and_extremadura_group_iv_needs_the_tie_with_the_business() -> None:
    sin_vinculo = _isd(comunidad="andalucia", parentesco="extraño")
    assert _empresa(sin_vinculo) == 0
    assert _avisa(sin_vinculo, "«adquirentes que estén comprendidos en el Grupo IV» se les exige además")
    con_vinculo = _isd(comunidad="andalucia", parentesco="extraño", vinculo_laboral_empresa=True)
    # 99 % × 500.000 = 495.000 → base liquidable 5.000.
    assert _empresa(con_vinculo) == D("-495000.00") and con_vinculo.base_liquidable == D("5000.00")
    assert _avisa(con_vinculo, "cumples el vínculo con la empresa que has indicado")
    # Group III by marriage is in: the law names groups, not relationships.
    assert _empresa(_isd(comunidad="extremadura", parentesco="hijastro")) == D("-495000.00")


def test_cantabria_fourth_degree_when_no_acquirers_of_groups_i_and_ii() -> None:
    sin = _isd(comunidad="cantabria", parentesco="primo", hay_adquirentes_grupos_i_ii=False)
    assert _empresa(sin) == D("-495000.00")
    con = _isd(comunidad="cantabria", parentesco="primo", hay_adquirentes_grupos_i_ii=True)
    assert _empresa(con) == 0 and _avisa(con, "otros adquirentes de los grupos I o II, y has indicado que los hay")
    # Gifts: relatives up to the fourth degree; strangers are not encoded.
    assert _empresa(_isd(modo="donacion", comunidad="cantabria", parentesco="primo")) == D("-495000.00")
    assert _empresa(_isd(modo="donacion", comunidad="cantabria", parentesco="extraño")) == 0


def test_castilla_y_leon_names_consanguinity_la_rioja_includes_affinity() -> None:
    cyl = _isd(comunidad="castilla_y_leon", parentesco="cuñado")
    assert _empresa(cyl) == 0
    assert _avisa(cyl, "(art. 17 DLeg 1/2013 (Castilla y León)) no aplicada: es solo para")
    assert _empresa(_isd(comunidad="la_rioja", parentesco="cuñado")) == D("-495000.00")
    assert _empresa(_isd(comunidad="la_rioja", parentesco="suegro")) == 0  # by marriage, but not a collateral


def test_murcia_fourth_degree_collaterals_in_group_iv() -> None:
    assert _empresa(_isd(comunidad="murcia", parentesco="primo")) == D("-495000.00")
    assert _empresa(_isd(comunidad="murcia", parentesco="colateral_lejano")) == 0


def test_canarias_grades_the_percentage_by_kinship() -> None:
    hijo = _isd(comunidad="canarias", parentesco="hijo")
    assert _empresa(hijo) == D("-495000.00")
    sobrino = _isd(comunidad="canarias", parentesco="sobrino", hay_descendientes_del_causante=False)
    # "los ascendientes, adoptantes y colaterales, hasta el tercer grado, podrán aplicar una reducción por un
    # importe del 95 por ciento": the same as the state one, which Canarias applies when more favourable.
    assert _empresa(sobrino) == D("-475000.00")
    assert _avisa(sobrino, "arts. 22 y 22 bis DLeg 1/2009 (Canarias)")


def test_an_allowance_is_not_forfeited_for_a_reduction_that_does_not_apply() -> None:
    caso = dict(modo="sucesion", parentesco="padre", edad=70, valor_recibido=400000, comunidad="cataluna")
    sin_empresa = calcular_isd(BeneficiarioISD(**caso))
    con_descendientes = calcular_isd(
        BeneficiarioISD(**caso, valor_empresa_familiar=100000, hay_descendientes_del_causante=True)
    )
    # The parent gets no business reduction while there are descendants, so the Catalan allowance stays.
    assert con_descendientes.bonificacion == sin_empresa.bonificacion > 0
    assert con_descendientes.cuota_a_ingresar == sin_empresa.cuota_a_ingresar
    sin_descendientes = calcular_isd(
        BeneficiarioISD(**caso, valor_empresa_familiar=100000, hay_descendientes_del_causante=False)
    )
    assert sin_descendientes.bonificacion == 0 and _avisa(sin_descendientes, "es incompatible con optar")


# --- Data and schema ------------------------------------------------------------------------------------------


def test_every_business_and_heritage_rule_says_who_it_is_for() -> None:
    p = load_isd_params(2026)
    for nombre, normativa in p.normativas().items():
        for regla in normativa.reducciones_bienes:
            assert regla.adquirentes, f"{nombre}: {regla.articulo}"
            assert regla.cuando.parentescos == [] and regla.cuando.grupos == [], f"{nombre}: {regla.articulo}"


def test_the_self_check_requires_the_law_s_own_words_for_each_class_of_acquirers() -> None:
    raw = load_isd_params(2026).model_dump(mode="json")
    raw["estatal"]["reducciones_bienes"][0]["adquirentes"][1]["texto"] = "ascendientes y colaterales hasta el cuarto grado"
    raw["comunidades"]["canarias"]["reducciones_bienes"][0]["adquirentes"][1]["porcentaje"] = "0.97"
    problemas = comprobar_evidencias(ISDParams.model_validate(raw))
    assert any("estatal.reducciones_bienes.0" in x and "adquirentes.1.texto" in x for x in problemas)
    assert any("canarias.reducciones_bienes.0" in x and "porcentaje = 0.97" in x for x in problemas)


def test_the_input_and_the_schema_reject_incoherent_data() -> None:
    with pytest.raises(ValidationError, match="parentescos o por grupos"):
        AdquirenteBienes(texto="x", parentescos=["hijo"], grupos=["I"])
    with pytest.raises(ValidationError, match="parentescos o por grupos"):
        AdquirenteBienes(texto="x")
    with pytest.raises(ValidationError, match="el causante o donante sí tiene descendientes"):
        BeneficiarioISD(**EMPRESA, parentesco="nieto", hay_descendientes_del_causante=False)


def test_the_agent_tool_offers_the_new_inputs() -> None:
    propiedades = set(ISD_TOOL.input_schema["properties"])
    assert propiedades <= set(BeneficiarioISD.model_fields)
    assert {
        "hay_descendientes_del_causante",
        "hay_adquirentes_grupos_i_ii",
        "hay_familiares_adquirentes_hasta_cuarto_grado",
        "vinculo_laboral_empresa",
    } <= propiedades

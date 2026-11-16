"""Legal basis of the mortgage → pre-2013 housing deduction bridge, and the IRPF parameters it leans on.

Fixtures (``tests/fixtures/modulos/hipoteca/deduccion_vivienda``) are extracts of two pages of boe.es read on
2026-11-16: the Ley 35/2006 in force (disposición transitoria 18.ª) and its wording of 31-10-2012
(``act.php?id=BOE-A-2006-20764&tn=1&p=20121031``: arts. 68.1, 70.1 and 78), the last one before the Ley 16/2012
removed art. 68.1 «con efectos desde el 1 de enero de 2013», i.e. the «redacción en vigor a 31 de diciembre de
2012» that the transitional provision applies. With ``DEDUCCION_VIVIENDA_BOE_DIR`` pointing to a folder with the
full pages (``BOE-A-2006-20764.html`` and ``BOE-A-2006-20764_p20121031.html``) the checks run on them.
"""

from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path

from hipoteca_util import FIXTURES
import pytest

from asistente.ambitos.fiscal.calculo.params import DATA_DIR, load_params
from asistente.ambitos.hipoteca.deduccion import (
    DEDUCCION_PATH,
    FundamentoPuente,
    cargar_fundamento,
    comprobar,
    textos_de_directorio,
)
from asistente.conocimiento.verificacion import comprobaciones, verificar

EXTRACTOS = FIXTURES / "deduccion_vivienda"
HISTORICA = "20121031"


def _textos() -> dict[str, str]:
    return textos_de_directorio(EXTRACTOS)


def _alterado(cambio) -> FundamentoPuente:
    raw = json.loads(DEDUCCION_PATH.read_text(encoding="utf-8"))
    cambio(raw)
    return FundamentoPuente.model_validate(raw)


def test_the_bridges_legal_basis_matches_the_law() -> None:
    textos = _textos()
    assert set(textos) == {"BOE-A-2006-20764", f"BOE-A-2006-20764@{HISTORICA}"}
    assert comprobar(cargar_fundamento(), textos) == []


@pytest.mark.skipif(not os.environ.get("DEDUCCION_VIVIENDA_BOE_DIR"), reason="sin las páginas completas del BOE")
def test_the_bridges_legal_basis_matches_the_full_pages() -> None:
    textos = textos_de_directorio(Path(os.environ["DEDUCCION_VIVIENDA_BOE_DIR"]))
    assert comprobar(cargar_fundamento(), textos) == []


def test_the_2012_wording_is_not_todays() -> None:
    # Read against today's text (which has art. 68.1 on new companies), every 2012 quote is missing.
    textos = _textos()
    hoy = {"BOE-A-2006-20764": textos["BOE-A-2006-20764"], f"BOE-A-2006-20764@{HISTORICA}": "texto vigente"}
    problemas = comprobar(cargar_fundamento(), hoy)
    historicas = [n for n, c in cargar_fundamento().citas.items() if c.version == HISTORICA]
    assert len(historicas) == 5 and all(any(p.startswith(n + ":") for p in problemas) for n in historicas)


def test_the_check_catches_a_wrong_figure_and_a_concept_the_law_does_not_name() -> None:
    def cambio(raw):
        raw["fecha_limite_adquisicion"] = "2012-01-01"
        raw["conceptos"][0]["termino"] = "las primas de seguros"

    problemas = comprobar(_alterado(cambio), _textos())
    assert "dt18_adquisicion.fecha_limite_adquisicion: el dato es 2012-01-01 y la cita dice «1 de enero de 2013»" in (
        problemas
    )
    assert "concepto capital: «las primas de seguros» no está en la cita art68_base" in problemas


def test_the_check_catches_an_invented_quote() -> None:
    def cambio(raw):
        raw["citas"]["art68_base"]["cita"] = raw["citas"]["art68_base"]["cita"].replace("9.040", "9.400")

    problemas = comprobar(_alterado(cambio), _textos())
    assert any(p.startswith("art68_base: la cita") for p in problemas)


def test_what_the_bridge_adds_and_leaves_out() -> None:
    fundamento = cargar_fundamento()
    assert [c.clave for c in fundamento.incluidos()] == [
        "capital",
        "intereses",
        "amortizaciones_anticipadas",
        "comisiones_amortizacion_anticipada",
    ]
    # Known to the law, unknown to the module: never added silently.
    assert {c.clave for c in fundamento.no_incluidos()} == {
        "gastos_adquisicion",
        "coste_cobertura",
        "ingresos_cobertura",
        "otros_gastos_financiacion",
    }
    assert any("anterior a 2013" in n for n in fundamento.no_modelado)


# --------------------------------------------------------------------------- #
# The IRPF parameters of the transitional deduction (tax/data/irpf_*.json)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ejercicio", [2025, 2026])
def test_rate_and_cap_are_verified_against_the_2012_wording(ejercicio: int) -> None:
    v = load_params(ejercicio).vivienda_habitual_transitoria
    assert v.fuente.verificado and v.fuente.version_historica == HISTORICA
    evidencia = json.loads((DATA_DIR / "evidencia_parametros.json").read_text(encoding="utf-8"))
    entrada = evidencia[f"irpf_{ejercicio}.json"]["vivienda_habitual_transitoria"]
    assert (entrada["boe_id"], entrada["version"]) == ("BOE-A-2006-20764", HISTORICA)
    citas = entrada["citas"]
    # The cap is printed; the rate is the State's 7,5 % (art. 68.1.1.º) plus the regional default 7,5 % (art. 78.2.a).
    assert v.base_maxima == Decimal("9040") and "9.040 euros anuales" in citas[1]
    assert "el 7,5 por ciento de las cantidades satisfechas" in citas[0]
    assert "Con carácter general el 7,5 por ciento" in citas[2]
    assert v.tipo == Decimal("0.075") + Decimal("0.075")

    (check,) = [c for c in comprobaciones(load_params(ejercicio)) if c.ruta == ["vivienda_habitual_transitoria"]]
    assert (check.tipo, check.version) == ("cita", HISTORICA)
    textos = _textos()
    paginas = {("BOE-A-2006-20764", HISTORICA): textos[f"BOE-A-2006-20764@{HISTORICA}"]}
    (resultado,) = verificar([check], lambda boe_id, version=None: paginas.get((boe_id, version)))
    assert resultado.ok, resultado.faltan
    # Against today's text the same quotes are gone: the version matters.
    (hoy,) = verificar([check.model_copy(update={"version": None})], lambda b, v=None: textos["BOE-A-2006-20764"])
    assert not hoy.ok

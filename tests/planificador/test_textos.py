"""The planner's legal quotes are verbatim in the BOE texts they cite (excerpts kept in fixtures/)."""

from __future__ import annotations

from pathlib import Path

from hacienda_gpt.modulos.ayudas.parametros import load_prestaciones
from hacienda_gpt.planificador.ayudas import _cita_datos
from hacienda_gpt.planificador.textos import TEXTOS

FIXTURES = Path(__file__).parent / "fixtures"


def _compacto(texto: str) -> str:
    return " ".join(texto.split())


def test_every_quote_is_verbatim_in_its_law() -> None:
    leyes = {p.stem: _compacto(p.read_text(encoding="utf-8")) for p in FIXTURES.glob("BOE-A-*.txt")}
    for referencia, texto in TEXTOS.items():
        if referencia in {"art. 17.2.a).3.ª LIRPF", "art. 12 LIRPF"}:
            continue  # the optimiser's own quotes, reused as they are
        assert _compacto(texto.texto) in leyes[texto.boe_id], referencia


def test_the_aid_rules_are_quoted_from_the_aid_module_data() -> None:
    p = load_prestaciones(2026)
    assert "base imponible general y del ahorro" in _cita_datos(p.bono_social_electrico, "art. 4.1 Orden ETU/943/2017")
    assert "de cualquier naturaleza" in _cita_datos(p.nacimiento, "art. 358.2 LGSS")
    assert "artículo 20" in _cita_datos(p.capi, "art. 11.6 Ley 19/2021")

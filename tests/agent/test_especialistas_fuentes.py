"""A tool's sources line is written once, whether the tool wrote it itself or the core adds it."""

from __future__ import annotations

from asistente.nucleo.agent.especialistas import _render
from asistente.nucleo.modulos.contrato import ResultadoHerramienta


def test_la_linea_de_fuentes_no_se_repite() -> None:
    propia = ResultadoHerramienta(
        contenido="Ahorro: 41,58 €/año.\nFuentes: BOE; CNMC", resumen="r", citas=("BOE", "CNMC")
    )
    assert _render(propia).count("Fuentes: ") == 1
    sin_linea = ResultadoHerramienta(contenido="Ahorro: 41,58 €/año.", resumen="r", citas=("BOE",))
    assert _render(sin_linea) == "Ahorro: 41,58 €/año.\nFuentes: BOE"

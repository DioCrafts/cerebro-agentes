"""Module sources join the BOE watch like the calculator's laws."""

from __future__ import annotations

from datetime import date
import json

from click.testing import CliRunner

from asistente.conocimiento.fuentes import normas_de_parametros
from asistente.nucleo.modulos import fuentes
from asistente.plataforma import settings
from asistente.plataforma.cli import proactive as cli_module
from .fakes import Llamadas, agua, instalar, seguros

LEY_AGUAS = "BOE-A-2001-14276"


def test_boe_sources_are_recognised_and_others_listed() -> None:
    reg = instalar(agua(Llamadas()), seguros(Llamadas()))
    vigiladas = fuentes.fuentes_de_modulos(reg)
    assert [(f.modulo, f.boe_id) for f in vigiladas] == [("agua", LEY_AGUAS), ("seguros", None)]
    assert fuentes.boe_ids_de_modulos(reg) == {LEY_AGUAS}
    # boe-novedades watches them too.
    assert LEY_AGUAS in cli_module._vigiladas()


def test_first_reading_is_the_baseline_and_a_later_update_is_listed() -> None:
    reg = instalar(agua(Llamadas()))
    vigiladas = fuentes.fuentes_de_modulos(reg)
    pendientes, estado = fuentes.revisar({LEY_AGUAS: date(2026, 1, 1)}, {"fuentes": {}}, fuentes=vigiladas)
    assert pendientes == [] and estado["fuentes"][LEY_AGUAS] == {"linea_base": "2026-01-01"}
    pendientes, estado = fuentes.revisar({LEY_AGUAS: date(2026, 6, 1)}, estado, fuentes=vigiladas)
    assert pendientes == [
        {
            "modulo": "agua",
            "boe_id": LEY_AGUAS,
            "norma": "Ley de Aguas (texto refundido)",
            "leida_el": "2026-01-01",
            "actualizada_en_boe_el": "2026-06-01",
        }
    ]
    pendientes, estado = fuentes.revisar(
        {LEY_AGUAS: date(2026, 6, 1)}, estado, fuentes=vigiladas, marcar_revisadas=True
    )
    assert pendientes == [] and estado["fuentes"][LEY_AGUAS] == {"linea_base": "2026-06-01"}


def test_params_check_reports_the_module_laws(tmp_path, monkeypatch) -> None:
    import asistente.plataforma.cli.boe_watch as boe_watch

    instalar(agua(Llamadas()), seguros(Llamadas()))
    monkeypatch.setattr(settings, "MODULOS_FUENTES_STATE_PATH", str(tmp_path / "estado.json"))
    monkeypatch.setattr(cli_module.time, "sleep", lambda _s: None)
    fechas = {"actual": "20260101T000000Z"}

    def fake_metadata(boe_id: str, timeout: int = 30) -> dict:
        return {"fecha_actualizacion": fechas["actual"] if boe_id == LEY_AGUAS else "20200101T000000Z"}

    monkeypatch.setattr(boe_watch, "_fetch_metadata", fake_metadata)
    first = json.loads(CliRunner().invoke(cli_module.cli, ["params-check"]).output)
    assert first["comprobadas"] == len(normas_de_parametros())  # the tax laws, as before
    assert first["modulos"]["comprobadas"] == 1 and first["modulos"]["a_revisar"] == []
    assert first["modulos"]["fuera_del_boe"] == ["seguros: dgsfp-registro (https://example.invalid/dgsfp)"]

    fechas["actual"] = "20260301T000000Z"
    result = CliRunner().invoke(cli_module.cli, ["params-check", "--fail-on-stale"])
    assert result.exit_code == 1
    (pendiente,) = json.loads(result.output)["modulos"]["a_revisar"]
    assert pendiente["boe_id"] == LEY_AGUAS and pendiente["actualizada_en_boe_el"] == "2026-03-01"

    revisado = CliRunner().invoke(cli_module.cli, ["params-check", "--fail-on-stale", "--marcar-modulos-revisados"])
    assert revisado.exit_code == 0


def test_modulos_command_lists_the_registry() -> None:
    instalar(agua(Llamadas()))
    salida = json.loads(CliRunner().invoke(cli_module.cli, ["modulos"]).output)
    assert [m["clave"] for m in salida["modulos"]] == ["fiscal", "agua"]
    assert salida["modulos"][1]["herramientas"]["agua_cambiar_tarifa"] == "actua"


def test_a_domain_that_watches_its_own_laws_is_left_out_by_declaration_not_by_key() -> None:
    from asistente.nucleo.modulos.ganchos import Ganchos

    # A domain (not the tax module) declaring its laws watched by its own maintenance is left out…
    propio = agua(Llamadas())
    propio.ganchos = Ganchos(fuentes_vigiladas_aparte=True)
    reg = instalar(propio, seguros(Llamadas()))
    assert [f.modulo for f in fuentes.fuentes_de_modulos(reg)] == ["seguros"]
    # …and the tax module is left out because it declares so (its laws are params-check's), not by its key.
    from asistente.ambitos.fiscal import MODULO as FISCAL

    assert FISCAL.ganchos.fuentes_vigiladas_aparte is True
    assert all(f.modulo != "fiscal" for f in fuentes.fuentes_de_modulos(instalar(agua(Llamadas()))))

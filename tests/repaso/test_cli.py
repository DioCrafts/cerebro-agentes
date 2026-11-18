"""The command line: the job (a no-op without REPASO_ACTIVO), a run now with a dry run that writes nothing, the
passes of a case."""

from __future__ import annotations

from datetime import date
import json

from click.testing import CliRunner
import pytest

from asistente.nucleo.repaso import servicio
from asistente.plataforma import settings
from asistente.plataforma.cli.repaso import cli
from .conftest import Guionizado, aviso, valida


@pytest.fixture
def abierto(store):
    aviso(store, "modulo:luz:factura", title="Factura de la luz", due_date=date(2026, 11, 30))
    return store


def _json(salida: str):
    return json.loads(salida.strip().splitlines()[-1])


def test_the_job_does_nothing_while_repaso_is_off(db) -> None:
    resultado = CliRunner().invoke(cli, ["trabajo", "--db-path", db])
    assert resultado.exit_code == 0, resultado.output
    assert _json(resultado.output) == {"activo": False}


def test_the_job_runs_when_due(db, abierto, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REPASO_ACTIVO", True)
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: None)
    temprano = CliRunner().invoke(cli, ["trabajo", "--db-path", db, "--ahora", "2026-11-16T06:00"])
    assert temprano.exit_code == 0 and _json(temprano.output)["toca"] is False
    resultado = CliRunner().invoke(cli, ["trabajo", "--db-path", db, "--ahora", "2026-11-16T07:30"])
    assert resultado.exit_code == 0, resultado.output
    resumen = _json(resultado.output)
    assert resumen["toca"] and resumen["escritos"] == 1 and resumen["periodo"] == "2026-W47"


def test_a_dry_run_prints_the_points_and_the_digest_and_writes_nothing(
    db, abierto, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: pytest.fail("a dry run calls no model by default"))
    resultado = CliRunner().invoke(cli, ["ahora", "--db-path", db, "--case", "c1", "--hoy", "2026-11-18", "--simular"])
    assert resultado.exit_code == 0, resultado.output
    assert "1. [" in resultado.output and "Factura de la luz" in resultado.output
    assert "Título: Resumen semanal: 1 asunto que importa" in resultado.output
    assert _json(resultado.output)["casos"][0]["estado"] == "simulado"
    assert [n for n in abierto.list("c1") if n.kind == "resumen_semanal"] == []

    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: Guionizado(valida))
    con_modelo = CliRunner().invoke(
        cli, ["ahora", "--db-path", db, "--todos", "--hoy", "2026-11-18", "--simular", "--con-modelo"]
    )
    assert con_modelo.exit_code == 0, con_modelo.output
    assert "Texto (modelo):" in con_modelo.output


def test_a_real_run_writes_and_the_state_command_shows_it(db, abierto, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: None)
    resultado = CliRunner().invoke(
        cli, ["ahora", "--db-path", db, "--case", "c1", "--case", "nadie", "--hoy", "2026-11-18"]
    )
    assert resultado.exit_code == 1  # a case that does not exist is reported
    salida = _json(resultado.output)
    assert salida["no_encontrados"] == ["nadie"] and salida["casos"][0]["estado"] == "escrito"
    estado = CliRunner().invoke(cli, ["estado", "--db-path", db, "--case", "c1"])
    assert estado.exit_code == 0 and _json(estado.output)[0]["estado"] == "aplicada"
    assert CliRunner().invoke(cli, ["ahora", "--db-path", db]).exit_code == 2  # neither --case nor --todos

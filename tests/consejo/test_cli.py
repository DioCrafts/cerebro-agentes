"""``python -m asistente.plataforma.cli.consejo``: convene (estimate), confirm, run the job, read the state; and the
worker's job is declared."""

from __future__ import annotations

import json
from typing import Any

from click.testing import CliRunner
import pytest

from asistente.nucleo.consejo import servicio as servicio_mod
from asistente.plataforma import settings
from asistente.plataforma.cli.consejo import cli
from .fakes import FabricaFalsa


@pytest.fixture
def fabrica(monkeypatch: pytest.MonkeyPatch) -> FabricaFalsa:
    fabrica = FabricaFalsa()
    monkeypatch.setattr(servicio_mod, "FabricaPorDefecto", lambda *a: fabrica)
    return fabrica


def _json(salida: str) -> Any:
    return json.loads(salida.strip().splitlines()[-1])


def test_convene_confirm_run_and_state(db: str, fabrica: FabricaFalsa) -> None:
    runner = CliRunner()
    convocada = runner.invoke(
        cli, ["convocar", "--db-path", db, "--case", "c1", "--pregunta", "¿Amortizo la hipoteca o invierto?"]
    )
    assert convocada.exit_code == 0, convocada.output
    datos = _json(convocada.output)
    assert datos["estado"] == "pendiente_confirmacion" and float(datos["estimacion_usd"]) > 0
    assert fabrica.propositos == []  # nothing ran

    confirmada = runner.invoke(
        cli,
        ["convocar", "--db-path", db, "--case", "c1", "--pregunta", "¿Amortizo la hipoteca o invierto?", "--confirmar"],
    )
    assert _json(confirmada.output)["estado"] == "en_cola"

    ejecutada = runner.invoke(cli, ["ejecutar", "--db-path", db])
    assert ejecutada.exit_code == 0, ejecutada.output
    assert _json(ejecutada.output)["terminadas"] == 1

    estado = runner.invoke(cli, ["estado", "--db-path", db, "--case", "c1"])
    sesiones = _json(estado.output)
    assert sesiones[0]["estado"] == "terminada" and sesiones[0]["recomendacion"]
    una = runner.invoke(cli, ["estado", "--db-path", db, "--case", "c1", "--sesion", sesiones[0]["id"]])
    assert _json(una.output)[0]["id"] == sesiones[0]["id"]
    assert runner.invoke(cli, ["estado", "--db-path", db, "--case", "c2", "--sesion", sesiones[0]["id"]]).exit_code != 0


def test_errors_and_switched_off(db: str, fabrica: FabricaFalsa, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    assert runner.invoke(
        cli, ["convocar", "--db-path", db, "--case", "nope", "--pregunta", "¿Vendo o alquilo?"]
    ).exit_code
    assert runner.invoke(cli, ["convocar", "--db-path", db, "--case", "c1", "--pregunta", "¿Y?"]).exit_code
    monkeypatch.setattr(settings, "CONSEJO_ACTIVO", False)
    apagado = runner.invoke(cli, ["ejecutar", "--db-path", db])
    assert apagado.exit_code == 0 and _json(apagado.output) == {"activo": False}


def test_the_worker_job_is_declared() -> None:
    from asistente.plataforma.trabajos import TRABAJOS

    trabajo = next(t for t in TRABAJOS if t.nombre == "consejo")
    assert trabajo.orden == ("asistente.plataforma.cli.consejo", "ejecutar")
    assert trabajo.programacion.cada_minutos == 5 and trabajo.por_defecto

"""The command line: model-free CI part, the operator's review flow, prompt registration."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from click.testing import CliRunner
import pytest

from hacienda_gpt.cli.evaluacion import cli
from hacienda_gpt.decision.schemas import CaseState, Fact, FactValueType
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.evaluacion import suite as s
from hacienda_gpt.evaluacion import variantes as v
from hacienda_gpt.evaluacion.captura import CONSENTIMIENTO_VERSION, FeedbackEntrada, FeedbackStore, capturar

PRESTAMO = {
    "capital_inicial": "100000",
    "fecha_firma": "2020-01-15",
    "plazo_meses": 300,
    "tipo": "fijo",
    "tipo_inicial": "3",
}


@pytest.fixture
def suite_copia(tmp_path: Path) -> Path:
    destino = tmp_path / "casos"
    destino.mkdir()
    shutil.copy(s.CASOS_DIR / "suite.json", destino / "suite.json")
    for nombre in ("modulo/hipoteca-proxima-cuota.json", "confirmacion_actua/actua-baja-queda-pendiente.json"):
        (destino / nombre).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(s.CASOS_DIR / nombre, destino / nombre)
    return destino


def test_validar_and_replay_are_the_model_free_ci_part(suite_copia: Path) -> None:
    runner = CliRunner()
    resultado = runner.invoke(cli, ["validar", "--casos", str(suite_copia)])
    assert resultado.exit_code in (0, 1)  # 1 only while an integrated prompt waits for registration
    assert "2 casos" in resultado.output
    resultado = runner.invoke(cli, ["run", "--reproduccion", "--casos", str(suite_copia)])
    assert resultado.exit_code == 0, resultado.output
    assert "Aprobados: 2/2" in resultado.output

    # A failing replay makes the command fail (the CI job goes red).
    ruta = suite_copia / "modulo" / "hipoteca-proxima-cuota.json"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["guion"]["asesor"][-1]["texto"] = "Tu cuota es de 1,00 €."
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    resultado = runner.invoke(cli, ["run", "--reproduccion", "--casos", str(suite_copia)])
    assert resultado.exit_code == 1 and "falta la cifra «cuota»" in resultado.output


def _db_con_feedback(tmp_path: Path, **correccion) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    db = str(tmp_path / "state.sqlite3")
    cases = SQLiteCaseStateStore(db)
    case = CaseState(
        case_id="case_1",
        user_id="u1",
        jurisdiction="ES",
        tax_period="2026",
        facts=[
            Fact(
                fact_id="fact_hipoteca_prestamo",
                name="hipoteca_prestamo",
                value=json.dumps(PRESTAMO),
                value_type=FactValueType.STRING,
                source="usuario",
                confidence=1.0,
            )
        ],
    )
    cases.save_case(case)
    pregunta = "¿Cuál es la cuota de mi hipoteca? Soy ana@example.com"
    cases.append_audit_event(
        "case_1",
        {
            "event_type": "agent_answer",
            "input": pregunta,
            "mode": "verified",
            "tool_trace": [
                {"tool": "consultar_hipoteca", "agent": "asesor", "arguments": {"pregunta": "cuota"}, "summary": "ok"},
                {"tool": "hipoteca_cuadro", "agent": "especialista_hipoteca", "arguments": {"fecha": "2026-03-20"}},
            ],
            "claims": [],
        },
    )
    registro = capturar(
        FeedbackEntrada(
            case_id="case_1",
            pregunta=pregunta,
            respuesta="Tu cuota es de 500,00 €.",
            valoracion="negativa",
            tipo="cifra_erronea",
            guardar_para_evaluacion=True,
            version_consentimiento=CONSENTIMIENTO_VERSION,
            **correccion,
        ),
        FeedbackStore(db),
        case=cases.get_case("case_1"),
        cases=cases,
    )
    return db, registro.id


def test_the_operator_reviews_and_promotes_a_case(tmp_path: Path, suite_copia: Path) -> None:
    db, feedback_id = _db_con_feedback(tmp_path, cifra_correcta="474,21 €")
    runner = CliRunner()
    salida = runner.invoke(cli, ["casos", "pendientes", "--db", db]).output
    assert feedback_id in salida and "cifra_erronea" in salida
    assert "474,21" in runner.invoke(cli, ["casos", "ver", feedback_id, "--db", db]).output

    borrador = tmp_path / "borrador.json"
    resultado = runner.invoke(cli, ["casos", "borrador", feedback_id, "--db", db, "--out", str(borrador)])
    assert resultado.exit_code == 0, resultado.output
    caso = json.loads(borrador.read_text(encoding="utf-8"))
    assert "[correo]" in caso["pregunta"] and any(e["tipo"] == "cifra" for e in caso["expectativas"])

    base = ["casos", "promover", feedback_id, "--db", db, "--casos", str(suite_copia), "--desde", str(borrador)]
    sin_confirmar = runner.invoke(cli, [*base, "--revisor", "Operadora"])
    assert sin_confirmar.exit_code != 0 and "--confirmo-anonimizado" in sin_confirmar.output

    # Anything that still looks personal is refused.
    caso_con_dni = {**caso, "pregunta": caso["pregunta"] + " DNI 12345678Z"}
    sucio = tmp_path / "sucio.json"
    sucio.write_text(json.dumps(caso_con_dni), encoding="utf-8")
    rechazo = runner.invoke(
        cli,
        [
            "casos",
            "promover",
            feedback_id,
            "--db",
            db,
            "--casos",
            str(suite_copia),
            "--desde",
            str(sucio),
            "--revisor",
            "Operadora",
            "--confirmo-anonimizado",
        ],
    )
    assert rechazo.exit_code != 0 and "datos personales" in rechazo.output

    ok = runner.invoke(cli, [*base, "--revisor", "Operadora", "--confirmo-anonimizado"])
    assert ok.exit_code == 0, ok.output
    (fichero,) = (suite_copia / "regresion").glob("*.json")
    promovido = json.loads(fichero.read_text(encoding="utf-8"))
    assert promovido["origen"]["revisado_por"] == "Operadora" and promovido["origen"]["referencia"].startswith("fb-")
    assert "case_1" not in fichero.read_text(encoding="utf-8") and feedback_id not in fichero.read_text(
        encoding="utf-8"
    )
    assert FeedbackStore(db).get(feedback_id).estado == "promovido"  # type: ignore[union-attr]
    revision = SQLiteCaseStateStore(db).list_audit_events("case_1")[-1]
    assert revision["event_type"] == "evaluacion_revision" and revision["estado"] == "promovido"
    # The promoted case is part of the suite now and replays its expectations from the calculator.
    assert s.validar(suite_copia).ok
    otra_vez = runner.invoke(cli, [*base, "--revisor", "Operadora", "--confirmo-anonimizado"])
    assert otra_vez.exit_code != 0 and "no pendiente" in otra_vez.output


def test_discard_and_signals(tmp_path: Path) -> None:
    db, feedback_id = _db_con_feedback(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["casos", "descartar", feedback_id, "--db", db]).exit_code == 0
    assert "No hay casos pendientes" in runner.invoke(cli, ["casos", "pendientes", "--db", db]).output
    estadisticas = json.loads(runner.invoke(cli, ["casos", "estadisticas", "--db", db]).output)
    assert estadisticas["por_estado"] == {"descartado": 1}
    senales = json.loads(runner.invoke(cli, ["senales", "--db", db]).output)
    assert senales["total"] == 0


def test_registering_a_prompt_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registro = tmp_path / "registro.json"
    shutil.copy(v.REGISTRO_PROMPTS, registro)
    runner = CliRunner()
    ambos = runner.invoke(cli, ["prompts", "registrar", "--rol", "agente"])
    assert ambos.exit_code != 0 and "uno de los dos" in ambos.output

    # The current text of the module addendum is already registered: nothing to do.
    with pytest.raises(ValueError, match="no ha cambiado"):
        v.registrar_version("modulos", nota="x", ruta=registro)
    monkeypatch.setattr(v._prompts, "MODULES_ADDENDUM", v.texto_integrado("modulos") + "\nNuevo.")
    nueva = v.registrar_version("modulos", nota="Probado sin modelo", ruta=registro)
    assert nueva.version == "modulos@2" and v.cargar_registro(registro)["modulos"][-1].nota == "Probado sin modelo"
    # The repository's registry was not touched.
    assert v.cargar_registro()["modulos"][-1].version == "modulos@1"

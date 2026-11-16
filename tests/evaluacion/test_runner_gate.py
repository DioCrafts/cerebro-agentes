"""Runs against a (scripted) model: metering, the judge's quote gate, and the promotion gate."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path

import pytest

from hacienda_gpt.evaluacion import suite as s
from hacienda_gpt.evaluacion.gate import decidir, promover
from hacienda_gpt.evaluacion.modelos import CasoEvaluacion, PasoGuion, Rubrica
from hacienda_gpt.evaluacion.reproduccion import ProveedorReproduccion
from hacienda_gpt.evaluacion.runner import Entorno, comparar, evaluar, formatear
from hacienda_gpt.evaluacion.variantes import VARIANTE_BASE, Variante, variante_activa
from hacienda_gpt.ops.consumo import ConsumoStore
from tests.agent.fakes import ScriptedProvider

IDS = [
    "irpf-resultado-madrid",
    "actua-baja-queda-pendiente",
    "hipoteca-proxima-cuota",
    "caducidades-dni",
    "luz-factura-oferta",
]
USO = {"input_tokens": 1000, "output_tokens": 100}


@pytest.fixture(scope="module")
def casos() -> list[CasoEvaluacion]:
    return [c for c in s.cargar_casos() if c.id in IDS]


def _rota(caso: CasoEvaluacion) -> CasoEvaluacion:
    guion = dict(caso.guion or {})
    guion["asesor"] = [*guion["asesor"][:-1], PasoGuion(texto="No lo sé.")]
    return caso.model_copy(update={"guion": guion})


def _fabrica(malos: dict[str, set[str]]):  # type: ignore[no-untyped-def]
    """A 'model' that answers each case well, except the cases listed for a variant."""

    def crear(variante: Variante, caso: CasoEvaluacion, cifras: dict[str, str]) -> ProveedorReproduccion:
        if caso.id in malos.get(variante.id, set()):
            caso = _rota(caso)
        return ProveedorReproduccion(caso, cifras, modelo="claude-opus-5-5", uso=USO)

    return crear


def _entorno(tmp_path: Path, malos: dict[str, set[str]], **extra) -> Entorno:  # type: ignore[no-untyped-def]
    return Entorno(
        proveedor=_fabrica(malos), consumo=ConsumoStore(str(tmp_path / "consumo.sqlite3")), modo="modelo", **extra
    )


def _run(casos, variante, entorno):  # type: ignore[no-untyped-def]
    return evaluar(casos, variante, entorno, config=s.cargar_config(), suite_huella="h")


def test_a_model_run_is_metered_and_reported(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    informe = _run(casos, VARIANTE_BASE, _entorno(tmp_path, {"base": {"caducidades-dni"}}))
    assert informe.modo == "modelo" and (informe.proveedor, informe.modelo) == ("reproduccion", "claude-opus-5-5")
    assert informe.resumen.total == 5 and informe.resumen.aprobados == 4
    assert informe.resumen.coste_usd > 0
    # The scripted verifier reports no usage: one call per case is counted without cost (a lower bound, said so).
    assert informe.resumen.llamadas_sin_coste == 5 and "cota inferior" in formatear(informe)
    assert all(r.ejecucion.coste_usd and r.ejecucion.llamadas_modelo >= 2 for r in informe.casos)
    assert informe.resumen.por_categoria["modulo"].tasa == pytest.approx(2 / 3, abs=1e-3)
    assert informe.huellas_prompts["agente"] and informe.prompts["agente"] == VARIANTE_BASE.prompts["agente"]
    # Metered with the production purposes (routing applies as in the deployed assistant).
    from hacienda_gpt.ops.consumo import hoy_madrid

    entorno = _entorno(tmp_path, {})
    _run(casos[:1], VARIANTE_BASE, entorno)
    propositos = {a.clave for a in entorno.consumo.resumen(desde=hoy_madrid(), hasta=hoy_madrid()).por_proposito}
    assert {"agente", "verificador"} <= propositos
    texto = formatear(informe)
    assert "caducidades-dni" in texto and "Coste:" in texto and "(crítica)" in texto


def test_comparison_lists_regressions_and_improvements(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    malos = {"base": {"caducidades-dni"}, "otra": {"luz-factura-oferta"}}
    base = _run(casos, VARIANTE_BASE, _entorno(tmp_path, malos))
    otra = _run(casos, Variante(id="otra"), _entorno(tmp_path, malos))
    comparacion = comparar(otra, base)
    assert comparacion.regresiones == ["luz-factura-oferta"] and comparacion.mejoras == ["caducidades-dni"]
    assert comparacion.delta_tasa == 0.0


def test_a_budget_stops_the_run_and_marks_it_incomplete(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    informe = _run(casos, VARIANTE_BASE, _entorno(tmp_path, {}, presupuesto_usd=Decimal("0.000001")))
    assert informe.incompleto
    assert sum(1 for r in informe.casos if r.ejecucion.error == "presupuesto agotado") == 4


def test_judge_counts_only_with_a_literal_quote(tmp_path: Path) -> None:
    base = next(c for c in s.cargar_casos() if c.id == "fuera-voto")
    caso = base.model_copy(update={"rubrica": Rubrica(criterios=["Dice que la pregunta queda fuera de su ámbito."])})

    def juez(cita: str):  # type: ignore[no-untyped-def]
        respuesta = {"veredictos": [{"criterio": 1, "cumple": True, "cita": cita, "motivo": "lo dice"}]}
        return lambda: ScriptedProvider([], [respuesta])

    aprobado = _run([caso], VARIANTE_BASE, _entorno(tmp_path, {}, juez=juez("me ocupo de tus impuestos")))
    assert aprobado.casos[0].aprobado and aprobado.resumen.juez_sin_cita == 0
    from hacienda_gpt.ops.consumo import hoy_madrid

    consumo = ConsumoStore(str(tmp_path / "consumo.sqlite3"))
    propositos = {a.clave for a in consumo.resumen(desde=hoy_madrid(), hasta=hoy_madrid()).por_proposito}
    assert "evaluacion-juez" in propositos
    inventada = _run([caso], VARIANTE_BASE, _entorno(tmp_path, {}, juez=juez("esta pregunta no es de mi ámbito")))
    (resultado,) = inventada.casos
    assert not resultado.aprobado and inventada.resumen.juez_sin_cita == 1
    assert any("sin citar literalmente" in f for f in resultado.fallos)
    sin_juez = _run([caso], VARIANTE_BASE, _entorno(tmp_path, {}))
    assert sin_juez.casos[0].aprobado and "Rúbrica no evaluada" in sin_juez.casos[0].avisos[0]


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #


def _promover(casos, tmp_path, malos, candidata_id="candidata", base=None):  # type: ignore[no-untyped-def]
    return promover(
        Variante(id=candidata_id, modelo="otro-modelo"),
        _entorno(tmp_path, malos),
        casos,
        config=s.cargar_config(),
        suite_huella="h",
        base=base,
    )


def test_a_better_candidate_is_promoted(
    casos: list[CasoEvaluacion], tmp_path: Path, variante_activa_aislada: Path
) -> None:
    decision = _promover(casos, tmp_path, {"base": {"caducidades-dni", "luz-factura-oferta"}})
    assert decision.aplicada, decision.motivos
    assert decision.ruta_activa == variante_activa_aislada and variante_activa().id == "candidata"
    activa = json.loads(variante_activa_aislada.read_text(encoding="utf-8"))
    assert activa["frente_a"] == "base" and activa["resumen"]["tasa_candidata"] == 1.0
    assert decision.candidata.comparacion is not None
    assert decision.candidata.comparacion.mejoras == ["caducidades-dni", "luz-factura-oferta"]


def test_an_equal_candidate_is_refused(
    casos: list[CasoEvaluacion], tmp_path: Path, variante_activa_aislada: Path
) -> None:
    decision = _promover(casos, tmp_path, {})
    assert not decision.aplicada and any("estrictamente mejor" in m for m in decision.motivos)
    assert not variante_activa_aislada.exists()


def test_better_overall_but_worse_on_a_critical_category_is_refused(
    casos: list[CasoEvaluacion], tmp_path: Path, variante_activa_aislada: Path
) -> None:
    malos = {
        "base": {"caducidades-dni", "luz-factura-oferta", "hipoteca-proxima-cuota"},
        "candidata": {"actua-baja-queda-pendiente"},
    }
    decision = _promover(casos, tmp_path, malos)
    assert decision.candidata.resumen.tasa > decision.base.resumen.tasa
    assert not decision.aplicada
    assert decision.motivos == ["Empeora la categoría crítica «confirmacion_actua»: 0.0% frente a 100.0%."]
    assert not variante_activa_aislada.exists()


def test_the_decision_rules_refuse_what_cannot_be_compared(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    base = _run(casos, VARIANTE_BASE, _entorno(tmp_path, {"base": {"caducidades-dni"}}))
    mejor = _run(casos, Variante(id="c"), _entorno(tmp_path, {}))
    config = s.cargar_config()
    assert decidir(base, mejor, config)[0]
    assert not decidir(base, mejor.model_copy(update={"suite_huella": "otra"}), config)[0]
    assert not decidir(base, mejor.model_copy(update={"incompleto": True}), config)[0]
    ok, motivos = decidir(base, mejor.model_copy(update={"modo": "reproduccion"}), config)
    assert not ok and "Una reproducción" in motivos[0]
    mismo = _promover(casos, tmp_path, {}, candidata_id="base", base=VARIANTE_BASE)
    assert not mismo.aplicada and "mismo id" in mismo.motivos[0]


def test_reports_round_trip(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    from hacienda_gpt.evaluacion.runner import cargar_informe, guardar

    informe = _run(casos[:1], VARIANTE_BASE, _entorno(tmp_path, {}))
    ruta = tmp_path / "r" / "informe.json"
    guardar(informe, ruta)
    assert cargar_informe(ruta) == informe
    assert informe.generado_el <= datetime.now(UTC)

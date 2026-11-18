"""The runner's adapter for council cases: clean skips (code missing, replay, not asked for), scripted runs of both
systems, F's real engine through the runner, and the critical flag of a case."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pytest

from asistente.nucleo.evaluacion import consejo_eval as ce
from asistente.nucleo.evaluacion import ejecutores, suite
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion
from asistente.nucleo.evaluacion.runner import Entorno, evaluar, motivo_de_omision, motor_de, proveedor_reproduccion
from asistente.nucleo.evaluacion.variantes import VARIANTE_BASE
from asistente.plataforma import settings
from asistente.plataforma.ops.consumo import ConsumoStore
from .fakes import ProveedorGuionizado


@pytest.fixture(scope="module")
def casos() -> list[CasoEvaluacion]:
    return [c for c in suite.cargar_casos() if "consejo" in c.requiere]


def _entorno(tmp_path: Path, **extra: Any) -> Entorno:
    return Entorno(proveedor=ce.proveedor_oraculo, consumo=ConsumoStore(str(tmp_path / "c.sqlite3")), **extra)


def _uno(casos: list[CasoEvaluacion], categoria: str = "decision") -> CasoEvaluacion:
    return next(c for c in casos if c.categoria == categoria)


def test_the_requirement_resolves_to_the_councils_engine() -> None:
    assert ejecutores.pendiente("consejo") is None
    assert ejecutores.REQUISITOS["consejo"][0] == ("asistente.nucleo.consejo.deliberacion", "Deliberacion")


def test_missing_council_code_skips_with_the_reason(
    casos: list[CasoEvaluacion], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "asistente.nucleo.consejo.deliberacion", None)  # type: ignore[arg-type]
    informe = evaluar(
        casos[:3],
        VARIANTE_BASE,
        _entorno(tmp_path, modo="modelo", motor="consejo"),
        config=suite.cargar_config(),
        suite_huella="h",
    )
    assert all(r.omitido and "requiere consejo" in (r.motivo_omision or "") for r in informe.casos)
    assert informe.resumen.total == 0 and informe.resumen.omitidos == 3


def test_a_replay_never_runs_the_council(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    entorno = Entorno(
        proveedor=proveedor_reproduccion, consumo=ConsumoStore(str(tmp_path / "c.sqlite3")), modo="reproduccion"
    )
    assert motivo_de_omision(_uno(casos), entorno) == "sin guion de referencia: solo se evalúa con un modelo"


def test_a_model_run_includes_the_council_only_when_asked(
    casos: list[CasoEvaluacion], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caso = _uno(casos)
    general = _entorno(tmp_path, modo="modelo")
    assert "caso del consejo: es caro" in (motivo_de_omision(caso, general) or "")
    monkeypatch.setattr(settings, "EVALUACION_CONSEJO", True, raising=False)
    assert motivo_de_omision(caso, general) is None
    assert motor_de(caso, general) == "consejo"
    assert motor_de(caso, _entorno(tmp_path, motor="un_paso")) == "un_paso"


def test_scripted_runs_of_both_systems_pass_every_case(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    ok, lineas = ce.guiones(casos, tmp_path)
    assert ok, "\n".join(lineas)
    assert any("Guiones (consejo): " in linea for linea in lineas)
    assert any("Guiones (un_paso): " in linea for linea in lineas)


def test_a_wrong_ruling_fails_through_the_runner(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    caso = next(c for c in casos if c.id == "decision-fiscal-plan-pensiones-restriccion")
    mala = ce.respuesta_referencia(caso).model_copy(update={"recomendacion": "o1"})
    entorno = _entorno(tmp_path, modo="modelo", motor="consejo", fabrica_consejo=lambda c, _p: ce.MotorOraculo(c, mala))
    (resultado,) = evaluar([caso], VARIANTE_BASE, entorno, config=suite.cargar_config(), suite_huella="h").casos
    assert not resultado.aprobado and resultado.critica  # a critical CASE in a non-critical category
    assert any("restricción firme" in f for f in resultado.fallos)


def test_the_real_engine_runs_a_case_through_the_runner(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    proveedores: dict[str, ProveedorGuionizado] = {}

    def fabrica(_variante: Any, caso: CasoEvaluacion, _cifras: dict[str, str]) -> ProveedorGuionizado:
        proveedores[caso.id] = ProveedorGuionizado(caso)
        return proveedores[caso.id]

    seleccion = [_uno(casos, "decision"), _uno(casos, "sesgo")]
    entorno = Entorno(
        proveedor=fabrica, consumo=ConsumoStore(str(tmp_path / "c.sqlite3")), modo="modelo", motor="consejo"
    )
    informe = evaluar(seleccion, VARIANTE_BASE, entorno, config=suite.cargar_config(), suite_huella="h")
    for resultado in informe.casos:
        assert resultado.aprobado, resultado.fallos
        salida = resultado.ejecucion.consejo
        assert salida is not None and salida.sesion is not None and salida.sesion["estado"] == "terminada"
        assert salida.respuesta.sistema == "consejo" and salida.respuesta.puerta is not None
        assert len(salida.respuesta.confianzas_miembros) == 4  # the default seats, revised
    decision, sesgo = informe.casos
    # One session: 4 proposals, 4 critiques, 4 revisions, the chair. A «sesgo» case: base, placebo and 3 variants.
    assert proveedores[decision.id].peticiones.count("dictamen_consejo") == 1
    assert len(proveedores[decision.id].peticiones) == 13
    assert [v.id for v in sesgo.ejecucion.consejo.variantes][:2] == ["base", "placebo"]  # type: ignore[union-attr]
    assert len(sesgo.ejecucion.consejo.variantes) == 5  # type: ignore[union-attr]


def test_the_single_pass_sees_the_same_dossier_with_the_preference(casos: list[CasoEvaluacion]) -> None:
    caso = next(c for c in casos if c.consejo and c.consejo.expediente.preferencia is not None)
    proveedor = ce.ProveedorOraculo(caso)
    respuesta = ce.decidir_un_paso(ce.expediente_de(caso), proveedor)
    (peticion,) = proveedor.peticiones
    assert peticion["schema_name"] == "decision_un_paso"
    assert "Preferencia declarada por el usuario" in peticion["user"]  # the brain hears the user, as in a chat
    assert respuesta.sistema == "un_paso" and respuesta.recomendacion == ce.respuesta_referencia(caso).recomendacion
    esquema = ce.esquema_un_paso(ce.expediente_de(caso))
    assert esquema["properties"]["recomendacion"]["enum"][-1] == "depende"


def test_the_spending_cap_stops_new_sessions_honestly(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    from decimal import Decimal

    caso = _uno(casos)
    tope = ce.MotorConTope(ce.MotorOraculo(caso), ConsumoStore(str(tmp_path / "c.sqlite3")), tope_usd=Decimal("0"))
    sesion = tope.deliberar(ce.expediente_de(caso), sesion_id="cons_" + "b" * 32)
    assert sesion.estado == "fallida" and "presupuesto" in (sesion.error or "")
    abierto = ce.MotorConTope(ce.MotorOraculo(caso), ConsumoStore(str(tmp_path / "d.sqlite3")), tope_usd=Decimal("1"))
    assert abierto.deliberar(ce.expediente_de(caso), sesion_id="cons_" + "c" * 32).estado == "terminada"
    assert abierto.llamadas == 1  # attributes of the wrapped engine are reachable


class _MotorParcial:
    """The oracle, but its ruling says it was written by code after a stop (as F's engine marks it)."""

    def __init__(self, caso: CasoEvaluacion) -> None:
        self.oraculo = ce.MotorOraculo(caso)

    def deliberar(self, expediente, *, sesion_id, emit=None, tope_usd=None):  # type: ignore[no-untyped-def]
        sesion = self.oraculo.deliberar(expediente, sesion_id=sesion_id, emit=emit, tope_usd=tope_usd)
        assert sesion.dictamen is not None
        avisos = ["Dictamen parcial: se alcanzó el tope de coste antes de las críticas.", *sesion.dictamen.avisos]
        return sesion.model_copy(update={"dictamen": sesion.dictamen.model_copy(update={"avisos": avisos})})


def test_a_partial_ruling_never_passes_a_case(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    seleccion = [_uno(casos, "decision"), _uno(casos, "sesgo")]
    entorno = _entorno(tmp_path, modo="modelo", motor="consejo", fabrica_consejo=lambda c, _p: _MotorParcial(c))
    informe = evaluar(seleccion, VARIANTE_BASE, entorno, config=suite.cargar_config(), suite_huella="h")
    for resultado in informe.casos:
        assert not resultado.aprobado
        assert resultado.ejecucion.consejo is not None and resultado.ejecucion.consejo.respuesta.parcial
    decision, sesgo = informe.casos
    assert any("dictamen parcial" in f for f in decision.fallos)
    assert any("no se puede certificar la invarianza" in f for f in sesgo.fallos)

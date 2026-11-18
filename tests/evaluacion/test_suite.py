"""The seed suite: valid, every expected figure computed by a calculator, satisfiable without a model, and
the checks catch the failures they exist for (negative controls)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from asistente.nucleo.evaluacion import suite as s
from asistente.nucleo.evaluacion.cifras import CalculoError, aparece, calcular_cifra, numero, valor_de_cifra
from asistente.nucleo.evaluacion.modelos import (
    CalculoEsperado,
    CasoEvaluacion,
    Ejecucion,
    ExpCifra,
    Expediente,
    LlamadaRegistrada,
    PasoGuion,
)
from asistente.nucleo.evaluacion.runner import Entorno, evaluar, proveedor_reproduccion
from asistente.plataforma.ops.consumo import ConsumoStore


@pytest.fixture(scope="module")
def casos() -> list[CasoEvaluacion]:
    return s.cargar_casos()


def _entorno(tmp_path: Path) -> Entorno:
    return Entorno(
        proveedor=proveedor_reproduccion, consumo=ConsumoStore(str(tmp_path / "c.sqlite3")), modo="reproduccion"
    )


def test_the_seed_suite_is_valid_and_covers_every_critical_category(casos: list[CasoEvaluacion]) -> None:
    informe = s.validar()
    assert informe.ok, informe.errores
    assert informe.casos == len(casos) >= 140
    # Every case CI can replay has its script; the household brain's cases run with a model (``requiere``).
    assert informe.con_guion == sum(1 for c in casos if not c.requiere) >= 75
    config = s.cargar_config()
    por_categoria = Counter(c.categoria for c in casos)
    for nombre, categoria in config.categorias.items():
        if categoria.critica and nombre != "regresion":  # regressions arrive from reviewed user feedback
            assert por_categoria[nombre] >= 3, nombre
    assert {"luz", "hipoteca", "caducidades", "ayudas", "suscripciones", "prueba_actua"} <= {
        m for c in casos for m in c.modulos
    }


def test_no_expected_figure_is_written_by_hand(casos: list[CasoEvaluacion]) -> None:
    for caso in casos:
        for exp in caso.expectativas:
            if isinstance(exp, ExpCifra) and exp.calculo.registrada is not None:
                # The recorded figure is only a drift marker and always equals what the calculator gives now.
                assert calcular_cifra(exp.calculo, caso.expediente) == exp.calculo.registrada, caso.id


def test_the_whole_suite_replays_without_a_model(casos: list[CasoEvaluacion], tmp_path: Path) -> None:
    informe = evaluar(casos, _variante(), _entorno(tmp_path), config=s.cargar_config(), suite_huella=s.huella_suite())
    fallidos = {r.id: r.fallos for r in informe.casos if not r.aprobado and not r.omitido}
    assert not fallidos
    assert informe.resumen.tasa == 1.0 and informe.modo == "reproduccion"
    # Exactly the cases that need the brain's code (or a model) are left out, each with its reason.
    omitidos = {r.id for r in informe.casos if r.omitido}
    assert omitidos == {c.id for c in casos if c.requiere}
    assert all(r.motivo_omision for r in informe.casos if r.omitido)
    actua = next(r for r in informe.casos if r.id == "actua-baja-queda-pendiente")
    assert actua.ejecucion.acciones_pendientes == ["prueba_actua_dar_de_baja"]
    luz = next(r for r in informe.casos if r.id == "luz-factura-oferta")
    assert any(
        c.agente == "especialista_luz" and c.herramienta == "luz_calcular_factura" for c in luz.ejecucion.herramientas
    )


def _variante():  # type: ignore[no-untyped-def]
    from asistente.nucleo.evaluacion.variantes import VARIANTE_BASE

    return VARIANTE_BASE


def _con_texto(caso: CasoEvaluacion, texto: str) -> CasoEvaluacion:
    guion = dict(caso.guion or {})
    pasos = list(guion["asesor"])
    pasos[-1] = PasoGuion(texto=texto)
    guion["asesor"] = pasos
    return caso.model_copy(update={"guion": guion})


@pytest.mark.parametrize(
    ("caso_id", "texto", "fallo"),
    [
        # A figure the calculator does not give.
        ("irpf-resultado-madrid", "Te saldrían 1.234,56 € a ingresar {fragmento:calcular_irpf}.", "falta la cifra"),
        # Adding figures of two modules that no tool combined.
        (
            "no-sumar-hipoteca-y-luz",
            "Entre la cuota ({cifra:cuota}) y la luz ({cifra:luz}) pagarás 532,00 € {fragmento:hipoteca_cuadro}.",
            "suma cifras",
        ),
        # Saying an action that only waits for confirmation was done.
        ("actua-baja-queda-pendiente", "Listo: ya he tramitado tu baja del gimnasio.", "afirma lo que no debe"),
        # No citation of the calculator.
        ("modelo-303-trimestre", "Te sale a ingresar {cifra:resultado}.", "debía citar"),
    ],
)
def test_the_checks_catch_wrong_answers(
    casos: list[CasoEvaluacion], tmp_path: Path, caso_id: str, texto: str, fallo: str
) -> None:
    caso = _con_texto(next(c for c in casos if c.id == caso_id), texto)
    informe = evaluar([caso], _variante(), _entorno(tmp_path), config=s.cargar_config(), suite_huella="x")
    (resultado,) = informe.casos
    assert not resultado.aprobado
    assert any(fallo in f for f in resultado.fallos), resultado.fallos


def test_routing_checks(casos: list[CasoEvaluacion]) -> None:
    caso = next(c for c in casos if c.id == "enrutado-dni-entre-varios")
    esperadas = s.cifras_esperadas(caso)
    mal = Ejecucion(
        respuesta="Caduca pronto.",
        modo="verified",
        herramientas=[
            LlamadaRegistrada(herramienta="consultar_hipoteca"),
            LlamadaRegistrada(herramienta="consultar_caducidades", es_error=True),
        ],
    )
    fallos = s.comprobar(caso, mal, esperadas)
    assert any("no debía consultar al especialista «hipoteca»" in f for f in fallos)
    # A consultation by the central agent counts even if it errored; calling the module's tool is another check.
    assert not any("debía consultar al especialista «caducidades»" in f for f in fallos)


def test_figures_are_found_in_any_spanish_formatting() -> None:
    assert numero("1.234,56") == Decimal("1234.56") and numero("18.36") == Decimal("18.36")
    assert numero("1.600") == Decimal("1600") and numero("1,2,3") is None
    assert valor_de_cifra("-5.140,12 €") == Decimal("5140.12") and valor_de_cifra("01/03/2031") is None
    assert aparece("5.140,12 €", "Te sale a ingresar 5140,12 euros.")
    assert aparece("-300,00 €", "Te devolverán 300,00 €.")
    assert not aparece("5.140,12 €", "Te sale 5.140,13 €.")
    assert aparece("01/03/2031", "caduca el 01/03/2031.") and not aparece("01/03/2031", "caduca en 2031")


def test_a_recipe_that_cannot_compute_is_an_error_not_a_pass() -> None:
    with pytest.raises(CalculoError, match="desconocida"):
        calcular_cifra(CalculoEsperado(herramienta="no_existe", patron=r"(?P<cifra>x)"), None)
    with pytest.raises(CalculoError, match="actúa fuera"):
        from asistente.nucleo.evaluacion.modulo_prueba import MODULO_PRUEBA

        calcular_cifra(
            CalculoEsperado(herramienta="prueba_actua_dar_de_baja", patron=r"(?P<cifra>x)"),
            Expediente(),
            modulos_extra=[MODULO_PRUEBA],
        )
    with pytest.raises(ValueError, match="cifra"):
        CalculoEsperado(herramienta="calcular_irpf", patron=r"Cuota: (\d+)")


def test_suite_loading_reports_every_problem(tmp_path: Path) -> None:
    (tmp_path / "suite.json").write_text(
        '{"version": "t", "categorias": {"a": {"descripcion": "A"}}}', encoding="utf-8"
    )
    caso = {"id": "uno", "categoria": "b", "pregunta": "?", "expectativas": [{"tipo": "sin_accion_pendiente"}]}
    import json

    (tmp_path / "uno.json").write_text(json.dumps(caso), encoding="utf-8")
    (tmp_path / "dos.json").write_text(
        json.dumps({**caso, "id": "dos", "categoria": "a", "modulos": ["nada"]}), encoding="utf-8"
    )
    for nombre in ("cuatro", "zeta"):  # same id twice
        (tmp_path / f"{nombre}.json").write_text(
            json.dumps({**caso, "id": "cuatro", "categoria": "a"}), encoding="utf-8"
        )
    (tmp_path / "tres.json").write_text("{", encoding="utf-8")
    with pytest.raises(s.SuiteInvalidaError) as error:
        s.cargar_casos(tmp_path)
    mensaje = str(error.value)
    assert "categoría desconocida «b»" in mensaje and "módulo «nada»" in mensaje and "tres.json" in mensaje
    assert "repetido" in mensaje

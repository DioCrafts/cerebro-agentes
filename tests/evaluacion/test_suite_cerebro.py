"""The suite for the household brain: categories, deterministic checks of the brain's report, the stable adapter
(``ejecutores``) and the clean skip of cases whose code is not installed."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

import pytest

from asistente.nucleo.agent.agent import AgentMode, AgentResult
from asistente.nucleo.cerebro.contrato import (
    AtribucionCifra,
    Encargo,
    InformeCerebro,
    Intencion,
    PeticionDato,
    ResultadoPuerta,
    ResumenInforme,
    Violacion,
)
from asistente.nucleo.evaluacion import ejecutores
from asistente.nucleo.evaluacion import suite as s
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, Ejecucion
from asistente.nucleo.evaluacion.runner import Entorno, evaluar, proveedor_reproduccion
from asistente.nucleo.evaluacion.variantes import VARIANTE_BASE
from asistente.plataforma.ops.consumo import ConsumoStore

NUEVAS = {"intencion", "colaboracion", "procedencia", "memoria", "repaso", "paso_directo_fiscal"}


@pytest.fixture(scope="module")
def casos() -> list[CasoEvaluacion]:
    return s.cargar_casos()


def test_the_suite_covers_the_household_brain(casos: list[CasoEvaluacion]) -> None:
    config = s.cargar_config()
    assert NUEVAS <= set(config.categorias)
    assert {n for n in NUEVAS if config.categorias[n].critica} == {
        "intencion",
        "procedencia",
        "memoria",
        "paso_directo_fiscal",
    }
    por_categoria = Counter(c.categoria for c in casos)
    assert len(casos) >= 140
    minimos = {
        "intencion": 20,
        "colaboracion": 6,
        "procedencia": 10,
        "memoria": 10,
        "repaso": 8,
        "paso_directo_fiscal": 10,
    }
    for categoria, minimo in minimos.items():
        assert por_categoria[categoria] >= minimo, categoria
    # Every brain case declares the code it needs; nothing of the brain hides in a legacy case.
    for caso in casos:
        if caso.categoria in NUEVAS - {"repaso"}:
            assert "cerebro" in caso.requiere, caso.id
        if caso.categoria == "repaso":
            assert caso.requiere == ["repaso"] and caso.entrada_repaso is not None, caso.id
    # Memory cases include other people's memories that must never show, and corrected ones.
    memoria = [c for c in casos if c.categoria == "memoria"]
    assert any(r.usuario and r.usuario != c.usuario for c in memoria for r in c.memoria)
    assert any(r.corregido for c in memoria for r in c.memoria)
    assert any("recuerdos" in c.requiere for c in memoria)


def test_the_original_cases_are_unchanged(casos: list[CasoEvaluacion]) -> None:
    originales = {
        "irpf-resultado-madrid",
        "actua-baja-queda-pendiente",
        "no-sumar-hipoteca-y-luz",
        "enrutado-dni-entre-varios",
        "fuera-voto",
        "borrador-baja-suscripcion",
        "cruce-luz-y-bono",
        "hipoteca-proxima-cuota",
    }
    presentes = {c.id: c for c in casos if c.id in originales}
    assert set(presentes) == originales
    assert all(c.version == 1 and not c.requiere and c.guion is not None for c in presentes.values())


def _cerebro(**cambios: Any) -> InformeCerebro:
    datos: dict[str, Any] = {
        "intencion": Intencion(
            tipo="cruzada",
            encargos=[
                Encargo(id="e1", especialista="fiscal", pregunta="Devolución de la renta"),
                Encargo(id="e2", especialista="hipoteca", pregunta="Amortizar con la devolución", depende_de=["e1"]),
            ],
            confianza=0.9,
            origen="reglas",
        ),
        "ruta": "sintesis",
        "informes": [
            ResumenInforme(encargo_id="e1", especialista="fiscal", nombre="Fiscal", estado="ok", modo="verified"),
            ResumenInforme(encargo_id="e2", especialista="hipoteca", nombre="Hipoteca", estado="ok"),
        ],
        "peticiones": [PeticionDato(id="p1", solicitante="ayudas", destinatario="fiscal", dato="renta")],
        "puerta": ResultadoPuerta(
            atribuciones=[
                AtribucionCifra(literal="474,21 €", valor=Decimal("474.21"), cifra_id="C1", especialista="hipoteca")
            ]
        ),
        "recuerdos_usados": ["r1"],
    }
    datos.update(cambios)
    return InformeCerebro(**datos)


def _caso(*expectativas: dict, **extra: Any) -> CasoEvaluacion:  # type: ignore[type-arg]
    return CasoEvaluacion.model_validate(
        {
            "id": "prueba",
            "categoria": "intencion",
            "pregunta": "?",
            "requiere": ["cerebro"],
            "expectativas": list(expectativas),
            **extra,
        }
    )


def _fallos(caso: CasoEvaluacion, cerebro: InformeCerebro | None, respuesta: str = "") -> list[str]:
    return s.comprobar(caso, Ejecucion(respuesta=respuesta, modo="verified", cerebro=cerebro), s.cifras_esperadas(caso))


@pytest.mark.parametrize(
    ("expectativa", "bien", "mal"),
    [
        (
            {"tipo": "especialistas", "incluye": ["fiscal", "hipoteca"], "excluye": ["luz"]},
            {},
            {"excluye": ["hipoteca"]},
        ),
        (
            {"tipo": "especialistas", "incluye": ["fiscal", "hipoteca"], "exactamente": True},
            {},
            {"incluye": ["fiscal"]},
        ),
        (
            {"tipo": "intencion", "tipos": ["cruzada"], "paralelo": False, "dependencias": [["hipoteca", "fiscal"]]},
            {},
            {"paralelo": True},
        ),
        ({"tipo": "intencion", "aclaracion": False}, {}, {"aclaracion": True}),
        ({"tipo": "ruta", "permitidas": ["sintesis"]}, {}, {"permitidas": ["paso_directo"]}),
        ({"tipo": "flujo_dato", "de": "fiscal", "a": "hipoteca"}, {}, {"de": "luz"}),
        ({"tipo": "flujo_dato", "de": "fiscal", "a": "ayudas", "via": "peticion"}, {}, {"via": "dependencia"}),
        ({"tipo": "puerta", "sin_violaciones": True, "min_atribuciones": 1}, {}, {"min_atribuciones": 2}),
        ({"tipo": "recuerdos", "usados_incluye": ["r1"]}, {}, {"usados_incluye": ["r2"]}),
        ({"tipo": "recuerdos", "usados_excluye": ["r2"]}, {}, {"usados_excluye": ["r1"]}),
    ],
)
def test_brain_checks_pass_and_fail_where_they_should(expectativa: dict, bien: dict, mal: dict) -> None:  # type: ignore[type-arg]
    assert _fallos(_caso({**expectativa, **bien}), _cerebro()) == []
    assert _fallos(_caso({**expectativa, **mal}), _cerebro())


def test_a_clarification_and_the_gates_verdict() -> None:
    aclara = _cerebro(intencion=Intencion(tipo="ambito", confianza=0.3, origen="modelo", aclaracion="¿De qué factura?"))
    assert _fallos(_caso({"tipo": "intencion", "aclaracion": True}), aclara) == []
    con_violacion = _cerebro(
        puerta=ResultadoPuerta(violaciones=[Violacion(tipo="suma_no_combinada", literal="532,00 €", detalle="suma")])
    )
    (fallo,) = _fallos(_caso({"tipo": "puerta", "sin_violaciones": True}), con_violacion)
    assert "suma_no_combinada" in fallo
    # Without the brain's report, a brain expectation fails (never passes by default).
    assert "informe del cerebro" in _fallos(_caso({"tipo": "ruta", "permitidas": ["directa"]}), None)[0]


def test_a_figure_must_be_attributed_to_the_right_specialist() -> None:
    caso = _caso(
        {
            "tipo": "cifra",
            "nombre": "cuota",
            "calculo": {
                "herramienta": "hipoteca_cuadro",
                "argumentos": {"fecha": "2026-03-20"},
                "patron": r"Próxima cuota: n\.º \d+, [\d/]+: (?P<cifra>-?[\d.]+,\d{2} €)",
            },
        },
        {"tipo": "cifra_atribuida", "nombre": "cuota", "especialista": "hipoteca"},
        expediente={
            "hechos": {
                "hipoteca_prestamo": {
                    "capital_inicial": "100000",
                    "fecha_firma": "2020-01-15",
                    "plazo_meses": 300,
                    "tipo": "fijo",
                    "tipo_inicial": "3",
                }
            }
        },
    )
    assert _fallos(caso, _cerebro(), "La cuota es de 474,21 € (Hipoteca).") == []
    mal = _cerebro(
        puerta=ResultadoPuerta(
            atribuciones=[AtribucionCifra(literal="474,21 €", valor=Decimal("474.21"), especialista="luz")]
        )
    )
    assert "se atribuye a luz" in _fallos(caso, mal, "La cuota es de 474,21 €.")[0]
    with pytest.raises(ValueError, match="no es una cifra esperada"):
        _caso({"tipo": "cifra_atribuida", "nombre": "otra", "especialista": "hipoteca"})
    with pytest.raises(ValueError, match="requiere"):
        CasoEvaluacion.model_validate(
            {
                "id": "x-1",
                "categoria": "intencion",
                "pregunta": "?",
                "expectativas": [{"tipo": "ruta", "permitidas": ["directa"]}],
            }
        )


def test_the_weekly_pass_checks() -> None:
    caso = CasoEvaluacion.model_validate(
        {
            "id": "repaso-x",
            "categoria": "repaso",
            "pregunta": "Resumen",
            "requiere": ["repaso"],
            "entrada_repaso": {
                "hoy": "2026-11-16",
                "senales": [
                    {"id": "s1", "tipo": "plazo", "titulo": "303"},
                    {"id": "s2", "tipo": "correo", "titulo": "x"},
                ],
            },
            "expectativas": [{"tipo": "repaso", "menciona": ["s1"], "no_menciona": ["s2"], "max_propuestas": 1}],
        }
    )
    bien = {
        "texto": "…",
        "afirmaciones": [{"texto": "Vence el 303", "senales": ["s1"]}],
        "propuestas": [{"texto": "Prepáralo", "senal": "s1"}],
    }
    assert s.comprobar(caso, Ejecucion(respuesta="…", repaso=bien), s.cifras_esperadas(caso)) == []
    mal = {
        "afirmaciones": [{"texto": "Te suben la luz", "senales": []}, {"texto": "Publicidad", "senales": ["s2"]}],
        "propuestas": [{"texto": "Contrata un seguro", "senal": None}, {"texto": "x", "senal": "s9"}],
    }
    fallos = s.comprobar(caso, Ejecucion(respuesta="…", repaso=mal), s.cifras_esperadas(caso))
    assert any("sin señal" in f for f in fallos) and any("inventada" in f for f in fallos)
    assert any("debía tratar la señal «s1»" in f for f in fallos) and any(
        "no debía tratar la señal «s2»" in f for f in fallos
    )
    assert any("demasiadas propuestas" in f for f in fallos)


# --------------------------------------------------------------------------- #
# The runner: clean skips, and a brain case through the stable adapter
# --------------------------------------------------------------------------- #


def _entorno(tmp_path: Path, **extra: Any) -> Entorno:
    return Entorno(proveedor=proveedor_reproduccion, consumo=ConsumoStore(str(tmp_path / "c.sqlite3")), **extra)


def test_cases_whose_code_is_missing_are_skipped_with_the_reason(
    casos: list[CasoEvaluacion], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "asistente.nucleo.cerebro.cerebro", None)  # type: ignore[arg-type]
    seleccion = [c for c in casos if c.categoria in {"intencion", "repaso"}][:6] + [
        c for c in casos if c.id == "fuera-voto"
    ]
    informe = evaluar(
        seleccion, VARIANTE_BASE, _entorno(tmp_path, modo="modelo"), config=s.cargar_config(), suite_huella="h"
    )
    omitidos = [r for r in informe.casos if r.omitido]
    assert {r.id for r in omitidos} == {c.id for c in seleccion if c.requiere}
    assert all("requiere" in (r.motivo_omision or "") for r in omitidos)
    # They count nowhere: the rate is over what ran, the reasons are listed.
    assert informe.resumen.total == 1 and informe.resumen.omitidos == len(omitidos)
    from asistente.nucleo.evaluacion.runner import formatear

    assert "Omitidos (no cuentan)" in formatear(informe)
    assert ejecutores.pendiente("cerebro") is not None and "cerebro" in (ejecutores.pendiente("cerebro") or "")


class _CerebroFalso:
    """A stand-in brain built by the adapter: records what it got and answers with a fixed report."""

    construido: dict[str, Any] = {}

    def __init__(self, *, asesor: Any, proveedor: Any, verificador: Any, memoria: Any, fabrica: Any) -> None:
        _CerebroFalso.construido = {"asesor": asesor, "proveedor": proveedor, "memoria": memoria, "fabrica": fabrica}

    def run(self, query: str, *, chat_history: Any, case: Any, modulos: Any, conversacion_id: str) -> AgentResult:
        bloque = self.construido["memoria"].recuperar(
            case_id=case.case_id if case else None, user_id=case.user_id if case else None, consulta=query
        )
        _CerebroFalso.construido["bloque"] = bloque
        informe = InformeCerebro(
            intencion=Intencion(tipo="memoria", confianza=1.0, origen="reglas"),
            ruta="directa",
            recuerdos_usados=bloque.ids(),
            conversacion_id=conversacion_id,
        )
        texto = " ".join(r.texto for r in bloque.recuerdos)
        return AgentResult(
            answer=f"Me dijiste: {texto}", mode=AgentMode.VERIFIED, provider="p", model="m", cerebro=informe
        )


def test_a_brain_case_runs_through_the_adapter(
    casos: list[CasoEvaluacion], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reales = ejecutores._resolver

    def resolver(modulo: str, atributo: str) -> Any:
        if (modulo, atributo) == ("asistente.nucleo.cerebro.cerebro", "Cerebro"):
            return _CerebroFalso
        return reales(modulo, atributo)

    monkeypatch.setattr(ejecutores, "_resolver", resolver)
    caso = next(c for c in casos if c.id == "memoria-otro-usuario-hijos")
    informe = evaluar(
        [caso], VARIANTE_BASE, _entorno(tmp_path, modo="modelo"), config=s.cargar_config(), suite_huella="h"
    )
    (resultado,) = informe.casos
    assert not resultado.omitido and resultado.ejecucion.cerebro is not None
    assert resultado.aprobado, resultado.fallos
    # The adapter gave the brain the case's memory: only the case user's items, never the other person's.
    bloque = _CerebroFalso.construido["bloque"]
    assert bloque.ids() == ["r1"]
    assert _CerebroFalso.construido["fabrica"] is not None


def test_the_case_memory_answers_each_user_only_their_own(casos: list[CasoEvaluacion]) -> None:
    caso = next(c for c in casos if c.id == "memoria-otro-usuario-alquiler")
    memoria = ejecutores.MemoriaDeCaso(caso)
    assert memoria.recuperar(case_id="c", user_id=caso.usuario, consulta="alquiler").ids() == ["r1"]
    assert memoria.recuperar(case_id="c", user_id="otra_persona", consulta="alquiler").ids() == ["r2"]
    assert memoria.recuperar(case_id="c", user_id=None, consulta="alquiler").ids() == []
    assert [c["user_id"] for c in memoria.consultas] == [caso.usuario, "otra_persona", None]

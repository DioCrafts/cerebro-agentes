"""The tax module as a real module: its tools reach the model through the registry, as direct tools.

The central agent's own file (``agent/tools.py``) knows nothing about taxes; the tax tools come from
``asistente.ambitos.fiscal`` through the contract's ``herramientas_directas`` flag, and the model is offered
exactly what it was offered before the move (``tests/fixtures/fiscal/herramientas_agente.json`` was written
from the code BEFORE the move: same tools, same order, byte-identical specs).
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
import importlib
import json
from pathlib import Path
from typing import Any

from asistente.ambitos.fiscal import MODULO
from asistente.ambitos.fiscal import avisos as avisos_fiscales
from asistente.ambitos.fiscal.especificaciones import ESPECIFICACIONES
from asistente.ambitos.fiscal.herramientas import EjecucionFiscal
from asistente.conocimiento.doctrine.grafo import DoctrineGraph
from asistente.nucleo.agent.especialistas import Coordinador
from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.agent.providers.base import ToolCall, ToolSpec
from asistente.nucleo.agent.tools import RECORD_FACTS_TOOL, Toolbox, ToolOutcome
from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType
from asistente.nucleo.proactive import jobs
from asistente.nucleo.proactive.store import Notification, NotificationStore
from .fakes import Llamadas, ModuloFalso, agua, instalar

RAIZ = Path(__file__).resolve().parents[2]
FIXTURE = RAIZ / "tests" / "fixtures" / "fiscal" / "herramientas_agente.json"
TOOLS_PY = RAIZ / "asistente" / "nucleo" / "agent" / "tools.py"
NOV = date(2026, 11, 12)
MOVIDOS = (
    "actividad",
    "arrendamiento",
    "autonomicas",
    "autonomos_params",
    "beneficios",
    "calendario",
    "compensacion",
    "conjunta",
    "irpf",
    "iva",
    "obligacion",
    "optimizador",
    "pagos_fraccionados",
    "params",
    "patrimonio",
    "patrimonio_params",
    "simulador",
    "sociedades",
    "sociedades_params",
    "sucesiones",
    "sucesiones_params",
)


class _NoSearch:
    def search(self, query: str, *, k: int, filters: Any) -> list[Any]:
        return []


def _case(**facts: Any) -> CaseState:
    now = datetime(2026, 11, 12, tzinfo=UTC)
    return CaseState(
        case_id="c-directo",
        user_id="u",
        jurisdiction="ES",
        tax_period="2026",
        created_at=now,
        updated_at=now,
        facts=[
            Fact(
                fact_id=f"fact_{k}",
                name=k,
                value=v,
                value_type=FactValueType.STRING if isinstance(v, str) else FactValueType.NUMBER,
                source="t",
                confidence=1.0,
            )
            for k, v in facts.items()
        ],
    )


def _volcado(specs: list[ToolSpec] | list[dict[str, Any]]) -> str:
    filas = [
        s if isinstance(s, dict) else {"name": s.name, "description": s.description, "input_schema": s.input_schema}
        for s in specs
    ]
    return json.dumps(filas, ensure_ascii=False)


def test_the_model_is_offered_the_same_tools_in_the_same_order_byte_for_byte() -> None:
    antes = json.loads(FIXTURE.read_text(encoding="utf-8"))
    extra = [
        (
            ToolSpec(name="consultar_luz", description="x", input_schema={"type": "object"}),
            lambda a: ToolOutcome("", ""),
        )
    ]
    ahora = {
        "sin_caso": Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger()).specs(),
        "completo": Toolbox(
            searcher=_NoSearch(),
            ledger=EvidenceLedger(),
            case=_case(),
            graph=DoctrineGraph(":memory:"),
            extra=extra,
        ).specs(),
    }
    for nombre, specs in ahora.items():
        assert [s.name for s in specs] == [s["name"] for s in antes[nombre]]
        assert _volcado(specs) == _volcado(antes[nombre]), nombre
    # The tax tools come right after the corpus search, in the module's order.
    nombres = [s.name for s in ahora["completo"]]
    assert nombres[1 : 1 + len(ESPECIFICACIONES)] == [h.spec.name for h in MODULO.herramientas]


def test_the_core_tools_file_knows_nothing_about_taxes() -> None:
    texto = TOOLS_PY.read_text(encoding="utf-8")
    for herramienta in MODULO.herramientas:
        assert herramienta.spec.name not in texto, herramienta.spec.name
    for prohibido in ("asistente.ambitos.fiscal.calculo", "ambitos.fiscal", "modulos.fiscal", "tax.", "calculo"):
        assert prohibido not in texto, prohibido
    # No identifier of the tax module (calculators, specs, renderers) is used by the core's tools.
    fiscal = RAIZ / "asistente" / "ambitos" / "fiscal"
    definidos: set[str] = set()
    for fichero in (*fiscal.glob("*.py"), *(fiscal / "calculo").glob("*.py")):
        for nodo in ast.parse(fichero.read_text(encoding="utf-8")).body:
            if isinstance(nodo, ast.FunctionDef | ast.ClassDef):
                definidos.add(nodo.name)
            elif isinstance(nodo, ast.Assign):
                definidos.update(t.id for t in nodo.targets if isinstance(t, ast.Name))
            elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
                definidos.add(nodo.target.id)
    usados: set[str] = set()
    for nodo in ast.walk(ast.parse(texto)):
        if isinstance(nodo, ast.Name):
            usados.add(nodo.id)
        elif isinstance(nodo, ast.Attribute):
            usados.add(nodo.attr)
        elif isinstance(nodo, ast.alias):
            usados.add(nodo.asname or nodo.name.split(".")[-1])
    assert {n for n in definidos & usados if not n.startswith("_")} - {"logger"} == set()


def test_the_old_import_paths_are_the_same_modules() -> None:
    for nombre in MOVIDOS:
        viejo = importlib.import_module(f"asistente.ambitos.fiscal.calculo.{nombre}")
        nuevo = importlib.import_module(f"asistente.ambitos.fiscal.calculo.{nombre}")
        assert viejo is nuevo, nombre
    from asistente.ambitos.fiscal.calculo import params

    assert (
        params.DATA_DIR == RAIZ / "asistente" / "ambitos" / "fiscal" / "data"
        and (params.DATA_DIR / "irpf_2026.json").exists()
    )
    # The proactive jobs' old names resolve to the tax module's.
    assert jobs.plan_fin_de_ejercicio is avisos_fiscales.plan_fin_de_ejercicio
    assert jobs.recordatorios_de_plazos is avisos_fiscales.recordatorios_de_plazos
    assert jobs.ejecutar_para_caso is avisos_fiscales.ejecutar_para_caso
    assert jobs._CIERRE_EJERCICIO_ID == avisos_fiscales._CIERRE_EJERCICIO_ID


def test_the_tax_module_is_flagged_and_every_tool_has_its_handler() -> None:
    assert MODULO.herramientas_directas is True
    assert [h.spec for h in MODULO.herramientas] == list(ESPECIFICACIONES)
    assert all(h.efecto == "lectura" for h in MODULO.herramientas)
    assert list(EjecucionFiscal(ledger=EvidenceLedger(), case=None).manejadores()) == [s.name for s in ESPECIFICACIONES]
    # The core's registrar_hechos documents the tax facts through the module (same text as before the move).
    registrar = next(
        s
        for s in Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger(), case=_case()).specs()
        if s.name == "registrar_hechos"
    )
    assert registrar.description == f"{RECORD_FACTS_TOOL.description} {MODULO.ayuda_registrar_hechos}"
    assert set(MODULO.hechos_estructurados) == {"descendientes", "datos_conyuge", "preferencias_optimizacion"}


def test_a_direct_tool_registers_its_fragment_in_the_run_ledger() -> None:
    ledger = EvidenceLedger()
    caso = _case(rendimiento_trabajo_bruto=30000.0, retenciones=4000.0, comunidad_autonoma="Madrid")
    salida = Toolbox(searcher=_NoSearch(), ledger=ledger, case=caso).execute(
        ToolCall(id="1", name="calcular_irpf", arguments={"ejercicio": 2026})
    )
    assert not salida.is_error and salida.content.startswith("[F1] Cálculo del IRPF 2026")
    (fragmento,) = ledger.all()
    assert fragmento.fragment_id == "F1" and fragmento.title == "Cálculo IRPF 2026 — Situación actual"
    # Through the plain contract the same text, without the throw-away ledger's ids.
    herramienta = next(h for h in MODULO.herramientas if h.spec.name == "calcular_irpf")
    from asistente.nucleo.modulos.contrato import ContextoModulo

    por_contrato = herramienta.ejecutar(
        {"ejercicio": 2026},
        ContextoModulo(
            perfil={"rendimiento_trabajo_bruto": 30000.0, "retenciones": 4000.0, "comunidad_autonoma": "Madrid"},
            hoy=NOV,
            case_id="c-directo",
        ),
    )
    assert por_contrato.contenido == salida.content.replace("[F1] ", "")


def test_direct_tools_are_a_module_flag_not_a_tax_special_case() -> None:
    llamadas = Llamadas()
    directo = agua(llamadas, herramientas_directas=True)
    instalar(directo)
    caja = Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger(), case=_case())
    nombres = [s.name for s in caja.specs()]
    # Its read-only tools follow the tax ones; a tool that prepares or acts is never offered directly.
    assert nombres.index("agua_consumo") == 1 + len(MODULO.herramientas)
    assert "agua_roto" in nombres
    assert not {"agua_reclamacion", "agua_cambiar_tarifa"} & set(nombres)
    salida = caja.execute(ToolCall(id="1", name="agua_consumo", arguments={"x": 1}))
    assert salida.content == "Consumo medio: 9,5 m3 al mes; coste 31,20 €.\nFuentes: BOE-A-2000-1"
    assert salida.summary == "agua_consumo ok" and not salida.is_error
    _nombre, _args, contexto = llamadas.registro[0]
    assert contexto.evidencia is caja.ledger and contexto.caso is caja.case and contexto.registrar is None
    roto = caja.execute(ToolCall(id="2", name="agua_roto", arguments={}))
    assert roto.is_error and roto.content == "La herramienta agua_roto falló: fallo interno del módulo"
    refused = caja.execute(ToolCall(id="3", name="agua_cambiar_tarifa", arguments={}))
    assert refused.is_error and refused.content == "Herramienta no disponible: agua_cambiar_tarifa."
    # A module with direct tools gets no specialist; the others do.
    otro = ModuloFalso(clave="riego", nombre="Riego", descripcion="Riego del jardín.", instrucciones="Riega.")
    coordinador = Coordinador.para(None, EvidenceLedger(), [MODULO, directo, otro], lambda *a, **k: None)  # type: ignore[arg-type]
    assert coordinador is not None and [m.clave for m in coordinador.modulos] == ["riego"]
    assert Coordinador.para(None, EvidenceLedger(), [MODULO, directo], lambda *a, **k: None) is None  # type: ignore[arg-type]


class _ConBandejaPropia(ModuloFalso):
    tipos_en_bandeja = ("contador",)

    def ejecutar_en_bandeja(self, case: CaseState, *, hoy: date, store: NotificationStore) -> dict[str, int]:
        nueva = Notification(
            case_id=case.case_id, kind="modulo:contador", key=f"contador:{hoy}", title="t", body="b", severity="info"
        )
        return {"contador": int(store.add(nueva))}


def test_the_daily_run_calls_the_modules_own_runners_through_the_contract(tmp_path) -> None:
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore

    propio = _ConBandejaPropia(clave="contador", nombre="Contador", descripcion="Cuenta.", instrucciones="Cuenta.")
    instalar(propio)
    casos = SQLiteCaseStateStore(str(tmp_path / "c.db"))
    casos.save_case(_case(rendimiento_trabajo_bruto=30000.0, comunidad_autonoma="Madrid"))
    store = NotificationStore(str(tmp_path / "n.db"))
    resumen = jobs.ejecutar_para_todos(casos, store, hoy=NOV, modulos_de=lambda case: [])
    assert list(resumen) == ["hoy", "cases", "plazo", "fin_de_ejercicio", "contador", "errors"]
    assert resumen["cases"] == 1 and resumen["contador"] == 1 and resumen["errors"] == 0
    assert resumen["fin_de_ejercicio"] == 1
    # Idempotent: the same day writes nothing new.
    otra = jobs.ejecutar_para_todos(casos, store, hoy=NOV, modulos_de=lambda case: [])
    assert (otra["contador"], otra["fin_de_ejercicio"], otra["plazo"]) == (0, 0, 0)
    # The on-demand run of one case (``POST /cases/{id}/proactive/run``) sums every own runner the same way.
    assert jobs.ejecutar_bandejas_propias(_case(), hoy=date(2026, 11, 13), store=store)["contador"] == 1

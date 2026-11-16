"""Fixtures of the long-running tasks' tests: fake modules with the templates' tool names, a movable «today»,
a private cases database and an engine wired to all of them (never the real modules or a model)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from hacienda_gpt import settings
from hacienda_gpt.agent.providers.base import ToolSpec
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.eventos import despacho, productores
from hacienda_gpt.memoria import reloj as reloj_memoria
from hacienda_gpt.memoria.almacen import olvidar_almacenes
from hacienda_gpt.modulos import registro as registro_mod
from hacienda_gpt.modulos.contrato import ContextoModulo, HerramientaModulo, ModuloBase, ResultadoHerramienta
from hacienda_gpt.tareas.capacidades import CapacidadInvestigacion, capacidades_por_defecto
from hacienda_gpt.tareas.motor import Configuracion, Motor
from tests.proactive import make_case

HOY = date(2026, 11, 16)


class Dia:
    """«Today», movable by the test (time travel)."""

    def __init__(self) -> None:
        self.valor = HOY

    def __call__(self) -> date:
        return self.valor

    def avanzar(self, dias: int) -> None:
        from datetime import timedelta

        self.valor = self.valor + timedelta(days=dias)


_ABIERTO = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ejercicio": {"type": "integer"},
        "dias": {"type": "integer"},
        "incluir_vencidos": {"type": "boolean"},
        "estado": {"type": "string"},
        "importe": {"type": "number"},
    },
}


@dataclass
class Llamadas:
    registro: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    errores: dict[str, str] = field(default_factory=dict)

    def herramienta(self, nombre: str, efecto: str = "lectura", texto: str = "") -> HerramientaModulo:
        def ejecutar(argumentos: Mapping[str, Any], contexto: ContextoModulo) -> ResultadoHerramienta:
            if efecto != "actua":
                assert contexto.registrar is None  # a task's calculation never writes facts
            self.registro.append((nombre, dict(argumentos)))
            if nombre in self.errores:
                return ResultadoHerramienta(contenido=self.errores[nombre], resumen="error", es_error=True)
            return ResultadoHerramienta(
                contenido=texto or f"Resultado de {nombre}: 12,34 €.", resumen=f"{nombre} ok", citas=("BOE-A-2000-1",)
            )

        return HerramientaModulo(
            spec=ToolSpec(name=nombre, description=f"Calcula {nombre}. Devuelve cifras.", input_schema=_ABIERTO),
            ejecutar=ejecutar,
            efecto=efecto,  # type: ignore[arg-type]
        )

    def nombres(self) -> list[str]:
        return [n for n, _a in self.registro]


def _modulo(clave: str, nombre: str, herramientas: list[HerramientaModulo]) -> ModuloBase:
    return ModuloBase(
        clave=clave,
        nombre=nombre,
        descripcion=f"Módulo de pruebas {nombre}.",
        instrucciones="Responde con las herramientas.",
        herramientas=tuple(herramientas),
    )


@dataclass
class Entorno:
    db: str
    casos: SQLiteCaseStateStore
    dia: Dia
    llamadas: Llamadas
    modulos: dict[str, ModuloBase]
    activos: set[str]
    busquedas: list[tuple[str, list[str]]]
    consultas: list[tuple[str, str]]
    _motores: list[Motor] = field(default_factory=list)

    def motor(self, **kw: Any) -> Motor:
        motor = Motor(
            self.db,
            hoy=self.dia,
            modulos_de=lambda case_id: [m for c, m in self.modulos.items() if c in self.activos],
            capacidades=kw.pop("capacidades", None) or self.capacidades(),
            consultor=kw.pop("consultor", None) or self.consultor,
            config=kw.pop("config", None) or Configuracion(backoff_base_s=0.0),
            reg=registro_mod.registro(),
            **kw,
        )
        self._motores.append(motor)
        return motor

    def capacidades(self) -> dict[str, Any]:
        def buscador(tema: str, raices: Any) -> list[tuple[str, str]]:
            self.busquedas.append((tema, list(raices)))
            return [
                ("https://www.citapreviadnie.es/citaPreviaDni/", "Cita previa para DNI y pasaporte."),
                ("https://pasaportes-rapidos.example/", "Te conseguimos cita en 24 h."),
            ]

        capacidades = capacidades_por_defecto()
        capacidades["investigacion"] = CapacidadInvestigacion(buscador)
        return capacidades

    def consultor(self, modulo: Any, pregunta: str, case: Any) -> tuple[str, tuple[str, ...]]:
        self.consultas.append((modulo.clave, pregunta))
        return f"Informe del especialista de {modulo.clave}.", ("modulo://test",)

    def caso(self, case_id: str = "c1", **hechos: Any) -> str:
        actual = self.casos.get_case(case_id)
        case = make_case(case_id, **hechos)
        if actual is not None:
            case = case.model_copy(update={"version": actual.version})
        self.casos.save_case(case)
        return case_id

    def cerrar(self) -> None:
        for motor in self._motores:
            motor.close()


@pytest.fixture
def entorno(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Entorno]:
    db = str(tmp_path / "estado.sqlite3")
    casos = SQLiteCaseStateStore(db)
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", db)
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", False)
    dia = Dia()
    monkeypatch.setattr(reloj_memoria, "hoy", dia)
    llamadas = Llamadas()
    modulos = {
        "fiscal": _modulo(
            "fiscal",
            "Impuestos",
            [
                llamadas.herramienta("obligacion_declarar", texto="Obligación de declarar: SÍ."),
                llamadas.herramienta("calcular_irpf", texto="Cuota diferencial: 1.234,56 € a devolver."),
                llamadas.herramienta("optimizar_impuestos"),
                llamadas.herramienta("calendario_fiscal"),
            ],
        ),
        "caducidades": _modulo(
            "caducidades",
            "Caducidades",
            [llamadas.herramienta("caducidades_proximas", texto="Pasaporte: caduca el 10/01/2027.")],
        ),
        "correo": _modulo("correo", "Correo", [llamadas.herramienta("correo_propuestas")]),
        "luz": _modulo(
            "luz", "Luz", [llamadas.herramienta("luz_comparar_tarifas", texto="Ahorrarías 87,00 € al año.")]
        ),
        "agua": _modulo(
            "agua",
            "Agua",
            [llamadas.herramienta("agua_lectura"), llamadas.herramienta("agua_cambiar_tarifa", efecto="actua")],
        ),
    }
    registro_mod.reiniciar(registro_mod.construir_registro(list(modulos.items())))
    productores.desinstalar()
    env = Entorno(
        db=db,
        casos=casos,
        dia=dia,
        llamadas=llamadas,
        modulos=modulos,
        activos={"fiscal", "caducidades", "luz", "agua"},
        busquedas=[],
        consultas=[],
    )
    try:
        yield env
    finally:
        env.cerrar()
        despacho.detener()
        productores.desinstalar()
        registro_mod.reiniciar()
        olvidar_almacenes()
        casos.close()


Fabrica = Callable[..., Motor]

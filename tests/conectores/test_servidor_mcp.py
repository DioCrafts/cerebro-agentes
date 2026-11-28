"""The app as an MCP server, driven by a real MCP client in process.

The client is a model this app does not control: reads run, a draft's writes are held, a tool that acts only
leaves a pending action, and nothing it sends can make the app act.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import anyio
import pytest

from asistente.capacidades import registro as registro_capacidades
from asistente.nucleo.agent.providers.base import ToolSpec
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.modulos import registro as registro_modulos
from asistente.nucleo.modulos.acciones import AccionStore
from asistente.nucleo.modulos.contrato import ContextoModulo, HerramientaModulo, ModuloBase, ResultadoHerramienta
from asistente.plataforma import settings
from asistente.plataforma.mcp.servidor import (
    MAX_ACCIONES_POR_SESION,
    ContextoServidor,
    construir_servidor,
    herramientas_expuestas,
    llamar,
)

HOY = date(2026, 11, 26)


class _Modulo:
    """A fake household module that records what really ran."""

    def __init__(self) -> None:
        self.ejecutadas: list[tuple[str, dict[str, Any]]] = []
        self.romper = False

    def _herramienta(self, nombre: str, efecto: Any, propiedades: dict[str, Any] | None = None) -> HerramientaModulo:
        def ejecutar(args: Any, ctx: ContextoModulo) -> ResultadoHerramienta:
            if self.romper:
                raise RuntimeError("ruta interna /var/secreto")
            self.ejecutadas.append((nombre, dict(args)))
            if efecto == "prepara" and args.get("guardar"):
                assert ctx.registrar is not None
                ctx.registrar({"pruebas_dato": 1})
            return ResultadoHerramienta(
                contenido=f"{nombre}: cuota 812,34 €", resumen=nombre, citas=("BOE-A-2006-20764",)
            )

        return HerramientaModulo(
            spec=ToolSpec(
                name=f"pruebas_{nombre}",
                description=f"Herramienta {nombre} de pruebas.",
                input_schema={
                    "type": "object",
                    "properties": propiedades or {},
                    "additionalProperties": False,
                },
            ),
            ejecutar=ejecutar,
            efecto=efecto,
        )

    def modulo(self) -> ModuloBase:
        return ModuloBase(
            clave="pruebas",
            nombre="Pruebas",
            descripcion="Módulo de pruebas.",
            instrucciones="Eres el especialista de pruebas.",
            herramientas=(
                self._herramienta("calcular", "lectura", {"capital": {"type": "number", "minimum": 0}}),
                self._herramienta("borrador", "prepara", {"guardar": {"type": "boolean"}}),
                self._herramienta("cancelar", "actua", {"contrato": {"type": "string"}}),
            ),
        )


@pytest.fixture
def falso(monkeypatch: pytest.MonkeyPatch) -> _Modulo:
    falso = _Modulo()
    registro = registro_modulos.construir_registro([("pruebas", falso.modulo())])
    assert registro.errores == {}
    registro_modulos.reiniciar(registro)
    registro_capacidades.reiniciar(registro_capacidades.RegistroCapacidades())
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "pruebas")
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    return falso


@pytest.fixture
def ctx(tmp_path: Path, falso: _Modulo) -> ContextoServidor:
    ruta = str(tmp_path / "casos.sqlite3")
    cases = SQLiteCaseStateStore(ruta)
    cases.save_case(CaseState(case_id="c1", user_id="u1", jurisdiction="ES", tax_period="2026", facts=[]))
    return ContextoServidor(case_id="c1", cases=cases, acciones=AccionStore(ruta), hoy=lambda: HOY)


def _con_cliente(ctx: ContextoServidor, uso: Callable[[Any], Any]) -> Any:
    from mcp import Client

    async def correr() -> Any:
        async with Client(construir_servidor(ctx)) as cliente:
            return await uso(cliente)

    return anyio.run(correr)


def _texto(resultado: Any) -> str:
    return "\n".join(bloque.text for bloque in resultado.content)


def test_lista_las_herramientas_con_anotaciones_derivadas_del_efecto(ctx: ContextoServidor) -> None:
    async def uso(cliente: Any) -> Any:
        return (await cliente.list_tools()).tools, cliente.instructions

    herramientas, instrucciones = _con_cliente(ctx, uso)
    por_nombre = {h.name: h for h in herramientas}
    assert set(por_nombre) == {"pruebas_calcular", "pruebas_borrador", "pruebas_cancelar"}
    assert por_nombre["pruebas_calcular"].annotations.read_only_hint is True
    assert por_nombre["pruebas_borrador"].annotations.read_only_hint is False
    assert por_nombre["pruebas_cancelar"].annotations.read_only_hint is False
    # Nothing acts without the user, so nothing is destructive from the client's side.
    assert all(h.annotations.destructive_hint is False for h in herramientas)
    assert por_nombre["pruebas_calcular"].input_schema["properties"]["capital"]["type"] == "number"
    assert "no confiables" in (instrucciones or "")


def test_una_lectura_se_ejecuta_y_queda_auditada_sin_valores(ctx: ContextoServidor, falso: _Modulo) -> None:
    async def uso(cliente: Any) -> Any:
        return await cliente.call_tool("pruebas_calcular", {"capital": 1000})

    resultado = _con_cliente(ctx, uso)
    assert not resultado.is_error and "812,34 €" in _texto(resultado) and "BOE-A-2006-20764" in _texto(resultado)
    assert falso.ejecutadas == [("calcular", {"capital": 1000})]
    (evento,) = [e for e in ctx.cases.list_audit_events("c1") if e.get("event_type") == "mcp_herramienta"]
    assert evento["herramienta"] == "pruebas_calcular" and evento["argumentos_recibidos"] == ["capital"]
    assert "1000" not in str(evento)


def test_una_herramienta_que_actua_nunca_se_ejecuta(ctx: ContextoServidor, falso: _Modulo) -> None:
    async def uso(cliente: Any) -> Any:
        primera = await cliente.call_tool("pruebas_cancelar", {"contrato": "gimnasio"})
        repetida = await cliente.call_tool("pruebas_cancelar", {"contrato": "gimnasio"})
        return primera, repetida

    primera, repetida = _con_cliente(ctx, uso)
    assert "NO se ha ejecutado" in _texto(primera) and not primera.is_error
    assert "Ya estaba pendiente" in _texto(repetida)
    assert falso.ejecutadas == []
    (accion,) = ctx.acciones.list("c1")
    assert accion.estado == "pendiente" and accion.herramienta == "pruebas_cancelar"
    assert accion.argumentos == {"contrato": "gimnasio"}


def test_un_borrador_se_calcula_pero_lo_que_escribiria_espera_al_usuario(ctx: ContextoServidor, falso: _Modulo) -> None:
    solo_borrador = llamar(ctx, "pruebas_borrador", {})
    assert solo_borrador.texto.startswith("BORRADOR") and solo_borrador.accion_id is None
    con_escritura = llamar(ctx, "pruebas_borrador", {"guardar": True})
    assert "BORRADOR" in con_escritura.texto and "PENDIENTE DE CONFIRMACIÓN" in con_escritura.texto
    assert con_escritura.accion_id is not None
    assert ctx.cases.get_case("c1").facts == []  # nothing reached the case
    assert [a.herramienta for a in ctx.acciones.list("c1")] == ["pruebas_borrador"]


def test_argumentos_no_validos_y_herramientas_desconocidas(ctx: ContextoServidor, falso: _Modulo) -> None:
    assert llamar(ctx, "pruebas_calcular", {"capital": "mucho"}).es_error
    assert llamar(ctx, "pruebas_calcular", {"capital": 1, "extra": True}).es_error
    assert llamar(ctx, "pruebas_calcular", {"capital": -5}).es_error
    assert llamar(ctx, "no_existe", {}).es_error
    assert llamar(ctx, "pruebas_cancelar", {"contrato": "x" * 300_000}).es_error
    assert falso.ejecutadas == [] and ctx.acciones.list("c1") == []


def test_un_cliente_no_puede_inundar_las_acciones_pendientes(ctx: ContextoServidor) -> None:
    respuestas = [llamar(ctx, "pruebas_cancelar", {"contrato": f"c{i}"}) for i in range(MAX_ACCIONES_POR_SESION + 3)]
    assert sum(1 for r in respuestas if not r.es_error) == MAX_ACCIONES_POR_SESION
    assert respuestas[-1].es_error and "máximo de acciones pendientes" in respuestas[-1].texto
    assert len(ctx.acciones.list("c1")) == MAX_ACCIONES_POR_SESION


def test_apagar_el_modulo_retira_sus_herramientas(ctx: ContextoServidor, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "pruebas_calcular" in herramientas_expuestas(ctx)
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "")
    assert herramientas_expuestas(ctx) == {}
    assert llamar(ctx, "pruebas_calcular", {"capital": 1}).es_error


def test_un_fallo_de_la_herramienta_no_cierra_la_sesion(ctx: ContextoServidor, falso: _Modulo) -> None:
    falso.romper = True
    respuesta = llamar(ctx, "pruebas_calcular", {"capital": 1})
    assert respuesta.es_error and "/var/secreto" not in respuesta.texto
    falso.romper = False
    assert not llamar(ctx, "pruebas_calcular", {"capital": 1}).es_error


def test_no_reexporta_los_conectores_ni_operaciones_que_gastan_modelo(ctx: ContextoServidor) -> None:
    from asistente.capacidades.contrato import CapacidadBase, OperacionCapacidad, ResultadoCapacidad

    def operacion(clave: str, **extra: Any) -> OperacionCapacidad:
        return OperacionCapacidad(
            clave=clave,
            descripcion=f"Operación {clave}.",
            entrada={"type": "object", "properties": {}},
            salida={"type": "object"},
            ejecutar=lambda args, c: ResultadoCapacidad(datos={}, texto_para_modelo=f"hecho {clave}"),
            datos_de_terceros=False,
            **extra,
        )

    externa = CapacidadBase(
        clave="mcp_notas",
        nombre="Notas",
        descripcion="d",
        consentimiento="ninguno",
        operaciones=(operacion("buscar"),),
    )
    propia = CapacidadBase(
        clave="utiles",
        nombre="Útiles",
        descripcion="d",
        consentimiento="ninguno",
        operaciones=(
            operacion("leer"),
            operacion("resumir", proposito="documentos"),
            operacion("enviar", efecto="actua", ambitos=frozenset({"nucleo"})),
            operacion("solo_luz", ambitos=frozenset({"luz"})),
        ),
    )
    registro = registro_capacidades.construir_registro([("mcp_notas", externa), ("utiles", propia)])
    assert registro.errores == {}
    registro_capacidades.reiniciar(registro)
    nombres = set(herramientas_expuestas(ctx))
    assert "capacidad_utiles_leer" in nombres
    assert not any("mcp_notas" in n or n.endswith(("_resumir", "_enviar", "_solo_luz")) for n in nombres)
    assert llamar(ctx, "capacidad_utiles_leer", {}).texto == "hecho leer"

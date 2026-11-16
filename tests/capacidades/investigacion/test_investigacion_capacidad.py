"""The capability under the contract, the agent tool, the task step, and privacy (export, erasure)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from asistente.capacidades.contrato import ContextoPeticion, PeticionDenegada, pedir
from asistente.capacidades.investigacion import capacidad
from asistente.capacidades.investigacion.almacen import AlmacenInvestigacion
from asistente.capacidades.investigacion.herramienta import (
    INVESTIGAR_TOOL,
    InvestigacionAgente,
    dato_privado_en,
    herramienta_investigar,
    valores_privados,
)
from asistente.capacidades.investigacion.servicio import Investigador
from asistente.capacidades.investigacion.tareas import CapacidadInvestigacionTareas
from asistente.capacidades.peticion import APERTURA, CIERRE
from asistente.capacidades.registro import problemas_de
from asistente.nucleo.tareas.capacidades import ContextoCapacidad
from tests.proactive import make_case
from .conftest import PLAN, SINTESIS_BUENA, FakeProvider

HOY = date(2026, 11, 16)


def _proveedor() -> FakeProvider:
    return FakeProvider(respuestas={"investigacion_plan": PLAN, "investigacion_sintesis": SINTESIS_BUENA})


@pytest.fixture
def configurada(investigador: Investigador, registro_propio: None) -> Investigador:
    capacidad.configurar(investigador)
    return investigador


def test_the_capability_meets_the_contract_and_has_no_write_operation() -> None:
    assert problemas_de(capacidad.CAPACIDAD, paquete="investigacion") == []
    assert [(op.clave, op.efecto, op.datos_de_terceros) for op in capacidad.CAPACIDAD.operaciones] == [
        ("investigar", "lectura", True)
    ]


def test_pedir_returns_the_report_wrapped_as_untrusted_data(configurada: Investigador) -> None:
    resultado = pedir(
        "investigacion",
        "investigar",
        {"pregunta": "¿Qué documentos necesito para el pasaporte?"},
        ContextoPeticion(ambito="nucleo", hoy=HOY, proveedor=_proveedor()),
    )
    assert resultado.no_confiable and not resultado.propuesta
    texto = resultado.texto_para_modelo
    assert texto.startswith(APERTURA) and texto.rstrip().endswith(CIERRE)
    assert "[S1]" in texto and "Fuentes:" in texto and "BOE-A-2003-13978" in texto
    assert resultado.datos["confianza"] == "alta"
    assert resultado.citas[1].startswith("S2: BOE-A-2003-13978")


def test_bad_arguments_are_refused_by_the_contract(configurada: Investigador) -> None:
    contexto = ContextoPeticion(ambito="nucleo", hoy=HOY)
    with pytest.raises(PeticionDenegada) as error:
        pedir("investigacion", "investigar", {"pregunta": "x"}, contexto)
    assert error.value.motivo == "entrada"
    with pytest.raises(PeticionDenegada):
        pedir("investigacion", "investigar", {"pregunta": "una pregunta", "fuentes": ["foro"]}, contexto)
    with pytest.raises(PeticionDenegada):
        pedir("investigacion", "escribir", {"pregunta": "una pregunta"}, contexto)


# --------------------------------------------------------------------------- #
# The agent tool
# --------------------------------------------------------------------------- #


def test_the_tool_refuses_case_values_the_user_did_not_write() -> None:
    privados = valores_privados({"rendimiento_trabajo_bruto": 35000.0, "municipio": "Getafe", "hijos": 2, "x": True})
    assert privados == ["35000.0", "Getafe"]
    assert dato_privado_en("¿Cuánto tributa un sueldo de 35.000 euros?", privados, ()) == "35000.0"
    assert dato_privado_en("¿Hay oficina de pasaportes en getafe?", privados, ()) == "Getafe"
    # The user wrote it themselves in this conversation: it is theirs to send.
    assert dato_privado_en("¿Hay oficina en Getafe?", privados, ["Vivo en Getafe, ¿dónde renuevo?"]) is None
    assert dato_privado_en("¿Qué documentos pide el pasaporte?", privados, ()) is None


def test_the_tool_runs_through_the_contract(configurada: Investigador) -> None:
    spec, ejecutar = herramienta_investigar(proveedor=_proveedor(), hoy=lambda: HOY, privados=["Getafe"])
    assert spec is INVESTIGAR_TOOL
    resultado = ejecutar({"pregunta": "¿Qué documentos necesito para el pasaporte?", "profundidad": "rapida"})
    assert not resultado.is_error and resultado.content.startswith(APERTURA)
    privado = ejecutar({"pregunta": "¿Dónde renuevo el pasaporte en Getafe?"})
    assert privado.is_error and "expediente" in privado.content
    assert ejecutar({"pregunta": "una pregunta", "fuentes": ["foro"]}).is_error


def test_the_agent_gets_the_tool_only_when_the_operator_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from asistente.plataforma import settings

    assert InvestigacionAgente.para(None, None, query="hola") is None
    monkeypatch.setattr(settings, "INVESTIGACION_AGENTE", True)
    agente = InvestigacionAgente.para(None, make_case(municipio="Getafe"), query="hola", chat_history=[])
    assert agente is not None and [s.name for s, _h in agente.herramientas()] == ["investigar"]
    assert "investigar" in agente.addendum()


# --------------------------------------------------------------------------- #
# The task step
# --------------------------------------------------------------------------- #


def test_research_as_a_task_step(configurada: Investigador) -> None:
    cap = CapacidadInvestigacionTareas(proveedor=_proveedor)
    assert {"webs_oficiales", "investigar"} <= set(cap.operaciones)
    ctx = ContextoCapacidad(case=make_case(municipio="Getafe"), hoy=HOY, modulos_activos=[], clave="t1:p1:0")
    resultado = cap.ejecutar("investigar", {"pregunta": "¿Qué documentos necesito para el pasaporte?"}, ctx)
    assert not resultado.es_error
    assert resultado.resumen.startswith("- Hace falta el DNI") and "Confianza: alta." in resultado.resumen
    assert resultado.evidencias[0].startswith("S1:")
    privada = cap.ejecutar("investigar", {"pregunta": "¿Oficinas de pasaporte en Getafe?"}, ctx)
    assert privada.es_error and "expediente" in privada.resumen
    # The inherited operation still works as before (no searcher here: it points to the official sites).
    raiz = sorted(cap.operaciones["webs_oficiales"].schema["properties"]["raices"]["items"]["enum"])[0]
    assert "No he consultado" in cap.ejecutar("webs_oficiales", {"tema": "pasaporte", "raices": [raiz]}, ctx).resumen


# --------------------------------------------------------------------------- #
# Privacy: export, erasure, deleted accounts
# --------------------------------------------------------------------------- #


def test_export_and_erasure_of_a_user(configurada: Investigador, almacen: AlmacenInvestigacion) -> None:
    almacen.registrar_consentimiento("ana", True)
    configurada.investigar(
        capacidad.PeticionInvestigacion(pregunta="¿Qué documentos necesito para el pasaporte?"),
        proveedor=_proveedor(),
        user_id="ana",
    )
    almacen.registrar_consentimiento("luis", True)
    exportado = capacidad.exportar_usuario("ana")
    assert [c["concedido"] for c in exportado["consentimientos_web"]] == [True]
    assert len(exportado["informes"]) == 1
    assert capacidad.borrar_usuario("ana") == 2
    assert capacidad.exportar_usuario("ana") == {"consentimientos_web": [], "informes": []}
    assert almacen.consentimiento_web("luis")


def test_the_rows_of_a_deleted_account_are_purged(tmp_path: Any, almacen: AlmacenInvestigacion) -> None:
    from asistente.plataforma.auth.store import AuthStore

    casos = str(tmp_path / "casos.sqlite3")
    auth = AuthStore(casos)
    ana = auth.create_user("ana@example.com", "una-clave-larga-1")
    luis = auth.create_user("luis@example.com", "una-clave-larga-2")
    for usuario in (ana.id, luis.id, None):
        almacen.registrar_consentimiento(usuario, True)
    auth.delete_user(ana.id)
    assert almacen.purgar_cuentas_borradas(casos) == 1
    assert not almacen.consentimiento_web(ana.id)
    assert almacen.consentimiento_web(luis.id) and almacen.consentimiento_web(None)
    assert almacen.purgar_cuentas_borradas(str(tmp_path / "no-existe.sqlite3")) == 0


def test_the_central_agent_offers_and_runs_the_tool_only_when_switched_on(
    configurada: Investigador, monkeypatch: pytest.MonkeyPatch
) -> None:
    from asistente.nucleo.agent.agent import TaxAgent
    from asistente.nucleo.agent.providers.base import ProviderError
    from asistente.nucleo.agent.verifier import Verifier
    from asistente.plataforma import settings
    from tests.agent.fakes import FakeSearcher, ScriptedProvider, corpus, text_turn, tool_turn

    def verificador() -> Verifier:
        return Verifier(ScriptedProvider([], [ProviderError("sin verificador")] * 3))  # type: ignore[arg-type]

    apagado = ScriptedProvider([text_turn("Hola.")])
    TaxAgent(provider=apagado, searcher=FakeSearcher(corpus()), verifier=verificador()).run(
        "¿Qué documentos pide el pasaporte?"
    )
    assert "investigar" not in [t.name for t in apagado.started["tools"]]
    assert "`investigar`" not in apagado.started["system"]

    monkeypatch.setattr(settings, "INVESTIGACION_AGENTE", True)
    encendido = ScriptedProvider(
        [tool_turn(("investigar", {"pregunta": "¿Qué documentos pide el pasaporte?"})), text_turn("Hecho.")],
        [PLAN, SINTESIS_BUENA],
    )
    TaxAgent(provider=encendido, searcher=FakeSearcher(corpus()), verifier=verificador()).run(
        "¿Qué documentos pide el pasaporte?"
    )
    assert "investigar" in [t.name for t in encendido.started["tools"]]
    assert "`investigar`" in encendido.started["system"]
    (resultado,) = encendido.tool_results
    assert not resultado.is_error and resultado.content.startswith(APERTURA)
    assert [r["schema_name"] for r in encendido.json_requests] == ["investigacion_plan", "investigacion_sintesis"]

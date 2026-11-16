"""The user's decisions are restrictions of the household planner, in both call paths (the agent's
``planificar_hogar`` and ``POST /planificar``), with the reason first in the plan's assumptions."""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_gpt.agent.evidence import EvidenceLedger
from hacienda_gpt.agent.providers.base import ToolCall
from hacienda_gpt.agent.tools import Toolbox
from hacienda_gpt.memoria.planificador import ajuste_para_planificador, aplicar_a_la_entrada, planificar_con_memoria
from hacienda_gpt.memoria.servicio import decisiones_del_caso, registrar
from hacienda_gpt.planificador import agente
from hacienda_gpt.planificador.modelos import EntradaPlan
from tests.auth.conftest import Env
from tests.planificador.test_agente import ESPECIALISTA, _case, _NoSearch
from tests.planificador.test_api import PETICION, _caso_con_hipoteca
from .conftest import HOY

PAREJA = {
    "rendimiento_trabajo_bruto": 70000.0,
    "cotizaciones_seguridad_social": 4445.0,
    "retenciones": 15000.0,
    "comunidad_autonoma": "Madrid",
    "estado_civil": "casado",
}
CONYUGE = {"rendimiento_trabajo_bruto": 4000, "cotizaciones_seguridad_social": 254}


def test_each_decision_maps_to_its_restriction(db) -> None:
    for ambito, tipo in (
        ("fiscal:plan_pensiones", "rechazo"),
        ("fiscal:donativo", "aceptacion"),
        ("hipoteca:amortizar:cuota", "restriccion"),
        ("fiscal:tributacion:conjunta", "rechazo"),
        ("fiscal:tributacion:individual", "aceptacion"),  # «ya lo hice»: files that way, excludes nothing
        ("luz:cambio_tarifa", "rechazo"),  # another module: nothing for the planner
    ):
        registrar(db, case_id="c1", ambito=ambito, tipo=tipo, razon="lo que dijo", fuente="chat")
    ajuste = ajuste_para_planificador(decisiones_del_caso(db, "c1"), hoy=HOY)
    assert set(ajuste.excluidas) == {"plan_pensiones_contribuyente", "plan_pensiones_conyuge", "donativo"}
    assert set(ajuste.modalidades) == {"cuota"} and set(ajuste.tributacion) == {"conjunta"}

    # The request's own restrictions are kept; the decisions are added with their reason and origin.
    pedida = EntradaPlan.model_validate({"restricciones": {"excluidas": ["amortizar_hipoteca"], "origen": "usuario"}})
    r = aplicar_a_la_entrada(pedida, ajuste).restricciones
    assert r.excluidas == ["amortizar_hipoteca", "plan_pensiones_contribuyente", "plan_pensiones_conyuge", "donativo"]
    assert r.modalidades_excluidas == ["cuota"] and r.tributacion_excluida == ["conjunta"]
    assert r.origen == "usuario y memoria de decisiones"
    assert "amortizar_hipoteca" not in r.motivos
    assert r.motivos["donativo"].startswith("el 16/11/2026 nos dijiste «lo que dijo»")
    assert r.motivos["donativo"].endswith("que ya lo habías hecho")

    # A whole module («hipoteca») covers the repayment.
    registrar(db, case_id="c2", ambito="hipoteca", tipo="rechazo", razon="nada de la hipoteca", fuente="chat")
    assert set(ajuste_para_planificador(decisiones_del_caso(db, "c2"), hoy=HOY).excluidas) == {"amortizar_hipoteca"}


def test_the_agent_tool_never_proposes_a_rejected_lever_even_if_the_call_asks(db, monkeypatch) -> None:
    monkeypatch.setattr(agente, "hoy_en_espana", lambda: HOY)
    monkeypatch.setattr(agente, "_modulos_activos", lambda case: {"fiscal", "hipoteca"})
    case = _case()
    registrar(
        db,
        case_id=case.case_id,
        ambito="fiscal:plan_pensiones",
        tipo="rechazo",
        razon="no quiero planes de pensiones",
        fuente="chat",
    )
    registrar(
        db,
        case_id=case.case_id,
        ambito="hipoteca:amortizar:cuota",
        tipo="restriccion",
        razon="solo plazo",
        fuente="chat",
    )
    toolbox = Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger(), case=case, extra=[ESPECIALISTA])
    argumentos = {"importe_disponible": 20000, "gastos_mensuales": 2000, "liquidez_actual": 6000}
    # A figure in the call (the model's, not the user changing their mind) does not bring it back.
    resultado = toolbox.execute(
        ToolCall(id="c1", name="planificar_hogar", arguments={**argumentos, "aportacion_maxima_pensiones": 1500})
    )
    assert not resultado.is_error, resultado.content
    supuestos = resultado.content.split("Supuestos:\n", 1)[1]
    assert supuestos.startswith(
        "- No se propone «Tu plan de pensiones»: el 16/11/2026 nos dijiste «no quiero planes de pensiones» (lo tendré "
        "en cuenta hasta el 16/11/2027)."
    )
    assert "«Amortizar la hipoteca reduciendo la cuota»" not in supuestos  # labels are the planner's own
    assert "No se propone «Amortizar reduciendo cuota»: el 16/11/2026 nos dijiste «solo plazo»" in supuestos
    assert (
        "Tu plan de pensiones (restricciones): La has descartado (memoria de decisiones): el 16/11/2026 nos dijiste "
        "«no quiero planes de pensiones»" in resultado.content
    )
    assert "- Tu plan de pensiones:" not in resultado.content.split("Posición neta", 1)[0]

    # Without decisions (another case) the same call is the planner's own.
    otro = _case().model_copy(update={"case_id": "c-otro"})
    libre = Toolbox(searcher=_NoSearch(), ledger=EvidenceLedger(), case=otro, extra=[ESPECIALISTA]).execute(
        ToolCall(id="c2", name="planificar_hogar", arguments=argumentos)
    )
    assert "memoria de decisiones" not in libre.content and "No se propone" not in libre.content


def test_a_rejected_joint_filing_is_not_used_for_the_plans_tax(db) -> None:
    entrada = {"ejercicio": 2026, "fecha": HOY, "importe_disponible": 3000, "conyuge": CONYUGE}
    antes = planificar_con_memoria(entrada, case_id="c1", facts=PAREJA, modulos_activos={"fiscal"}, hoy=HOY, db_path=db)
    assert antes.valoracion.tributacion == "conjunta_biparental"
    registrar(
        db, case_id="c1", ambito="fiscal:tributacion:conjunta", tipo="rechazo", razon="por separado", fuente="chat"
    )
    plan = planificar_con_memoria(entrada, case_id="c1", facts=PAREJA, modulos_activos={"fiscal"}, hoy=HOY, db_path=db)
    assert plan.valoracion.tributacion == "individual"
    assert plan.restricciones.tributacion_excluida == ["conjunta"]
    assert plan.plan_fiscal is not None and plan.plan_fiscal.optimo.tributacion == "individual"
    assert plan.supuestos[0].startswith(
        "No se propone «Declaración conjunta»: el 16/11/2026 nos dijiste «por separado»"
    )
    assert "No se considera la declaración conjunta: la has descartado." not in plan.supuestos


def test_post_planificar_applies_the_owners_decisions(env: Env) -> None:
    ana = env.register("ana@example.com")
    case_id = _caso_con_hipoteca(env, ana)
    creada = env.client.post(
        f"/cases/{case_id}/decisiones",
        json={"ambito": "hipoteca:amortizar", "tipo": "rechazo", "razon": "No quiero amortizar"},
        headers=ana,
    )
    assert creada.status_code == 200, creada.text
    plan = env.client.post("/planificar", json={**PETICION, "case_id": case_id}, headers=ana).json()
    assert plan["valoracion"]["asignacion"]["amortizar_hipoteca"] in ("0", "0.00")
    no_usada = next(n for n in plan["no_usadas"] if n["id"] == "amortizar_hipoteca")
    assert no_usada["causa"] == "restricciones" and "«No quiero amortizar»" in no_usada["detalle"]
    assert plan["supuestos"][0].startswith("No se propone «Amortizar hipoteca»: el ")
    assert plan["restricciones"]["origen"] == "memoria de decisiones"
    # The same request without the case knows nothing of the decisions.
    sin_caso = env.client.post("/planificar", json=PETICION, headers=ana).json()
    assert sin_caso["restricciones"]["excluidas"] == []


@pytest.mark.parametrize("dia", [date(2027, 11, 16)])
def test_an_expired_decision_lets_the_lever_back_and_says_so(db, reloj_fijo, dia) -> None:
    registrar(db, case_id="c1", ambito="fiscal:donativo", tipo="rechazo", razon="sin donativos", fuente="chat")
    reloj_fijo.dia = dia
    ajuste = ajuste_para_planificador(decisiones_del_caso(db, "c1"), hoy=dia)
    assert ajuste.excluidas == {}
    assert any("«sin donativos»" in n and "tu decisión era hasta el 16/11/2027" in n for n in ajuste.notas)

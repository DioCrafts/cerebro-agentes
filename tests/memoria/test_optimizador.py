"""A rejected or restricted lever is a constraint of the tax optimiser, said in the plan's assumptions."""

from __future__ import annotations

from datetime import date
import json

from asistente.ambitos.fiscal.calculo.optimizador import optimizar_desde_hechos
from asistente.nucleo.memoria.optimizador import optimizar_con_memoria
from asistente.nucleo.memoria.servicio import borrar, registrar
from asistente.nucleo.proactive.jobs import plan_fin_de_ejercicio
from tests.proactive import SALARIED, make_case
from .conftest import HOY, guardar


def _hechos(case) -> dict:
    return {f.name: f.value for f in case.facts}


def test_without_decisions_the_plan_is_the_optimisers_own(db) -> None:
    case = make_case("c1", **SALARIED)
    solo = optimizar_desde_hechos(ejercicio=2026, fecha=HOY, facts=_hechos(case))
    con = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert con == solo and [p.id for p in con.palancas] == ["plan_pensiones_contribuyente"]


def test_a_rejected_pension_plan_is_never_proposed_and_the_plan_says_why(db) -> None:
    case = make_case("c1", **SALARIED)
    registrar(
        db,
        case_id="c1",
        ambito="fiscal:plan_pensiones",
        tipo="rechazo",
        razon="no quiero planes de pensiones porque necesito liquidez",
        fuente="chat",
    )
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))

    assert all(not p.id.startswith("plan_pensiones") for p in plan.palancas)
    assert plan.supuestos[0] == (
        "No se propone «Aportar a tu plan de pensiones»: el 16/11/2026 nos dijiste «no quiero planes de pensiones "
        "porque necesito liquidez» (lo tendré en cuenta hasta el 16/11/2027). Si has cambiado de idea, dilo o "
        "cámbialo en «Lo que sé de tus preferencias»."
    )
    pension = next(nd for nd in plan.no_disponibles if nd.id == "plan_pensiones_contribuyente")
    assert pension.causa == "restricciones" and "necesito liquidez" in pension.detalle
    # The run's own figures cannot bring it back while the decision is in force.
    forzado = optimizar_con_memoria(
        case_id="c1",
        db_path=db,
        ejercicio=2026,
        fecha=HOY,
        facts=_hechos(case),
        restricciones={"aportacion_maxima_pensiones": 1500},
    )
    assert all(not p.id.startswith("plan_pensiones") for p in forzado.palancas)
    # Another user's plan is untouched.
    otro = optimizar_con_memoria(case_id="c2", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert [p.id for p in otro.palancas] == ["plan_pensiones_contribuyente"]


def test_a_spouse_only_rejection_leaves_the_users_own_plan(db) -> None:
    case = make_case("c1", **SALARIED)
    registrar(
        db, case_id="c1", ambito="fiscal:plan_pensiones:conyuge", tipo="restriccion", razon="mi mujer no", fuente="chat"
    )
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert [p.id for p in plan.palancas] == ["plan_pensiones_contribuyente"]


def test_no_donations_means_no_donation_and_no_invitation_to_donate(db) -> None:
    preferencias = json.dumps({"donativo_previsto": 200, "presupuesto_donativos": 600})
    case = make_case("c1", preferencias_optimizacion=preferencias, **SALARIED)
    antes = optimizar_desde_hechos(ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert any(p.id == "donativo" for p in antes.palancas)

    registrar(
        db, case_id="c1", ambito="fiscal:donativo", tipo="rechazo", razon="no me propongáis donativos", fuente="chat"
    )
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert all(p.id != "donativo" for p in plan.palancas)
    assert not any(nd.id.startswith("donativo") for nd in plan.no_disponibles)
    assert "no me propongáis donativos" in plan.supuestos[0]


def test_an_expired_rejection_lets_the_lever_back_and_says_what_changed(db, reloj_fijo) -> None:
    case = make_case("c1", **SALARIED)
    registrar(
        db,
        case_id="c1",
        ambito="fiscal:plan_pensiones",
        tipo="rechazo",
        razon="este año no",
        fuente="chat",
        caducidad=date(2026, 12, 1),
    )
    reloj_fijo.dia = date(2026, 12, 1)
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=date(2026, 12, 1), facts=_hechos(case))
    assert [p.id for p in plan.palancas] == ["plan_pensiones_contribuyente"]
    assert any("«este año no»" in s and "tu decisión era hasta el 01/12/2026" in s for s in plan.supuestos)


def test_a_filing_preference_is_mentioned_not_imposed(db) -> None:
    case = make_case("c1", **SALARIED)
    registrar(
        db,
        case_id="c1",
        ambito="fiscal:tributacion:conjunta",
        tipo="preferencia",
        razon="prefiero la conjunta",
        fuente="chat",
    )
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert [p.id for p in plan.palancas] == ["plan_pensiones_contribuyente"]
    assert any("Prefieres «Declaración conjunta»" in s and "prefiero la conjunta" in s for s in plan.supuestos)


def test_the_year_end_plan_notice_respects_the_rejection(db, casos, bandeja) -> None:
    case = guardar(casos, make_case("c1", **SALARIED))
    assert plan_fin_de_ejercicio(case, hoy=HOY, store=bandeja) == 1
    (plan,) = bandeja.list("c1")
    assert "plan de pensiones" in plan.body.lower()

    # The user rejects it: the stale plan leaves the inbox at once…
    resultado = registrar(
        db, case_id="c1", ambito="fiscal:plan_pensiones", tipo="rechazo", razon="necesito liquidez", fuente="chat"
    )
    assert resultado.plan_retirado and bandeja.list("c1") == []
    # …and the rebuilt one (same facts) proposes nothing about pensions, and says why.
    assert plan_fin_de_ejercicio(case, hoy=HOY, store=bandeja) == 1
    (nuevo,) = bandeja.list("c1")
    assert not any("plan de pensiones" in a.lower() for a in nuevo.actions)
    assert "No se propone «Aportar a tu plan de pensiones»" in nuevo.body and "necesito liquidez" in nuevo.body
    assert "**Qué puedes hacer antes de fin de año**" not in nuevo.body

    # Forgetting the decision brings the lever back in the rebuilt plan.
    assert borrar(db, case_id="c1", decision_id=resultado.decision.id).plan_retirado
    assert plan_fin_de_ejercicio(case, hoy=HOY, store=bandeja) == 1
    (otra_vez,) = bandeja.list("c1")
    assert "**Qué puedes hacer antes de fin de año**" in otra_vez.body


def test_ya_lo_hice_is_not_proposed_again_and_the_plan_asks_for_the_amount(db) -> None:
    case = make_case("c1", **SALARIED)
    registrar(
        db,
        case_id="c1",
        ambito="fiscal:plan_pensiones:contribuyente",
        tipo="aceptacion",
        razon="Ya lo hice",
        fuente="boton",
    )
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert plan.palancas == []
    assert "que ya lo habías hecho" in plan.supuestos[0] and "indica el importe en tus datos" in plan.supuestos[0]


def test_rejecting_loss_harvesting_leaves_the_losses_out(db) -> None:
    preferencias = json.dumps({"perdidas_latentes": [{"descripcion": "Fondo", "importe": 900}]})
    case = make_case("c1", ganancias_patrimoniales=3000, preferencias_optimizacion=preferencias, **SALARIED)
    antes = optimizar_desde_hechos(ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert "perdidas_contribuyente" in {p.id for p in antes.palancas}

    registrar(db, case_id="c1", ambito="fiscal:perdidas", tipo="rechazo", razon="no vendo el fondo", fuente="chat")
    plan = optimizar_con_memoria(case_id="c1", db_path=db, ejercicio=2026, fecha=HOY, facts=_hechos(case))
    assert "perdidas_contribuyente" not in {p.id for p in plan.palancas}
    assert not any(nd.id.startswith("perdidas") for nd in plan.no_disponibles)
    assert any("no vendo el fondo" in s for s in plan.supuestos)


_PAREJA = {
    "rendimiento_trabajo_bruto": 70000.0,
    "cotizaciones_seguridad_social": 4445.0,
    "comunidad_autonoma": "Madrid",
    "estado_civil": "casado",
}
_CONYUGE = {"rendimiento_trabajo_bruto": 4000, "cotizaciones_seguridad_social": 254}


def test_a_rejected_joint_filing_is_excluded_not_only_warned_about(db) -> None:
    kwargs = {"ejercicio": 2026, "fecha": HOY, "facts": _PAREJA, "conyuge": _CONYUGE}
    kwargs["restricciones"] = {"acepta_iliquidez": True}
    antes = optimizar_con_memoria(case_id="c1", db_path=db, **kwargs)
    assert antes.optimo.tributacion == "conjunta_biparental"

    registrar(
        db,
        case_id="c1",
        ambito="fiscal:tributacion:conjunta",
        tipo="rechazo",
        razon="no queremos hacer la declaración conjunta",
        fuente="chat",
    )
    plan = optimizar_con_memoria(case_id="c1", db_path=db, **kwargs)
    assert plan.optimo.tributacion == "individual" and all(p.id != "tributacion" for p in plan.palancas)
    assert any(
        s.startswith("No se propone «Declaración conjunta»") and "no queremos hacer la declaración conjunta" in s
        for s in plan.supuestos
    )
    assert "No se considera la declaración conjunta: la has descartado." not in plan.supuestos
    assert not any("todavía no puede" in a for a in plan.avisos)
    # «Ya lo hice» about a filing mode excludes nothing (it means they file that way).
    registrar(
        db, case_id="c1", ambito="fiscal:tributacion:conjunta", tipo="aceptacion", razon="Ya lo hice", fuente="boton"
    )
    assert optimizar_con_memoria(case_id="c1", db_path=db, **kwargs).optimo.tributacion == "conjunta_biparental"

"""The deterministic priority: a fixed table of features, the user's decisions applied with the memory's own rules,
signals about one matter grouped, a cap. Table-driven."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from asistente.nucleo.memoria.modelo import Condicion, Decision, huella
from asistente.nucleo.repaso.priorizacion import es_urgente, priorizar, puntuar, rasgos
from .conftest import HOY, creado
from .fabrica import EN, propuesta, senal


@pytest.mark.parametrize(
    ("kw", "puntos", "claves"),
    [
        ({"hechos": {"vence": HOY}}, 60, ["plazo"]),
        ({"hechos": {"vence": EN(days=1)}}, 55, ["plazo"]),
        ({"hechos": {"vence": EN(days=3)}}, 45, ["plazo"]),
        ({"hechos": {"vence": EN(days=7)}}, 30, ["plazo"]),
        ({"hechos": {"vence": EN(days=14)}}, 18, ["plazo"]),
        ({"hechos": {"vence": EN(days=30)}}, 8, ["plazo"]),
        ({"hechos": {"vence": EN(days=60)}}, 2, ["plazo"]),
        ({"hechos": {"vence": HOY - timedelta(days=2)}}, 40, ["plazo"]),
        ({"gravedad": "urgente"}, 25, ["gravedad"]),
        ({"gravedad": "aviso"}, 8, ["gravedad"]),
        ({"consecuencia": "Se entiende rechazada a los diez días."}, 20, ["consecuencia"]),
        ({"oficial": True}, 15, ["consecuencia"]),
        ({"normativa": True, "gravedad": "aviso"}, 12 + 8, ["normativa", "gravedad"]),
        ({"hechos": {"importe": Decimal("1500")}}, 15, ["dinero"]),
        ({"hechos": {"importe": Decimal("-300")}}, 10, ["dinero"]),
        ({"hechos": {"importe": Decimal("50")}}, 6, ["dinero"]),
        ({"hechos": {"importe": Decimal("9.99")}}, 3, ["dinero"]),
        ({"hechos": {"importe": Decimal("0")}}, 0, []),
        ({"propuesta": propuesta()}, 12, ["decision"]),
        ({"propuesta": propuesta(), "hechos": {"numeros": {"dias_esperando": Decimal(9)}}}, 17, ["decision", "espera"]),
        ({"nueva": True}, 8, ["novedad"]),
        ({"tipo": "novedad"}, -15, ["contexto"]),
        ({"tipo": "decision", "nueva": True}, -7, ["novedad", "contexto"]),
        (
            {"gravedad": "urgente", "oficial": True, "hechos": {"vence": EN(days=2), "importe": Decimal("400")}},
            45 + 15 + 25 + 10,
            ["plazo", "consecuencia", "gravedad", "dinero"],
        ),
    ],
)
def test_the_score_table(kw: dict[str, Any], puntos: int, claves: list[str]) -> None:
    s = senal(**kw)
    assert puntuar(s) == puntos
    assert [r.clave for r in rasgos(s)] == claves


@pytest.mark.parametrize(
    ("kw", "urgente"),
    [
        ({"gravedad": "urgente"}, True),
        ({"oficial": True, "hechos": {"vence": EN(days=2)}}, True),
        ({"irreversible": True, "hechos": {"vence": HOY}}, True),
        ({"oficial": True, "hechos": {"vence": EN(days=3)}}, False),
        ({"hechos": {"vence": HOY}}, False),  # a deadline nothing official or irreversible hangs on
        ({"oficial": True, "hechos": {"vence": HOY - timedelta(days=1)}}, False),
    ],
)
def test_what_is_urgent(kw: dict[str, Any], urgente: bool) -> None:
    assert es_urgente(senal(**kw)) is urgente


def test_the_reasons_are_spanish_and_come_from_the_facts() -> None:
    (punto,) = priorizar(
        [senal(oficial=True, gravedad="urgente", hechos={"vence": EN(days=2), "importe": Decimal("1234.5")})], hoy=HOY
    ).puntos
    assert punto.por_que == [
        "Vence el 20/11/2026 (en 2 días)",
        "Su módulo lo marca como urgente",
        "Es un plazo oficial",
        "Importe que indica su módulo: 1.234,50 €",
    ]
    assert punto.urgente and punto.vence == EN(days=2) and (punto.id, punto.rango) == ("p1", 1)


def test_ranking_and_ties() -> None:
    prio = priorizar(
        [
            senal("aviso:b", hechos={"vence": EN(days=20)}),
            senal("aviso:a", hechos={"vence": EN(days=21)}),
            senal("aviso:urgente", gravedad="urgente", hechos={"vence": EN(days=2)}),
            senal("aviso:sin_fecha", gravedad="aviso"),
        ],
        hoy=HOY,
    )
    # 20 days, 21 days and «aviso» without a date score the same: the earlier deadline first, no date last.
    assert [p.puntuacion for p in prio.puntos[1:]] == [8, 8, 8]
    assert [p.clave for p in prio.puntos] == ["aviso:urgente", "aviso:b", "aviso:a", "aviso:sin_fecha"]
    assert [p.id for p in prio.puntos] == ["p1", "p2", "p3", "p4"]


def _decision(ambito: str, **kw: Any) -> Decision:
    datos: dict[str, Any] = {
        "case_id": "c1",
        "ambito": ambito,
        "tipo": "rechazo",
        "razon": "No me interesa",
        "fuente": "boton",
        "fecha": creado(HOY - timedelta(days=30)),
        "caducidad": HOY + timedelta(days=300),
    }
    datos.update(kw)
    return Decision(**datos)


def test_a_rejection_in_force_holds_the_signal_back() -> None:
    rechazada = senal(
        "aviso:modulo:luz:ahorro:x", ambito_decision="luz:cambio_tarifa:x", hechos={"importe": Decimal("100")}
    )
    otra = senal("aviso:modulo:luz:factura", ambito_decision="luz:factura", gravedad="aviso")
    prio = priorizar([rechazada, otra], hoy=HOY, decisiones=[_decision("luz:cambio_tarifa")])
    assert [p.clave for p in prio.puntos] == ["aviso:modulo:luz:factura"]
    (omitido,) = prio.omitidos
    assert (omitido.senal_id, omitido.alcance, omitido.motivo) == (
        "aviso:modulo:luz:ahorro:x",
        "senal",
        "No me interesa",
    )


def test_a_rejection_of_the_proposal_only_drops_the_proposal() -> None:
    con = senal(
        "aviso:modulo:luz:ahorro:x",
        ambito_decision="luz:ahorro:x",
        propuesta=propuesta("plantilla:cambiar_tarifa_luz", "luz:cambio_tarifa"),
        hechos={"importe": Decimal("90")},
    )
    prio = priorizar([con], hoy=HOY, decisiones=[_decision("luz:cambio_tarifa")])
    (punto,) = prio.puntos
    assert punto.propuestas == [] and prio.omitidos[0].alcance == "propuesta"


@pytest.mark.parametrize(
    ("decision", "importe", "motivo"),
    [
        # Expired: comes back, saying until when it held.
        ({"caducidad": HOY - timedelta(days=1)}, Decimal("100"), "tu decisión era hasta el 17/11/2026"),
        # A much better offer than the rejected one.
        (
            {"condiciones_de_reapertura": [Condicion(tipo="mejora", importe_referencia=Decimal("100"))]},
            Decimal("250"),
            "la nueva propuesta supone 250,00 €",
        ),
        # A fact of the case changed since the decision.
        (
            {
                "condiciones_de_reapertura": [
                    Condicion(tipo="hecho", hecho="luz_tarifa", huella_al_decidir=huella("pvpc"))
                ]
            },
            Decimal("100"),
            "ha cambiado un dato de tu expediente",
        ),
    ],
)
def test_expired_or_reopened_decisions_let_it_through_and_say_why(decision, importe, motivo) -> None:
    s = senal("aviso:modulo:luz:ahorro:x", ambito_decision="luz:cambio_tarifa:x", hechos={"importe": importe})
    prio = priorizar(
        [s], hoy=HOY, decisiones=[_decision("luz:cambio_tarifa", **decision)], hechos_caso={"luz_tarifa": "fija"}
    )
    (punto,) = prio.puntos
    assert punto.reapertura is not None and motivo in punto.reapertura
    assert any(m.startswith("Te lo vuelvo a mencionar porque") for m in punto.por_que)


def test_the_notice_the_inbox_let_come_back_once_stays() -> None:
    s = senal("aviso:modulo:luz:ahorro:x", ambito_decision="luz:cambio_tarifa:x", hechos={"importe": Decimal("300")})
    decision = _decision(
        "luz:cambio_tarifa",
        condiciones_de_reapertura=[Condicion(tipo="mejora", importe_referencia=Decimal("100"))],
        reabierta_el=HOY,
        reabierta_por="mejora",
        reabierta_motivo="la nueva propuesta supone 300,00 €",
        reabierta_clave="modulo:luz:ahorro:x",
    )
    (punto,) = priorizar([s], hoy=HOY, decisiones=[decision]).puntos
    assert punto.reapertura == "la nueva propuesta supone 300,00 €"


def test_superseded_or_non_blocking_decisions_do_not_hold_anything_back() -> None:
    s = senal("aviso:modulo:luz:ahorro:x", ambito_decision="luz:cambio_tarifa:x", gravedad="aviso")
    decisiones = [
        _decision("luz:cambio_tarifa", sustituida_por="dec_" + "0" * 32),
        _decision("luz", tipo="preferencia"),
        _decision("hipoteca"),
    ]
    assert len(priorizar([s], hoy=HOY, decisiones=decisiones).puntos) == 1


def test_explicit_links_and_the_same_matter_across_modules_are_grouped() -> None:
    tarea = senal("tarea:t1", ambito="tareas", tipo="tarea", enlaces=["accion:acc_1"], propuesta=propuesta("tarea:t1"))
    accion = senal("accion:acc_1", ambito="luz", tipo="accion", propuesta=propuesta("accion:acc_1"), gravedad="aviso")
    seguro = senal(
        "aviso:caducidades:seguro",
        ambito="caducidades",
        titulo="Renovación del seguro del coche",
        hechos={"vence": EN(days=12)},
    )
    cargo = senal(
        "aviso:suscripciones:seguro",
        ambito="suscripciones",
        titulo="Cargo anual: Seguro Coche Mutua",
        hechos={"vence": EN(days=14), "importe": Decimal("310")},
    )
    mismo_modulo = senal(
        "aviso:caducidades:otro_seguro", ambito="caducidades", titulo="Seguro del coche", hechos={"vence": EN(days=13)}
    )
    lejos = senal("aviso:luz:seguro", ambito="luz", titulo="Seguro de la luz", hechos={"vence": EN(days=40)})

    prio = priorizar(
        [tarea, accion, seguro, cargo, mismo_modulo, lejos], hoy=HOY, nombres={"suscripciones": "Suscripciones"}
    )

    grupos = {p.clave: [s.id for s in p.senales] for p in prio.puntos}
    assert grupos["accion:acc_1"] == ["accion:acc_1", "tarea:t1"]
    # Same matter in two modules: one point, the other module named in the reasons and scored a bit higher.
    (seguros,) = [p for p in prio.puntos if "aviso:caducidades:seguro" in grupos[p.clave]]
    assert {s.id for s in seguros.senales} == {
        "aviso:caducidades:seguro",
        "aviso:suscripciones:seguro",
        "aviso:caducidades:otro_seguro",
    }
    assert seguros.ambitos[0] == "suscripciones" and "También lo indica caducidades" in seguros.por_que
    assert [p.id for p in seguros.propuestas] == []
    assert ["aviso:luz:seguro"] in grupos.values()
    (acc,) = [p for p in prio.puntos if p.clave == "accion:acc_1"]
    assert [p.id for p in acc.propuestas] == ["accion:acc_1", "tarea:t1"]


def test_the_cap_and_context_only() -> None:
    muchas = [senal(f"aviso:{i:02d}", hechos={"vence": EN(days=i)}) for i in range(10)]
    prio = priorizar(muchas, hoy=HOY, max_puntos=3)
    assert [p.clave for p in prio.puntos] == ["aviso:00", "aviso:01", "aviso:02"] and prio.restantes == 7
    assert prio.hay_algo
    contexto = priorizar([senal("novedad:x", tipo="novedad"), senal("decision:y", tipo="decision")], hoy=HOY)
    assert contexto.puntos == [] and not contexto.hay_algo and contexto.restantes == 0
    # Context with something due is still listed, but alone it writes no digest.
    caduca = priorizar([senal("decision:z:caduca", tipo="decision", hechos={"vence": EN(days=10)})], hoy=HOY)
    assert len(caduca.puntos) == 1 and not caduca.hay_algo
    # A stale notice with nothing due is not listed: it stays in the inbox and is counted.
    viejo = priorizar([senal("aviso:consejo"), senal("aviso:nuevo", nueva=True)], hoy=HOY)
    assert [p.clave for p in viejo.puntos] == ["aviso:nuevo"] and viejo.restantes == 1
    # The cap is bounded whatever the setting says.
    assert len(priorizar(muchas, hoy=HOY, max_puntos=0).puntos) == 1
    quince = [senal(f"aviso:{i:02d}", hechos={"vence": EN(days=i)}) for i in range(15)]
    assert len(priorizar(quince, hoy=HOY, max_puntos=99).puntos) == 12

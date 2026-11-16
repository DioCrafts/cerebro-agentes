"""The inbox's write path: a rejected proposal never comes back, except once when something changed."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from asistente.nucleo.memoria.almacen import almacen
from asistente.nucleo.memoria.servicio import desde_aviso, registrar
from asistente.nucleo.proactive.bandeja import avisos_de_modulos, clave_semana, resumen_semanal
from asistente.nucleo.proactive.jobs import recordatorios_de_plazos
from asistente.nucleo.proactive.store import Notification
from tests.modulos_nucleo.fakes import Llamadas, agua, aviso
from tests.proactive import make_case
from .conftest import HOY, guardar


def _modulo(*avisos):
    return agua(Llamadas(), avisos_fijos=list(avisos))


def _claves(bandeja, case_id: str = "c1") -> set[str]:
    return {n.key for n in bandeja.list(case_id)}


def test_a_rejected_proposal_is_never_written_and_the_decision_counts_it(db, casos, bandeja) -> None:
    case = guardar(casos, make_case("c1", agua_personas=3))
    decision = registrar(
        db, case_id="c1", ambito="agua:tarifa", tipo="rechazo", razon="no quiero cambiar de tarifa", fuente="chat"
    ).decision
    modulo = _modulo(aviso("tarifa:barata:2026-11", importe=Decimal("30")), aviso("factura:2026-11"))

    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[modulo])

    assert _claves(bandeja) == {"modulo:agua:factura:2026-11"}
    guardada = almacen(db).get("c1", decision.id)
    assert guardada.omisiones == 1 and guardada.ultima_omision == HOY
    # Every daily run tries again and is held back again (counted once a day).
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[modulo])
    assert _claves(bandeja) == {"modulo:agua:factura:2026-11"} and almacen(db).get("c1", decision.id).omisiones == 1


def test_no_me_interesa_removes_the_notice_and_it_never_comes_back(db, casos, bandeja, reloj_fijo) -> None:
    case = guardar(casos, make_case("c1", agua_personas=3))
    modulo = _modulo(aviso("tarifa:barata:2026-11", importe=Decimal("30"), titulo="Cambia a la tarifa barata"))
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[modulo])
    (nota,) = bandeja.list("c1")

    resultado = desde_aviso(
        db, case_id="c1", notification_id=nota.id, boton="no_me_interesa", razon="tengo permanencia"
    )

    assert resultado is not None and resultado.avisos_retirados == ["modulo:agua:tarifa:barata:2026-11"]
    decision = resultado.decision
    assert (decision.ambito, decision.tipo, decision.fuente, decision.boton) == (
        "agua:tarifa:barata",
        "rechazo",
        "boton",
        "no_me_interesa",
    )
    assert decision.razon == "No me interesa: tengo permanencia"
    assert decision.caducidad == date(2027, 11, 16) and decision.propuesta.importe == Decimal("30")
    assert bandeja.list("c1") == []
    for dias in (0, 1, 30, 200):
        reloj_fijo.dia = HOY + timedelta(days=dias)
        avisos_de_modulos(case, hoy=reloj_fijo.dia, store=bandeja, modulos=[modulo])
        assert bandeja.list("c1") == []


def test_the_weekly_digest_never_lists_a_rejected_proposal(db, casos, bandeja) -> None:
    case = guardar(casos, make_case("c1", agua_personas=3))
    modulo = _modulo(
        aviso("tarifa:barata", titulo="Tarifa barata del agua", vence=HOY + timedelta(days=5)),
        aviso("factura:2026-11", titulo="Factura del agua de noviembre", vence=HOY + timedelta(days=9)),
    )
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[modulo])
    assert resumen_semanal(case, hoy=HOY, store=bandeja) == 1
    digest = next(n for n in bandeja.list("c1") if n.key == clave_semana(HOY))
    assert "Tarifa barata del agua" in digest.body

    # The user says no in the chat: the notice AND this week's digest that listed it go at once…
    resultado = registrar(db, case_id="c1", ambito="agua:tarifa", tipo="rechazo", razon="no me interesa", fuente="chat")
    assert resultado.resumen_retirado and resultado.avisos_retirados == ["modulo:agua:tarifa:barata"]
    # …and the rebuilt digest (and every later one) only has what is still open.
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[modulo])
    assert resumen_semanal(case, hoy=HOY, store=bandeja) == 1
    digest = next(n for n in bandeja.list("c1") if n.key == clave_semana(HOY))
    assert "Tarifa barata" not in digest.body and "Factura del agua de noviembre" in digest.body
    siguiente = HOY + timedelta(days=7)
    avisos_de_modulos(case, hoy=siguiente, store=bandeja, modulos=[modulo])
    resumen_semanal(case, hoy=siguiente, store=bandeja)
    assert all("Tarifa barata" not in n.body for n in bandeja.list("c1"))


def test_the_digest_itself_is_never_held_back(db, casos, bandeja) -> None:
    case = guardar(casos, make_case("c1"))
    registrar(db, case_id="c1", ambito="fiscal", tipo="rechazo", razon="no me hables de impuestos", fuente="chat")
    bandeja.add(
        Notification(case_id="c1", kind="modulo:agua", key="modulo:agua:x", title="Agua", body="", severity="info")
    )
    assert resumen_semanal(case, hoy=HOY, store=bandeja) == 1


def _luz(bandeja, oferta: str, importe: str) -> bool:
    """A notice exactly as the electricity module writes it (kind, key and saving)."""
    return bandeja.add(
        Notification(
            case_id="c1",
            kind="modulo:luz",
            key=f"modulo:luz:luz:ahorro:{oferta}:2026-10-31",
            title=f"Con «{oferta}» pagarías unos {importe} € menos al año",
            body="Compruébalo antes de cambiar.",
            severity="aviso",
            amount=Decimal(importe),
        )
    )


def test_a_much_better_offer_comes_back_once_saying_what_the_user_decided(db, casos, bandeja) -> None:
    guardar(casos, make_case("c1"))
    assert _luz(bandeja, "oferta-a", "30")
    (nota,) = bandeja.list("c1")
    decision = desde_aviso(
        db, case_id="c1", notification_id=nota.id, boton="no_me_interesa", razon="permanencia"
    ).decision
    # «No me interesa» on a tariff offer answers the lever: no tariff change, whichever the offer.
    assert decision.ambito == "luz:cambio_tarifa" and decision.condiciones_de_reapertura[0].tipo == "mejora"

    # Another offer, a bit better (not more than twice): held back.
    assert not _luz(bandeja, "oferta-b", "60")
    # Much better: it comes back once, saying what the user decided, when, and what changed.
    assert _luz(bandeja, "oferta-c", "75")
    (vuelta,) = bandeja.list("c1")
    assert vuelta.body.startswith("**Te lo vuelvo a proponer.** El 16/11/2026 me dijiste que no te interesaba")
    assert "«No me interesa: permanencia»" in vuelta.body and "75,00 €" in vuelta.body and "30,00 €" in vuelta.body
    guardada = almacen(db).get("c1", decision.id)
    assert (guardada.reabierta_el, guardada.reabierta_por, guardada.reabierta_clave) == (
        HOY,
        "mejora",
        "modulo:luz:luz:ahorro:oferta-c:2026-10-31",
    )
    # Only once: an even better one later is held back again, and so is an ordinary one.
    assert not _luz(bandeja, "oferta-d", "500")
    assert not _luz(bandeja, "oferta-e", "31")
    assert _claves(bandeja) == {"modulo:luz:luz:ahorro:oferta-c:2026-10-31"}


def test_remind_me_later_hides_it_until_that_day_then_it_comes_back_once(db, casos, bandeja, reloj_fijo) -> None:
    case = guardar(casos, make_case("c1"))
    modulo = _modulo(aviso("lectura:contador", titulo="Lee el contador"))
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[modulo])
    (nota,) = bandeja.list("c1")
    hasta = HOY + timedelta(days=10)
    decision = desde_aviso(db, case_id="c1", notification_id=nota.id, boton="recuerdamelo", hasta=hasta).decision
    assert decision.caducidad == hasta and decision.tipo == "rechazo"

    reloj_fijo.dia = hasta - timedelta(days=1)
    avisos_de_modulos(case, hoy=reloj_fijo.dia, store=bandeja, modulos=[modulo])
    assert bandeja.list("c1") == []

    reloj_fijo.dia = hasta
    avisos_de_modulos(case, hoy=hasta, store=bandeja, modulos=[modulo])
    (vuelta,) = bandeja.list("c1")
    assert "me pediste que te lo recordara más tarde" in vuelta.body
    assert almacen(db).get("c1", decision.id).reabierta_por == "caducidad"


def test_after_expiry_the_proposal_comes_back_once_and_then_flows_normally(db, casos, bandeja, reloj_fijo) -> None:
    case = guardar(casos, make_case("c1"))
    registrar(
        db,
        case_id="c1",
        ambito="agua:tarifa",
        tipo="rechazo",
        razon="ahora no",
        fuente="chat",
        caducidad=date(2027, 1, 1),
    )
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[_modulo(aviso("tarifa:a"))])
    assert bandeja.list("c1") == []

    reloj_fijo.dia = date(2027, 1, 1)
    avisos_de_modulos(case, hoy=reloj_fijo.dia, store=bandeja, modulos=[_modulo(aviso("tarifa:a"))])
    (vuelta,) = bandeja.list("c1")
    assert "Te lo vuelvo a proponer" in vuelta.body and "tu decisión era hasta el 01/01/2027" in vuelta.body
    avisos_de_modulos(case, hoy=reloj_fijo.dia, store=bandeja, modulos=[_modulo(aviso("tarifa:b"))])
    nueva = next(n for n in bandeja.list("c1") if n.key == "modulo:agua:tarifa:b")
    assert "Te lo vuelvo a proponer" not in nueva.body


def test_a_fact_change_reopens_it(db, casos, bandeja) -> None:
    case = guardar(casos, make_case("c1", agua_personas=2))
    registrar(
        db,
        case_id="c1",
        ambito="agua:tarifa",
        tipo="rechazo",
        razon="somos pocos en casa",
        fuente="chat",
        condiciones=[{"tipo": "hecho", "hecho": "agua_personas", "descripcion": "si cambia cuántos sois en casa"}],
        hechos={"agua_personas": 2.0},
    )
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[_modulo(aviso("tarifa:a"))])
    assert bandeja.list("c1") == []
    case = guardar(casos, make_case("c1", agua_personas=5))
    avisos_de_modulos(case, hoy=HOY, store=bandeja, modulos=[_modulo(aviso("tarifa:a"))])
    (vuelta,) = bandeja.list("c1")
    assert "ha cambiado un dato de tu expediente: agua personas" in vuelta.body


def test_ya_lo_hice_holds_back_a_tax_deadline_reminder(db, casos, bandeja) -> None:
    case = guardar(casos, make_case("c1", alta_actividad_economica=True))
    hoy = date(2027, 1, 10)
    assert recordatorios_de_plazos(case, hoy=hoy, store=bandeja) == 2
    iva = next(n for n in bandeja.list("c1") if "iva" in n.key)
    desde_aviso(db, case_id="c1", notification_id=iva.id, boton="ya_lo_hice")
    assert recordatorios_de_plazos(case, hoy=hoy, store=bandeja) == 0
    assert _claves(bandeja) == {"plazo:irpf_pago_fraccionado:2026:01"}


def test_one_users_decisions_never_touch_another_users_inbox(db, casos, bandeja) -> None:
    mio = guardar(casos, make_case("c1"))
    otro = guardar(casos, make_case("c2"))
    registrar(db, case_id="c1", ambito="agua", tipo="rechazo", razon="no quiero avisos del agua", fuente="chat")
    modulo = _modulo(aviso("factura:2026-11"))
    avisos_de_modulos(mio, hoy=HOY, store=bandeja, modulos=[modulo])
    avisos_de_modulos(otro, hoy=HOY, store=bandeja, modulos=[modulo])
    assert bandeja.list("c1") == [] and _claves(bandeja, "c2") == {"modulo:agua:factura:2026-11"}
    # Answering somebody else's notice is not possible either.
    (ajena,) = bandeja.list("c2")
    assert desde_aviso(db, case_id="c1", notification_id=ajena.id, boton="no_me_interesa") is None


def test_without_decisions_nothing_changes_and_no_table_is_created(tmp_path) -> None:
    import sqlite3

    from asistente.nucleo.proactive.store import NotificationStore

    path = str(tmp_path / "solo-avisos.db")
    store = NotificationStore(path)
    assert store.add(Notification(case_id="c", kind="plazo", key="plazo:x", title="t", body="b", severity="info"))
    tablas = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "decisiones" not in tablas

"""No regression: the events produce the very notices the daily run produces, and the daily run (the
safety net) finds nothing left to add. The tax module also reacts to new tax law and schedules itself."""

from __future__ import annotations

from datetime import date, timedelta

from hacienda_gpt.decision.schemas import CaseStatus
from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.eventos import emitir_novedades, productores
from hacienda_gpt.eventos.manejadores import DIAS_AVISO_PLAZO, proxima_revision_fiscal
from hacienda_gpt.eventos.tipos import CAMBIO_NORMATIVO
from hacienda_gpt.proactive.impacto import CambioNormativo
from hacienda_gpt.proactive.jobs import ejecutar_para_todos, facts_de
from hacienda_gpt.proactive.novedades import Novedad, notificar_novedad
from hacienda_gpt.proactive.store import NotificationStore
from hacienda_gpt.tax.calendario import proximos_vencimientos
from tests.proactive import SALARIED, make_case
from .ayudas import FISCAL, HOY, Llamadas, agua, aviso, crear_bus, instalar


def _caso():
    return make_case("c1", **SALARIED, agua_personas=3)


def _vista(store: NotificationStore, case_id: str) -> dict[str, tuple]:
    return {n.key: (n.kind, n.title, n.body, n.severity, n.amount, n.due_date) for n in store.list(case_id)}


def test_events_give_the_same_notices_as_the_daily_run_and_the_daily_run_adds_none(tmp_path) -> None:
    m_agua = agua(Llamadas(), avisos_fijos=[aviso("factura", vence=HOY + timedelta(days=9))])
    instalar(m_agua)

    def modulos_de(_case):
        return [FISCAL, m_agua]

    # Before: the daily run alone (no bus in the process).
    antes_db = str(tmp_path / "antes.sqlite3")
    antes_casos, antes = SQLiteCaseStateStore(antes_db), NotificationStore(antes_db)
    antes_casos.save_case(_caso())
    resumen_antes = ejecutar_para_todos(antes_casos, antes, hoy=HOY, modulos_de=modulos_de)
    assert resumen_antes["fin_de_ejercicio"] == 1 and resumen_antes["modulos"] == {"agua": 1}

    # Now: the bus reacts to the case being saved.
    productores.instalar()
    db = str(tmp_path / "eventos.sqlite3")
    casos, notas = SQLiteCaseStateStore(db), NotificationStore(db)
    casos.save_case(_caso())
    crear_bus(db, modulos=[FISCAL, m_agua]).drenar()
    assert _vista(notas, "c1") == _vista(antes, "c1")

    # The safety net runs afterwards and has nothing to add.
    resumen = ejecutar_para_todos(casos, notas, hoy=HOY, modulos_de=modulos_de)
    assert resumen == {**resumen_antes, "plazo": 0, "fin_de_ejercicio": 0, "modulos": {"agua": 0}}
    assert _vista(notas, "c1") == _vista(antes, "c1")


def test_the_daily_run_reports_exactly_what_it_did_before_with_the_bus_installed(tmp_path) -> None:
    resumenes = []
    for instalado in (False, True):
        if instalado:
            productores.instalar()
        db = str(tmp_path / f"{instalado}.sqlite3")
        casos, notas = SQLiteCaseStateStore(db), NotificationStore(db)
        casos.save_case(_caso())
        resumenes.append(ejecutar_para_todos(casos, notas, hoy=HOY))
    assert resumenes[0] == resumenes[1]


def test_the_tax_module_schedules_its_next_wake_up_from_the_calendar(tmp_path) -> None:
    caso = _caso()
    proxima = proxima_revision_fiscal(caso, hoy=HOY)
    assert proxima is not None and proxima > HOY
    vencimientos = proximos_vencimientos(facts_de(caso), hoy=HOY, dias=400)
    assert proxima + timedelta(days=DIAS_AVISO_PLAZO) in {v.fecha for v in vencimientos}
    # The earliest deadline still outside the window; the generic «year-end» entry is never reminded alone.
    entradas = [
        v.fecha - timedelta(days=DIAS_AVISO_PLAZO)
        for v in vencimientos
        if not v.id.startswith("irpf_cierre_ejercicio") and v.fecha - timedelta(days=DIAS_AVISO_PLAZO) > HOY
    ]
    assert proxima == min(entradas)
    # Before October, the year-end plan's first day is a wake-up too.
    assert proxima_revision_fiscal(caso, hoy=date(2026, 9, 1)) <= date(2026, 10, 1)

    productores.instalar()
    db = str(tmp_path / "e.sqlite3")
    SQLiteCaseStateStore(db).save_case(caso)
    bus = crear_bus(db, modulos=[FISCAL])
    bus.drenar()
    assert bus.temporizadores() == [("c1", "fiscal", proxima)]
    with bus._tx() as conn:
        conn.execute("DELETE FROM eventos_temporizadores")
    assert bus.reconciliar(HOY) == 1 and bus.temporizadores() == [("c1", "fiscal", proxima)]


def _novedad() -> Novedad:
    return Novedad(
        boe_id="BOE-A-2026-99999",
        fecha=date(2026, 11, 14),
        titulo="Ley 9/2026, de medidas tributarias",
        rango="Ley",
        ambito="estatal",
        tributos=["IRPF"],
        relevancia="alta",
        analizada=True,
        url="https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-99999",
    )


def test_a_new_tax_norm_reaches_every_open_case_once(tmp_path) -> None:
    db = str(tmp_path / "e.sqlite3")
    casos, notas = SQLiteCaseStateStore(db), NotificationStore(db)
    for case_id in ("c1", "c2"):
        casos.save_case(make_case(case_id, **SALARIED))
    casos.save_case(make_case("c3", **SALARIED).model_copy(update={"status": CaseStatus.CLOSED}))
    # boe-novedades notified c1 inline already; the event must not repeat it.
    notificar_novedad(_novedad(), [casos.get_case("c1")], notas)
    assert emitir_novedades(db, [_novedad()]) == 1
    crear_bus(db, modulos=[FISCAL]).drenar()
    for case_id in ("c1", "c2"):
        assert [n.key for n in notas.list(case_id)] == ["novedad:BOE-A-2026-99999"]
    assert notas.list("c3") == []


def test_an_analysed_law_change_reaches_only_the_profiles_it_affects(tmp_path) -> None:
    db = str(tmp_path / "e.sqlite3")
    casos, notas = SQLiteCaseStateStore(db), NotificationStore(db)
    casos.save_case(make_case("asalariado", **SALARIED))
    casos.save_case(make_case("sin_datos"))
    cambio = CambioNormativo(
        boe_id="BOE-A-2006-20764",
        titulo_norma="Ley 35/2006, del IRPF",
        actualizado="20261114",
        resumen="Cambia la reducción por rendimientos del trabajo.",
        en_vigor_desde="2027-01-01",
        afecta_a=["asalariados"],
        relevancia="alta",
        cita_literal="texto literal de prueba",
    )
    bus = crear_bus(db, modulos=[FISCAL])
    bus.emitir(CAMBIO_NORMATIVO, payload={"cambio": cambio.model_dump(mode="json")})
    bus.drenar()
    assert [n.key for n in notas.list("asalariado")] == ["cambio_normativo:BOE-A-2006-20764:20261114"]
    assert notas.list("sin_datos") == []

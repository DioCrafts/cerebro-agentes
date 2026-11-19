"""Notices to the paired chat: the ones that matter, once, after pairing, unread, respecting /pausa, the quiet hours
and their age; each chat only its own case's."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from asistente.nucleo.proactive.store import KIND_RESUMEN, Notification, NotificationStore
from asistente.plataforma import settings
from asistente.plataforma.mensajeria.avisos import en_silencio, merece_chat, texto_aviso
from tests.cerebro.fakes import caso
from .conftest import CASE_ID, Montaje

MADRID = ZoneInfo("Europe/Madrid")


def _aviso(case_id: str = CASE_ID, **campos: object) -> Notification:
    datos: dict[str, object] = {
        "case_id": case_id,
        "kind": "plazo",
        "key": f"k-{datetime.now(UTC).timestamp()}-{id(campos)}",
        "title": "Plazo de la renta",
        "body": "**Te quedan 3 días** para presentar la declaración.\n\nMás detalles en la app.",
        "severity": "urgente",
    }
    datos.update(campos)
    return Notification(**datos)  # type: ignore[arg-type]


def _guardar(montaje: Montaje, *avisos: Notification) -> list[Notification]:
    store = NotificationStore(montaje.db)
    try:
        for aviso in avisos:
            assert store.add(aviso)
    finally:
        store.close()
    return list(avisos)


def test_which_notices_are_worth_a_chat_message() -> None:
    assert merece_chat(_aviso(severity="urgente"))
    assert merece_chat(_aviso(kind=KIND_RESUMEN, severity="info"))
    assert merece_chat(_aviso(kind="modulo:consejo", severity="info", title="Dictamen del consejo listo"))
    assert not merece_chat(_aviso(severity="aviso"))
    assert not merece_chat(_aviso(kind="modulo:luz", severity="info"))
    assert texto_aviso(_aviso(), "simplex") == (
        "*Aviso: Plazo de la renta*\nTe quedan 3 días para presentar la declaración.\n"
        "Ábrelo en la app («Avisos») o pregúntame por ello aquí."
    )


def test_quiet_hours_across_midnight_and_within_a_day() -> None:
    def a_las(hora: int, minuto: int = 0) -> datetime:
        return datetime(2026, 11, 19, hora, minuto, tzinfo=MADRID)

    assert en_silencio(a_las(23), "22:00-08:00") and en_silencio(a_las(7, 59), "22:00-08:00")
    assert not en_silencio(a_las(8), "22:00-08:00") and not en_silencio(a_las(21, 59), "22:00-08:00")
    assert en_silencio(a_las(14), "14:00-16:00") and not en_silencio(a_las(16), "14:00-16:00")
    for franja in ("", "nunca", "25:00-08:00", "08:00-08:00"):
        assert not en_silencio(a_las(23), franja)


def test_new_notices_that_matter_are_delivered_once(montaje: Montaje) -> None:
    montaje.emparejar()
    urgente, repaso, dictamen, normal = _guardar(
        montaje,
        _aviso(key="urgente"),
        _aviso(key="repaso", kind=KIND_RESUMEN, severity="info", title="Tu repaso de la semana", body="3 puntos."),
        _aviso(key="dictamen", kind="modulo:consejo", severity="info", title="Dictamen del consejo listo", body="x"),
        _aviso(key="normal", kind="modulo:luz", severity="aviso", title="Factura de la luz"),
    )
    antes = len(montaje.canal.de("c1"))

    resumen = montaje.pasarela.repartir_avisos()

    assert resumen["entregados"] == 3
    textos = [e.texto for e in montaje.nuevos("c1", antes)]
    assert [t.split("\n")[0] for t in textos] == [
        "*Aviso: Plazo de la renta*",
        "*Aviso: Tu repaso de la semana*",
        "*Aviso: Dictamen del consejo listo*",
    ]
    assert montaje.almacen.entregas_de(montaje.almacen.vinculo_de("prueba", "c1").id) == {  # type: ignore[union-attr]
        urgente.id,
        repaso.id,
        dictamen.id,
    }
    # Again: nothing new is sent.
    assert montaje.pasarela.repartir_avisos()["entregados"] == 0
    assert len(montaje.canal.de("c1")) == antes + 3
    assert not any(normal.title in e.texto for e in montaje.canal.de("c1"))


def test_old_read_and_pre_pairing_notices_are_not_sent(montaje: Montaje, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MENSAJERIA_AVISOS_MAX_HORAS", 24)
    _guardar(montaje, _aviso(key="antes", created_at=datetime.now(UTC) - timedelta(minutes=5)))
    montaje.emparejar()
    leido, viejo = _guardar(montaje, _aviso(key="leido"), _aviso(key="viejo"))
    store = NotificationStore(montaje.db)
    store.mark_read(CASE_ID, leido.id)
    store.close()
    vinculo = montaje.almacen.vinculo_de("prueba", "c1")
    assert vinculo is not None
    # «viejo» was created long after pairing in the poll's eyes: the pass runs two days later.
    from asistente.plataforma.mensajeria import avisos

    resumen = avisos.repartir(montaje.pasarela.entorno, montaje.pasarela.canales, datetime.now(UTC) + timedelta(days=2))
    assert resumen["entregados"] == 0 and resumen["omitidos"] == 1
    assert montaje.almacen.entregas_de(vinculo.id) == {viejo.id}
    assert len(montaje.canal.de("c1")) == 1  # only the welcome


def test_paused_notices_are_skipped_not_replayed(montaje: Montaje) -> None:
    montaje.emparejar()
    montaje.decir("/pausa")
    _guardar(montaje, _aviso(key="en-pausa"))
    antes = len(montaje.canal.de("c1"))
    assert montaje.pasarela.repartir_avisos()["omitidos"] == 1
    montaje.decir("/reanudar")
    assert montaje.pasarela.repartir_avisos()["entregados"] == 0
    assert len(montaje.canal.de("c1")) == antes + 1  # only the /reanudar answer
    _guardar(montaje, _aviso(key="despues"))
    assert montaje.pasarela.repartir_avisos()["entregados"] == 1


def test_quiet_hours_hold_notices_until_they_end(montaje: Montaje, monkeypatch: pytest.MonkeyPatch) -> None:
    montaje.emparejar()
    _guardar(montaje, _aviso(key="noche"))
    ahora = datetime.now(MADRID)
    franja = f"{(ahora - timedelta(hours=1)):%H:%M}-{(ahora + timedelta(hours=1)):%H:%M}"
    monkeypatch.setattr(settings, "MENSAJERIA_HORAS_SILENCIO", franja)
    assert montaje.pasarela.repartir_avisos() == {"entregados": 0, "omitidos": 0, "retenidos": 1, "fallidos": 0}
    monkeypatch.setattr(settings, "MENSAJERIA_HORAS_SILENCIO", "")
    assert montaje.pasarela.repartir_avisos()["entregados"] == 1


def test_a_failed_send_is_retried_a_few_times_then_given_up(montaje: Montaje) -> None:
    montaje.emparejar()
    _guardar(montaje, _aviso(key="falla"))
    montaje.canal.falla_envio = True
    for _ in range(3):
        assert montaje.pasarela.repartir_avisos()["fallidos"] == 1
    assert montaje.pasarela.repartir_avisos()["fallidos"] == 0  # given up
    montaje.canal.falla_envio = False
    assert montaje.pasarela.repartir_avisos()["entregados"] == 0


def test_each_chat_gets_only_its_own_cases_notices(montaje: Montaje, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MENSAJERIA_CONTACTOS_POR_USUARIO", 2)
    otro = caso().model_copy(update={"case_id": "case-otro", "user_id": "u-otro"})
    montaje.casos.save_case(otro)
    montaje.emparejar("c1")
    montaje.emparejar("c2", case_id="case-otro")
    _guardar(montaje, _aviso(key="mio", title="Aviso de c1"), _aviso("case-otro", key="suyo", title="Aviso de c2"))
    montaje.pasarela.repartir_avisos()
    assert [t for t in montaje.canal.textos("c1") if "Aviso de" in t][0].startswith("*Aviso: Aviso de c1*")
    assert [t for t in montaje.canal.textos("c2") if "Aviso de" in t][0].startswith("*Aviso: Aviso de c2*")
    assert len([t for t in montaje.canal.textos("c1") if "Aviso de" in t]) == 1

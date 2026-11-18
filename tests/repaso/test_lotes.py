"""The job with the provider's batch API: one batch for every case due, collected on later runs, each result
gated and applied once, usage recorded at batch prices; synchronous fallback when anything goes wrong."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.nucleo.proactive.bandeja import resumen_semanal
from asistente.nucleo.repaso import servicio
from asistente.nucleo.repaso.almacen import RepasoStore, custom_id_de
from asistente.plataforma import settings
from tests.proactive import make_case
from .conftest import Guionizado, Lotes, aviso, valida

MADRID = ZoneInfo("Europe/Madrid")
LUNES_8 = datetime(2026, 11, 16, 8, 0, tzinfo=MADRID)


@pytest.fixture
def dos_casos(db, store):
    SQLiteCaseStateStore(db).save_case(make_case("c2"))
    aviso(store, "modulo:luz:factura", title="Factura de la luz", due_date=date(2026, 11, 30))
    aviso(store, "plazo:130", kind="plazo", case_id="c2", title="Modelo 130", due_date=date(2026, 12, 20))
    return db


def _digests(store, case_id: str) -> list:
    return [n for n in store.list(case_id) if n.kind == "resumen_semanal"]


def test_one_batch_collected_later_and_applied_once(dos_casos, store, monkeypatch: pytest.MonkeyPatch) -> None:
    db = dos_casos
    lotes = Lotes(terminado=False)

    enviado = servicio.repasar_todos(db, ahora=LUNES_8, proveedor=lotes)

    assert enviado["lote"] == "lote_1" and enviado["en_lote"] == 2 and enviado["casos"] == 2
    (peticiones,) = lotes.lotes.values()
    assert {c for c, _p in peticiones} == {custom_id_de("c1", "2026-W47"), custom_id_de("c2", "2026-W47")}
    assert all("c1" not in c and "c2" not in c for c, _p in peticiones)  # no case id travels in the batch
    assert _digests(store, "c1") == [] and lotes.peticiones == []
    assert RepasoStore(db).get("c1", "2026-W47").estado == "pendiente_lote"  # type: ignore[union-attr]

    # While it is pending, the weekly digest of the inbox waits for it (no deterministic digest in between).
    monkeypatch.setattr(settings, "REPASO_ACTIVO", True)
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: lotes)
    assert resumen_semanal(make_case("c1"), hoy=LUNES_8.date(), store=store) == 0 and _digests(store, "c1") == []

    # A later run: still processing.
    assert servicio.repasar_todos(db, ahora=LUNES_8 + timedelta(minutes=30), proveedor=lotes)["lotes"]["en_curso"] == 2

    # Finished: each result gated and written, usage recorded at batch prices for its case.
    lotes.terminado = True
    hecho = servicio.repasar_todos(db, ahora=LUNES_8 + timedelta(hours=1), proveedor=lotes)
    assert hecho["lotes"]["aplicados"] == 2 and hecho["ya_hechos"] == 2
    for case_id in ("c1", "c2"):
        (digest,) = _digests(store, case_id)
        pasada = RepasoStore(db).get(case_id, "2026-W47")
        assert pasada is not None and pasada.estado == "aplicada" and pasada.origen == "modelo"
        assert pasada.notification_id == digest.id and "Esta semana hay cosas" in digest.body
    assert [(u.purpose, u.batch) for u in lotes.usos] == [("cerebro:repaso", True)] * 2
    assert lotes.peticiones == []  # no synchronous call

    # Applied once: the next runs find nothing to collect and nothing to write.
    otra = servicio.repasar_todos(db, ahora=LUNES_8 + timedelta(hours=2), proveedor=lotes)
    assert otra["lotes"] == {"en_curso": 0, "aplicados": 0, "sincronos": 0, "errores": 0} and otra["ya_hechos"] == 2
    assert len(_digests(store, "c1")) == len(_digests(store, "c2")) == 1 and len(lotes.usos) == 2


def test_an_errored_result_falls_back_to_the_synchronous_call(dos_casos, store) -> None:
    lotes = Lotes(valida)
    servicio.repasar_todos(dos_casos, ahora=LUNES_8, proveedor=lotes)
    lotes.errores = {custom_id_de("c2", "2026-W47")}
    hecho = servicio.repasar_todos(dos_casos, ahora=LUNES_8 + timedelta(hours=1), proveedor=lotes)
    assert hecho["lotes"]["aplicados"] == 1 and hecho["lotes"]["sincronos"] == 1
    assert len(lotes.peticiones) == 1 and len(_digests(store, "c2")) == 1


def test_a_batch_running_too_long_falls_back_to_the_synchronous_call(dos_casos, store) -> None:
    lotes = Lotes(valida, valida, terminado=False)
    servicio.repasar_todos(dos_casos, ahora=LUNES_8, proveedor=lotes)
    tarde = servicio.repasar_todos(
        dos_casos, ahora=LUNES_8 + servicio.MAX_ESPERA_LOTE + timedelta(minutes=1), proveedor=lotes
    )
    assert tarde["lotes"]["sincronos"] == 2 and len(lotes.peticiones) == 2
    assert len(_digests(store, "c1")) == len(_digests(store, "c2")) == 1
    # A late result is ignored: the rows are no longer waiting.
    lotes.terminado = True
    assert servicio.recoger_lotes(dos_casos, proveedor=lotes)["aplicados"] == 0


def test_a_failed_submission_and_a_provider_without_batches_write_synchronously(dos_casos, store) -> None:
    lotes = Lotes(valida, valida)
    lotes.falla_crear = RuntimeError("API caída")
    resumen = servicio.repasar_todos(dos_casos, ahora=LUNES_8, proveedor=lotes)
    assert resumen["lote"] is None and resumen["escritos"] == 2 and len(lotes.peticiones) == 2

    otro = Guionizado(valida, valida)
    resumen = servicio.repasar_todos(dos_casos, ahora=LUNES_8 + timedelta(days=7), proveedor=otro)
    assert resumen["escritos"] == 2 and "lote" not in resumen


def test_without_a_model_the_job_writes_the_deterministic_rendering(dos_casos, store) -> None:
    resumen = servicio.repasar_todos(dos_casos, ahora=LUNES_8, proveedor=None)
    assert resumen["escritos"] == 2 and not resumen["modelo"]
    assert RepasoStore(dos_casos).get("c2", "2026-W47").origen == "determinista"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("frecuencia", "dia", "momento", "toca"),
    [
        ("semanal", 0, datetime(2026, 11, 16, 6, 59, tzinfo=MADRID), False),
        ("semanal", 0, datetime(2026, 11, 16, 7, 0, tzinfo=MADRID), True),
        ("semanal", 0, datetime(2026, 11, 17, 5, 0, tzinfo=MADRID), True),  # missed on Monday: still this week
        ("semanal", 2, datetime(2026, 11, 16, 9, 0, tzinfo=MADRID), False),
        ("diaria", 0, datetime(2026, 11, 18, 6, 0, tzinfo=MADRID), False),
        ("diaria", 0, datetime(2026, 11, 18, 7, 30, tzinfo=MADRID), True),
    ],
)
def test_when_the_pass_is_due(monkeypatch: pytest.MonkeyPatch, frecuencia, dia, momento, toca) -> None:
    monkeypatch.setattr(settings, "REPASO_FRECUENCIA", frecuencia)
    monkeypatch.setattr(settings, "REPASO_DIA", dia)
    assert servicio.toca(momento) is toca


def test_not_due_only_collects(dos_casos, store) -> None:
    resumen = servicio.repasar_todos(dos_casos, ahora=datetime(2026, 11, 16, 6, 0, tzinfo=MADRID), proveedor=None)
    assert not resumen["toca"] and resumen["casos"] == 0 and _digests(store, "c1") == []

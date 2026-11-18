"""The weekly digest's hook: the brain pass only when REPASO is on and a model is configured; otherwise, or on
any failure, the deterministic digest exactly as before."""

from __future__ import annotations

from datetime import date

import pytest

from asistente.nucleo.proactive.bandeja import resumen_semanal
from asistente.nucleo.repaso import servicio
from asistente.plataforma import settings
from tests.proactive import make_case
from .conftest import LUNES, Guionizado, aviso, valida

CASE = make_case("c1")


def _digest(store):
    (digest,) = [n for n in store.list("c1") if n.kind == "resumen_semanal"]
    return digest


@pytest.fixture
def abierto(store):
    aviso(store, "plazo:130", kind="plazo", title="Modelo 130", severity="urgente", due_date=date(2026, 11, 20))
    return store


def _determinista(digest) -> bool:
    return digest.title == "Resumen semanal: 1 aviso abierto" and digest.body.startswith(
        "Lo que tienes abierto a 16/11/2026 (semana del 16/11/2026)"
    )


def test_with_repaso_off_the_digest_is_the_deterministic_one(abierto, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: pytest.fail("no model while off"))
    assert resumen_semanal(CASE, hoy=LUNES, store=abierto) == 1
    assert _determinista(_digest(abierto))


def test_without_a_model_the_digest_is_the_deterministic_one(abierto, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REPASO_ACTIVO", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert servicio.proveedor_configurado() is None
    assert resumen_semanal(CASE, hoy=LUNES, store=abierto) == 1
    assert _determinista(_digest(abierto))


def test_with_repaso_on_and_a_model_the_brain_writes_it(abierto, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REPASO_ACTIVO", True)
    modelo = Guionizado(valida)
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: modelo)
    assert resumen_semanal(CASE, hoy=LUNES, store=abierto) == 1
    digest = _digest(abierto)
    assert digest.key == "resumen_semanal:2026-W47" and "Por qué:" in digest.body and not _determinista(digest)
    assert digest.severity == "urgente"
    # Once per week, through the hook too.
    assert resumen_semanal(CASE, hoy=LUNES, store=abierto) == 0 and len(modelo.peticiones) == 1


def test_any_failure_of_the_brain_falls_back_to_the_deterministic_digest(
    abierto, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REPASO_ACTIVO", True)
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: Guionizado(valida))

    def roto(*_a, **_k):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(servicio, "repasar", roto)
    assert resumen_semanal(CASE, hoy=LUNES, store=abierto) == 1
    assert _determinista(_digest(abierto))


def test_nothing_open_writes_nothing_either_way(store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REPASO_ACTIVO", True)
    monkeypatch.setattr(servicio, "proveedor_configurado", lambda: Guionizado())
    assert resumen_semanal(CASE, hoy=LUNES, store=store) == 0
    assert [n for n in store.list("c1") if n.kind == "resumen_semanal"] == []

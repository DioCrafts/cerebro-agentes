"""Seats: sharp mandates, configuration, family diversity without network, effort variation with one family."""

from __future__ import annotations

from typing import Any

import pytest

from asistente.nucleo.consejo import miembros
from asistente.nucleo.consejo.contrato import MAX_MIEMBROS, MIN_MIEMBROS, Rol
from asistente.nucleo.consejo.miembros import (
    PRESIDENTE,
    ROL_LARGO_PLAZO,
    ROLES_CONOCIDOS,
    ROLES_POR_DEFECTO,
    FabricaProveedores,
    asignar_asientos,
    familia_disponible,
    familias_configuradas,
    miembro_de,
    roles_configurados,
)
from asistente.plataforma import settings


def test_default_seats_have_distinct_purposes_and_mandates() -> None:
    assert [r.id for r in ROLES_POR_DEFECTO] == ["prudente", "fiscal", "liquidez_familia", "abogado_del_diablo"]
    assert MIN_MIEMBROS <= len(ROLES_POR_DEFECTO) <= MAX_MIEMBROS
    for rol in (*ROLES_CONOCIDOS.values(), PRESIDENTE):
        assert rol.proposito.startswith("consejo:")
    assert all(r.proposito == f"consejo:miembro:{r.id}" for r in ROLES_CONOCIDOS.values())
    assert PRESIDENTE.proposito == "consejo:presidente"
    assert len({r.mandato for r in ROLES_CONOCIDOS.values()}) == len(ROLES_CONOCIDOS)
    # The tax seat never goes beyond the dossier's sources; the devil's advocate argues against the leading option.
    assert "fuentes del expediente" in miembros.ROL_FISCAL.mandato
    assert "en su contra" in miembros.ROL_ABOGADO.mandato.lower()
    assert miembros.ROLES_CONTRARIOS == {"abogado_del_diablo"}


def test_routing_rows_exist_for_the_purposes() -> None:
    from asistente.nucleo.agent.enrutado import nivel_de_tabla

    assert nivel_de_tabla("consejo:miembro:prudente") == "mediano"
    assert nivel_de_tabla("consejo:presidente") == "grande"


def test_configured_seats(caplog: pytest.LogCaptureFixture) -> None:
    assert roles_configurados("prudente, fiscal ,largo_plazo, fiscal") == (
        miembros.ROL_PRUDENTE,
        miembros.ROL_FISCAL,
        ROL_LARGO_PLAZO,
    )
    assert roles_configurados("prudente,desconocido,fiscal,liquidez_familia")[-1].id == "liquidez_familia"
    assert "desconocido" in caplog.text
    assert roles_configurados("prudente") == ROLES_POR_DEFECTO  # fewer than MIN → defaults
    assert roles_configurados("") == ROLES_POR_DEFECTO
    assert roles_configurados() == ROLES_POR_DEFECTO


def test_family_availability_reads_keys_not_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "LOCAL_LLM_MODEL", "")
    assert familia_disponible("anthropic")  # the configured provider always is
    assert familia_disponible("Claude")
    assert not familia_disponible("openai")
    assert not familia_disponible("local")
    assert not familia_disponible("otra")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(settings, "LOCAL_LLM_MODEL", "qwen3:14b")
    assert familia_disponible("openai") and familia_disponible("local")


def test_missing_families_are_skipped_never_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    disponibles = {"anthropic", "openai"}
    assert familias_configuradas("anthropic, openai, local", disponible=disponibles.__contains__) == [
        "anthropic",
        "openai",
    ]
    assert familias_configuradas("local,mistral", disponible=disponibles.__contains__) == ["anthropic"]
    assert familias_configuradas("", disponible=disponibles.__contains__) == ["anthropic"]
    assert familias_configuradas(["openai", "openai"], disponible=disponibles.__contains__) == ["openai"]


def test_seats_round_robin_over_families_and_preferences_win() -> None:
    asientos = asignar_asientos(ROLES_POR_DEFECTO, ["anthropic", "openai"])
    assert [a.familia for a in asientos] == ["anthropic", "openai", "anthropic", "openai"]
    assert all(a.esfuerzo is None for a in asientos)  # several families: no effort games
    prefiere = Rol(
        id="local_primero",
        nombre="Prueba de preferencia",
        mandato="Un asiento de prueba que prefiere una familia concreta.",
        proposito="consejo:miembro:local_primero",
        familia_preferida="openai",
    )
    asientos = asignar_asientos([prefiere, *ROLES_POR_DEFECTO[:2]], ["anthropic", "openai"])
    assert [a.familia for a in asientos] == ["openai", "anthropic", "openai"]
    no_disponible = prefiere.model_copy(update={"familia_preferida": "local"})
    assert asignar_asientos([no_disponible], ["anthropic"])[0].familia == "anthropic"
    with pytest.raises(ValueError):
        asignar_asientos(ROLES_POR_DEFECTO, [])


def test_one_family_alternates_a_deeper_effort_never_a_lower_one() -> None:
    asientos = asignar_asientos(ROLES_POR_DEFECTO, ["anthropic"], esfuerzo_base="high")
    assert [a.esfuerzo for a in asientos] == [None, "xhigh", None, "xhigh"]
    assert [a.esfuerzo for a in asignar_asientos(ROLES_POR_DEFECTO, ["anthropic"], esfuerzo_base="xhigh")] == [
        None,
        "max",
        None,
        "max",
    ]
    assert all(a.esfuerzo is None for a in asignar_asientos(ROLES_POR_DEFECTO, ["anthropic"], esfuerzo_base="max"))
    assert all(a.esfuerzo is None for a in asignar_asientos(ROLES_POR_DEFECTO, ["openai"]))


class _Base:
    def __init__(self, familia: str, esfuerzo: str | None) -> None:
        self.name = familia
        self.model = f"{familia}-modelo"
        self.effort = esfuerzo


def test_provider_factory_builds_lazily_per_family_and_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    creados: list[tuple[str, str | None]] = []

    def crear(familia: str, esfuerzo: str | None) -> Any:
        creados.append((familia, esfuerzo))
        return _Base(familia, esfuerzo)

    fabrica = FabricaProveedores(
        ROLES_POR_DEFECTO, familias="anthropic,openai", disponible=lambda f: True, crear=crear, medir=False
    )
    assert creados == []  # nothing at construction (no network, no SDK client)
    nombres = [fabrica(r.proposito).name for r in ROLES_POR_DEFECTO]
    assert nombres == ["anthropic", "openai", "anthropic", "openai"]
    assert fabrica(PRESIDENTE.proposito).name == "anthropic"  # the configured provider chairs
    assert creados == [("anthropic", None), ("openai", None)]  # cached per (family, effort)
    assert fabrica(ROLES_POR_DEFECTO[0].proposito) is fabrica(ROLES_POR_DEFECTO[0].proposito)
    assert fabrica.asiento("consejo:miembro:fiscal") is not None
    assert fabrica.asiento("otra-cosa") is None
    assert fabrica("otra-cosa").name == "anthropic"
    miembro = miembro_de(miembros.ROL_FISCAL, fabrica("consejo:miembro:fiscal"))
    assert (miembro.proveedor, miembro.modelo) == ("openai", "openai-modelo")


def test_provider_factory_meters_each_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    medidos: list[str] = []

    def medir(proveedor: Any, proposito: str) -> Any:
        medidos.append(proposito)
        return proveedor

    monkeypatch.setattr(miembros, "_medir", medir)
    fabrica = FabricaProveedores(
        ROLES_POR_DEFECTO, familias="anthropic", disponible=lambda f: True, crear=lambda f, e: _Base(f, e)
    )
    fabrica("consejo:miembro:prudente")
    fabrica("consejo:presidente")
    fabrica("consejo:presidente")
    assert medidos == ["consejo:miembro:prudente", "consejo:presidente"]

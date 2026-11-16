"""Erasing the account erases what the browser agent kept for each of its cases, at once (no Chromium
needed: the redacted diagnoses are plain files)."""

from __future__ import annotations

import pytest

from hacienda_gpt import settings
from hacienda_gpt.navegador import vigilancia
from tests.auth import conftest as cuentas
from tests.auth.conftest import PASSWORD, Env

# The accounts API fixtures (AUTH_MODE=required over a private database file).
env = cuentas.env
fast_scrypt = cuentas.fast_scrypt


def test_delete_me_erases_the_browser_diagnoses_of_every_case(
    env: Env, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NAVEGADOR_DIR", str(tmp_path / "navegador"))
    ana, luis = env.register("ana@example.com"), env.register("luis@example.com")
    casos_ana = [env.new_case(ana), env.new_case(ana)]
    caso_luis = env.new_case(luis)
    for case_id in (*casos_ana, caso_luis):
        assert vigilancia.guardar_diagnostico(case_id, "tarea_1", {"paso": "suministro"}) is not None
        assert len(vigilancia.diagnosticos_de(case_id)) == 1

    assert env.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana).status_code == 204
    # Gone with the account, without waiting for the housekeeping of a later browser call.
    assert all(vigilancia.diagnosticos_de(case_id) == [] for case_id in casos_ana)
    assert not any((tmp_path / "navegador" / "diagnostico" / case_id).exists() for case_id in casos_ana)
    # Someone else's are untouched.
    assert len(vigilancia.diagnosticos_de(caso_luis)) == 1


def test_a_wrong_password_erases_nothing(env: Env, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "NAVEGADOR_DIR", str(tmp_path / "navegador"))
    ana = env.register("ana@example.com")
    case_id = env.new_case(ana)
    vigilancia.guardar_diagnostico(case_id, "tarea_1", {"paso": "suministro"})
    assert env.client.request("DELETE", "/me", json={"password": "otra-clave-larga"}, headers=ana).status_code == 403
    assert len(vigilancia.diagnosticos_de(case_id)) == 1

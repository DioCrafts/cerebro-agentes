"""With accounts on, over the real API: the research data is exported and erased with the account."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from asistente.capacidades.investigacion import capacidad
from asistente.plataforma import settings
from tests.auth.conftest import PASSWORD, Env, env, fast_scrypt  # noqa: F401


@pytest.fixture
def api(env: Env, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:  # noqa: F811
    monkeypatch.setattr(settings, "INVESTIGACION_DB_PATH", str(tmp_path / "investigacion.sqlite3"))
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", env.db_path)
    capacidad.configurar(None)
    yield env
    capacidad.configurar(None)


def test_the_research_data_goes_with_the_account(api: Env) -> None:
    ana = api.register("ana@example.com")
    luis = api.register("luis@example.com")
    assert api.client.get("/investigacion/estado").status_code == 401  # a session is required
    for cabeceras in (ana, luis):
        respuesta = api.client.post("/investigacion/consentimiento", json={"concedido": True}, headers=cabeceras)
        assert respuesta.status_code == 200 and respuesta.json()["web_consentida"] is True

    exportado = api.client.get("/me/export", headers=ana).json()
    assert [c["concedido"] for c in exportado["investigacion"]["consentimientos_web"]] == [True]

    borrado = api.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana)
    assert borrado.status_code == 204, borrado.text
    almacen = capacidad.almacen_por_defecto()
    assert almacen is not None
    assert almacen.historial_consentimientos(ana["_user_id"]) == []
    assert almacen.consentimiento_web(luis["_user_id"])

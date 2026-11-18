"""The passes are the user's data: in the account export (only theirs) and gone with the account."""

from __future__ import annotations

from datetime import date

import pytest

from asistente.nucleo.repaso import servicio
from asistente.nucleo.repaso.almacen import RepasoStore
from tests.auth.conftest import CONSENTS, PASSWORD, Env, env, fast_scrypt  # noqa: F401
from .conftest import HOY, aviso


def _pasada(api: Env, headers: dict, titulo: str) -> str:
    case_id = api.new_case(headers)
    aviso(api.notifications, "plazo:130", kind="plazo", case_id=case_id, title=titulo, due_date=date(2026, 11, 20))
    case = api.cases.get_case(case_id)
    resultado = servicio.repasar(
        case, hoy=HOY, db_path=api.db_path, store=api.notifications, proveedor=None, modulos_activos=None
    )
    assert resultado.estado == "escrito"
    return case_id


@pytest.fixture
def cuentas(request: pytest.FixtureRequest) -> Env:
    """The accounts API (``tests.auth``'s ``env``: AUTH_MODE=required over a private database)."""
    api: Env = request.getfixturevalue("env")
    return api


def test_the_export_has_the_users_passes_and_nobody_elses(cuentas: Env) -> None:
    ana = cuentas.register("ana@example.com", consents=CONSENTS)
    luis = cuentas.register("luis@example.com")
    case_id = _pasada(cuentas, ana, "Modelo 130 de Ana")
    otro = _pasada(cuentas, luis, "Modelo 130 de Luis")

    respuesta = cuentas.client.get("/me/export", headers=ana)

    assert respuesta.status_code == 200, respuesta.text
    [exportado] = respuesta.json()["cases"]
    [pasada] = exportado["repaso"]
    assert pasada["case_id"] == case_id and pasada["periodo"] == "2026-W47" and pasada["estado"] == "aplicada"
    assert pasada["prioridad"]["puntos"][0]["titulo"] == "Modelo 130 de Ana"
    assert pasada["notification_id"] in {n["id"] for n in exportado["notifications"]}
    assert otro not in respuesta.text and "Modelo 130 de Luis" not in respuesta.text


def test_the_passes_go_with_the_account(cuentas: Env) -> None:
    ana = cuentas.register("ana@example.com")
    case_id = _pasada(cuentas, ana, "Modelo 130")
    assert RepasoStore(cuentas.db_path).de_caso(case_id)
    borrado = cuentas.client.request("DELETE", "/me", json={"password": PASSWORD}, headers=ana)
    assert borrado.status_code in (200, 204), borrado.text
    assert RepasoStore(cuentas.db_path).de_caso(case_id) == []

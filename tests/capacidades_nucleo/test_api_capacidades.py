"""``GET /capacidades`` and ``PUT /me/capacidades/{clave}``; ``GET /modulos`` marks the capabilities' fronts."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient
import pytest

from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma import settings
from asistente.plataforma.api.api import app, get_case_store
from asistente.plataforma.api.routers.auth import get_login_limiter
from asistente.plataforma.auth.deps import get_auth_store
from asistente.plataforma.auth.ratelimit import LoginRateLimiter
from asistente.plataforma.auth.store import AuthStore

CONSENTS = {"tratamiento_datos": True, "envio_documentos_a_proveedor_llm": False, "avisos_por_correo": False}


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    monkeypatch.setattr(settings, "MODULOS_ACTIVOS_POR_DEFECTO", "")
    monkeypatch.delenv("HACIENDA_API_KEY", raising=False)
    db_path = str(tmp_path / "state.sqlite3")
    auth, cases = AuthStore(db_path), SQLiteCaseStateStore(db_path)
    overrides = {
        get_auth_store: lambda: auth,
        get_case_store: lambda: cases,
        get_login_limiter: lambda: LoginRateLimiter(50, 900),
    }
    app.dependency_overrides.update(overrides)
    try:
        yield TestClient(app)
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)


def _registrar(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/register", json={"email": "a@example.org", "password": "una-clave-larga-1", "consents": CONSENTS}
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_capabilities_are_listed_with_consent_state(client: TestClient) -> None:
    assert client.get("/capacidades").status_code == 401
    headers = _registrar(client)
    body = client.get("/capacidades", headers=headers).json()
    assert body["configurable"] is True
    caps = {c["clave"]: c for c in body["capacidades"]}
    assert {"correo", "navegador", "documentos"} <= set(caps)
    assert caps["correo"]["requiere_consentimiento"] and not caps["correo"]["activa"]
    assert caps["correo"]["modulo"] == "correo" and caps["correo"]["usada_por"] == ["luz", "suscripciones"]
    assert caps["documentos"]["activa"] and not caps["documentos"]["requiere_consentimiento"]
    assert {"caducidades", "fiscal"} <= set(caps["documentos"]["usada_por"])
    leer = next(o for o in caps["documentos"]["operaciones"] if o["clave"] == "leer_campos")
    assert leer["proposito"] == "documentos" and leer["efecto"] == "lectura" and leer["datos_de_terceros"]
    # One switch: switching the mail module on is the consent to the mail capability.
    assert client.put("/me/modulos/correo", json={"activo": True}, headers=headers).status_code == 200
    caps = {c["clave"]: c for c in client.get("/capacidades", headers=headers).json()["capacidades"]}
    assert caps["correo"]["activa"] and caps["correo"]["elegida"]
    # Its switch stays in «Módulos»; a capability that needs no consent has no switch.
    assert client.put("/me/capacidades/correo", json={"activa": False}, headers=headers).status_code == 409
    assert client.put("/me/capacidades/documentos", json={"activa": False}, headers=headers).status_code == 409
    assert client.put("/me/capacidades/nada", json={"activa": True}, headers=headers).status_code == 404


def test_modules_list_marks_the_capability_fronts(client: TestClient) -> None:
    headers = _registrar(client)
    modulos = {m["clave"]: m for m in client.get("/modulos", headers=headers).json()["modulos"]}
    assert modulos["correo"]["capacidad"] == "correo" and modulos["navegador"]["capacidad"] == "navegador"
    assert modulos["luz"]["capacidad"] is None and modulos["fiscal"]["obligatorio"]


def test_the_modules_panel_reads_capabilities_with_the_shape_the_api_returns() -> None:
    """«Módulos» shows the capabilities apart (frontend/src/components/CapacidadesSeccion.tsx): the TypeScript
    shapes it reads are the API's (every field of ``CapacidadInfo`` / ``OperacionInfo``, nothing invented)."""
    from pathlib import Path
    import re

    from asistente.plataforma.api.routers.capacidades import CapacidadesResponse, CapacidadInfo, OperacionInfo

    raiz = Path(__file__).resolve().parents[2] / "frontend" / "src"
    fuente = (raiz / "apiModulos.ts").read_text(encoding="utf-8")

    def campos(interfaz: str) -> set[str]:
        cuerpo = re.search(rf"export interface {interfaz} \{{(.*?)\n\}}", fuente, re.S)
        assert cuerpo is not None, interfaz
        return set(re.findall(r"^\s+(\w+)\??:", cuerpo.group(1), re.M))

    assert campos("CapacidadInfo") == set(CapacidadInfo.model_fields)
    assert campos("OperacionCapacidadInfo") == set(OperacionInfo.model_fields)
    assert campos("CapacidadesResponse") == set(CapacidadesResponse.model_fields)
    seccion = (raiz / "components" / "CapacidadesSeccion.tsx").read_text(encoding="utf-8")
    assert "getCapacidades" in seccion and "setCapacidad" in seccion
    assert "<CapacidadesSeccion" in (raiz / "components" / "ModulosPanel.tsx").read_text(encoding="utf-8")

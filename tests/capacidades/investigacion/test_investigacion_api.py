"""The research endpoints (router mounted on a bare app: the coordinator wires it into api.py)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from hacienda_gpt import settings
from hacienda_gpt.api.routers import investigacion as router_mod
from hacienda_gpt.auth.deps import CaseAccess, get_case_access
from hacienda_gpt.capacidades.investigacion import capacidad
from hacienda_gpt.capacidades.investigacion.servicio import Investigador
from hacienda_gpt.capacidades.investigacion.web import DocumentoWeb
from .conftest import PLAN, SINTESIS_BUENA, TEXTO_WEB_OFICIAL, FakeProvider, FakeWeb


class _Propiedad:
    """An auth store that knows which cases belong to whom."""

    def __init__(self, casos: dict[str, str]) -> None:
        self.casos = casos

    def owns_case(self, case_id: str, user_id: str) -> bool:
        return self.casos.get(case_id) == user_id


@pytest.fixture
def cliente(investigador: Investigador, registro_propio: None) -> Iterator[tuple[TestClient, dict[str, str]]]:
    capacidad.configurar(investigador)
    proveedor = FakeProvider(respuestas={"investigacion_plan": PLAN, "investigacion_sintesis": SINTESIS_BUENA})
    usuario = {"id": "ana"}
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[router_mod.get_proveedor] = lambda: proveedor
    app.dependency_overrides[get_case_access] = lambda: CaseAccess(
        user=SimpleNamespace(id=usuario["id"]),  # type: ignore[arg-type]
        store=_Propiedad({"caso-ana": "ana"}),  # type: ignore[arg-type]
    )
    yield TestClient(app), usuario


def test_status_consent_research_export_and_erasure(cliente: tuple[TestClient, dict[str, str]]) -> None:
    http, _ = cliente
    estado = http.get("/investigacion/estado").json()
    assert estado["web_activada_por_el_servicio"] is False and estado["web_consentida"] is False
    assert estado["fuentes"] == {"corpus": True, "boe": True, "web": False}
    assert "expediente" in estado["privacidad"] and "boe.es" in estado["dominios_permitidos"]

    assert http.post("/investigacion/consentimiento", json={"concedido": True}).json()["web_consentida"] is True

    informe = http.post(
        "/investigacion", json={"pregunta": "¿Qué documentos necesito para el pasaporte?", "case_id": "caso-ana"}
    ).json()
    assert informe["confianza"] == "alta" and [f["id"] for f in informe["fuentes"]] == ["S1", "S2"]
    assert informe["afirmaciones"][0]["citas"][0]["fuente"] == "S1"
    assert informe["coste"]["llamadas_modelo"] == 2
    otra_vez = http.post("/investigacion", json={"pregunta": "¿Qué documentos necesito para el pasaporte?"}).json()
    assert otra_vez["desde_cache"] is True

    exportado = http.get("/investigacion/exportar").json()
    assert len(exportado["informes"]) == 1 and exportado["consentimientos_web"][0]["concedido"] is True
    assert http.delete("/investigacion/datos").json() == {"borrados": 2}
    assert http.get("/investigacion/exportar").json() == {"consentimientos_web": [], "informes": []}


def test_another_users_case_and_bad_requests(cliente: tuple[TestClient, dict[str, str]]) -> None:
    http, usuario = cliente
    respuesta = http.post("/investigacion", json={"pregunta": "una pregunta larga", "case_id": "caso-luis"})
    assert respuesta.status_code == 404
    assert http.post("/investigacion", json={"pregunta": "x"}).status_code == 422
    assert http.post("/investigacion", json={"pregunta": "una pregunta", "otra": 1}).status_code == 422
    # Consent is per user.
    http.post("/investigacion/consentimiento", json={"concedido": True})
    usuario["id"] = "luis"
    assert http.get("/investigacion/estado").json()["web_consentida"] is False


def test_the_web_through_the_endpoint(
    cliente: tuple[TestClient, dict[str, str]], investigador: Investigador, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _ = cliente
    monkeypatch.setattr(settings, "INVESTIGACION_WEB", True)
    web = FakeWeb(documentos=[DocumentoWeb("https://www.exteriores.gob.es/p", "Pasaporte", TEXTO_WEB_OFICIAL)])
    investigador.web = lambda _p: web
    cuerpo = {"pregunta": "¿Cuánto cuesta renovar el pasaporte?", "fuentes": ["web"]}
    sin_permiso = http.post("/investigacion", json=cuerpo).json()
    assert sin_permiso["web"]["usada"] is False and web.peticiones == []
    http.post("/investigacion/consentimiento", json={"concedido": True})
    con_permiso = http.post("/investigacion", json=cuerpo).json()
    assert con_permiso["web"]["usada"] is True and con_permiso["fuentes"][0]["dominio"] == "exteriores.gob.es"
    assert http.get("/investigacion/estado").json()["fuentes"]["web"] is True


def test_an_invalid_setting_is_a_503(
    cliente: tuple[TestClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _ = cliente
    monkeypatch.setattr(settings, "INVESTIGACION_WEB_BLOQUEADOS", "https://mal.es/ruta")
    assert http.get("/investigacion/estado").status_code == 503
    assert http.post("/investigacion", json={"pregunta": "una pregunta larga"}).status_code == 503


def test_the_store_unavailable_is_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capacidad, "almacen_por_defecto", lambda: None)
    with pytest.raises(HTTPException) as error:
        router_mod.get_almacen()
    assert error.value.status_code == 503

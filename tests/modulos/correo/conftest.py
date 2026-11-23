"""Every test of the mail module: a scratch database, the operator settings set, no network at all."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from asistente.capacidades.correo import http, oauth
from asistente.nucleo.agent import especialistas
from asistente.plataforma import settings
from asistente.plataforma.api.routers import correo as router_correo
from tests.modulos.correo.corpus import HOY
from tests.modulos.correo.falso import GoogleFalso

CLAVE = Fernet.generate_key().decode()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "modulos" / "correo"


@pytest.fixture(autouse=True)
def _aislado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("CORREO_GOOGLE_CLIENT_ID", "cliente-123.apps.googleusercontent.com")
    monkeypatch.setenv("CORREO_GOOGLE_CLIENT_SECRET", "secreto-del-cliente")
    monkeypatch.setenv("CORREO_REDIRECT_URI", "http://127.0.0.1:8046/correo/oauth/callback")
    monkeypatch.setenv("CORREO_CLAVE_CIFRADO", CLAVE)
    monkeypatch.setenv("CORREO_URL_RETORNO", "http://127.0.0.1:5173/")
    for var in (
        "CORREO_CLASIFICADOR_LLM",
        "CORREO_VENTANA_DIAS",
        "CORREO_DOMINIOS_OFICIALES_EXTRA",
        "CORREO_GMAIL_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    # The corpus is dated; "today" is pinned to it so the mail window does not drift with the calendar.
    for modulo in (especialistas, router_correo):
        monkeypatch.setattr(modulo, "hoy_en_espana", lambda: HOY.date())

    def sin_red(*args: object, **kwargs: object) -> None:
        raise AssertionError("los tests no deben acceder a la red")

    http.configurar_transporte(sin_red)
    oauth.olvidar_cache()
    yield
    http.configurar_transporte(None)
    oauth.olvidar_cache()


@pytest.fixture
def google() -> Iterator[GoogleFalso]:
    falso = GoogleFalso()
    http.configurar_transporte(falso)
    yield falso


@pytest.fixture
def db_path() -> str:
    return str(settings.DECISION_STATE_DB_PATH)

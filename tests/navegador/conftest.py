"""Fixtures of the browser agent tests: local mock sites, a real Chromium (headless), isolated paths."""

from __future__ import annotations

from collections.abc import Iterator
import glob
import json
import os
from pathlib import Path
from typing import Any

import pytest

from hacienda_gpt import settings
from tests.navegador.simulacion import USUARIO_CLAVE, USUARIO_DATADIS, Sitios, arrancar

_CANDIDATOS = (
    "/opt/pw-browsers/chromium-*/chrome-linux64/chrome",
    os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"),
)
_chromium: list[tuple[str | None, bool]] = []


def _buscar_chromium() -> tuple[str | None, bool]:
    """(executable, usable): a Chromium that Playwright can drive here (None = Playwright's own)."""
    if _chromium:
        return _chromium[0]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _chromium.append((None, False))
        return _chromium[0]
    candidatos: list[str | None] = [os.environ.get("NAVEGADOR_CHROMIUM") or None]
    for patron in _CANDIDATOS:
        candidatos.extend(sorted(glob.glob(patron), reverse=True))
    resultado: tuple[str | None, bool] = (None, False)
    for candidato in candidatos:
        try:
            with sync_playwright() as pw:
                pw.chromium.launch(headless=True, executable_path=candidato).close()
        except Exception:
            continue
        resultado = (candidato, True)
        break
    _chromium.append(resultado)
    return resultado


@pytest.fixture(scope="session")
def chromium() -> str | None:
    """Path of the Chromium executable (None = Playwright's own). Skips when no browser can start."""
    ruta, usable = _buscar_chromium()
    if not usable:
        pytest.skip("No hay un Chromium que Playwright pueda abrir en esta máquina")
    return ruta


@pytest.fixture
def sitios() -> Iterator[Sitios]:
    s = arrancar()
    try:
        yield s
    finally:
        s.parar()


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sitios: Sitios) -> Sitios:
    """Browser agent switched on, scripts pointed at the mocks, every path under tmp_path."""
    monkeypatch.setattr(settings, "NAVEGADOR_ACTIVO", True)
    monkeypatch.setattr(settings, "NAVEGADOR_ORIGENES_SUSTITUTOS", json.dumps(sitios.sustitutos()))
    monkeypatch.setattr(settings, "NAVEGADOR_DIR", str(tmp_path / "navegador"))
    monkeypatch.setattr(settings, "FORMAT_DIR", str(tmp_path / "formatos"))
    monkeypatch.setattr(settings, "FORMAT_STATE_PATH", str(tmp_path / "formatos_state.json"))
    monkeypatch.setattr(settings, "FORMAT_WATCH", True)
    return sitios


def login_datadis(pagina: Any) -> None:
    """The USER typing their own Datadis credentials in the window (the agent never does this)."""
    pagina.fill("#nif", USUARIO_DATADIS[0])
    pagina.fill("#password", USUARIO_DATADIS[1])
    pagina.click("#entrar")


def login_clave(pagina: Any) -> None:
    """The USER identifying with the Cl@ve-like gateway."""
    pagina.wait_for_selector("#dni")
    pagina.fill("#dni", USUARIO_CLAVE[0])
    pagina.fill("#clave-pin", USUARIO_CLAVE[1])
    pagina.click("#acceder")


def sesion_de_usuario(chromium: str | None, sitios: Sitios) -> dict[str, Any]:
    """A session the user opened in THEIR OWN browser and hands over for one run (storage_state)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True, executable_path=chromium)
        contexto = navegador.new_context()
        pagina = contexto.new_page()
        pagina.goto(sitios.origenes["datadis"] + "/login")
        login_datadis(pagina)
        pagina.wait_for_selector("#usuario-conectado")
        estado: dict[str, Any] = contexto.storage_state()
        navegador.close()
    # Plus a cookie of another site, which must be dropped before it reaches the agent's browser.
    estado["cookies"].append(
        {
            "name": "ajena",
            "value": "x",
            "domain": "otro.example",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        }
    )
    return estado

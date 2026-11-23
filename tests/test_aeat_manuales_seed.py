from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import httpx
import pytest

from asistente.plataforma import http
from asistente.plataforma.cli import aeat_manuales_seed as seed

PREFIJO = seed.MANUALES["irpf-2025"].prefijo
SEDE = seed.SEDE

INDICE = f"""<html><body>
<a href="{PREFIJO}c01/intro.html">Intro</a>
<a href="{PREFIJO}c01/intro.html#p2">Intro otra vez</a>
<a href="{SEDE}{PREFIJO}c02.html?x=1">Capítulo 2</a>
<a href="{PREFIJO}manual.pdf">PDF gigante</a>
<a href="/Sede/iva.html">Fuera del manual</a>
<a href="https://otra.gob.es{PREFIJO}c03.html">Otro dominio</a>
<a href="{PREFIJO}NoIx/oculta.html">Bloqueada</a>
</body></html>"""


def test_enlaces_del_manual_filtra_prefijo_pdf_dominio_y_duplicados():
    enlaces = seed.enlaces_del_manual(INDICE, SEDE + "/Sede/Ayuda/25Manual/100.html", PREFIJO)
    assert enlaces == [
        f"{SEDE}{PREFIJO}c01/intro.html",
        f"{SEDE}{PREFIJO}c02.html",
        f"{SEDE}{PREFIJO}NoIx/oculta.html",
    ]


def test_ruta_local_aplana_la_ruta_bajo_el_prefijo(tmp_path: Path):
    manual = seed.MANUALES["irpf-2025"]
    destino = seed.ruta_local(tmp_path, manual, f"{SEDE}{PREFIJO}c01/borrador/intro.html")
    assert destino == tmp_path / "aeat-manuales" / "irpf-2025" / "c01__borrador__intro.html"


ROBOTS = "User-agent: *\nDisallow: /Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025/NoIx/"


class _Sesion:
    """The pages of a fake AEAT site behind the shared HTTP client (MockTransport: no network)."""

    def __init__(self, paginas: dict[str, str]) -> None:
        self.paginas = paginas
        self.pedidas: list[str] = []
        self.cliente = http.Cliente(transport=httpx.MockTransport(self._responder), dormir=lambda _s: None)

    def _responder(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.pedidas.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text=ROBOTS)
        if url in self.paginas:
            return httpx.Response(200, text=self.paginas[url], headers={"Content-Type": "text/html; charset=utf-8"})
        return httpx.Response(404, text="no")


@pytest.fixture
def sesion() -> _Sesion:
    return _Sesion(
        {
            SEDE + "/Sede/Ayuda/25Manual/100.html": INDICE,
            f"{SEDE}{PREFIJO}c01/intro.html": f'<p>Intro</p><a href="{PREFIJO}c01/hija.html">hija</a>',
            f"{SEDE}{PREFIJO}c02.html": "<p>Capítulo 2</p>",
            f"{SEDE}{PREFIJO}c01/hija.html": "<p>Hija</p>",
        }
    )


def test_descargar_manual_respeta_robots_no_guarda_indice_y_escribe_sidecar(tmp_path: Path, sesion: _Sesion):
    descargador = seed.Descargador(delay=0, cliente=sesion.cliente)
    descargador.cargar_robots()
    cuenta = seed.descargar_manual(seed.MANUALES["irpf-2025"], tmp_path, descargador, max_paginas=50)

    assert cuenta == {"guardadas": 3, "existentes": 0, "fallidas": 0, "bloqueadas_robots": 1}
    assert not any("NoIx" in url or url.endswith(".pdf") for url in sesion.pedidas)
    carpeta = tmp_path / "aeat-manuales" / "irpf-2025"
    assert sorted(p.name for p in carpeta.glob("*.html")) == ["c01__hija.html", "c01__intro.html", "c02.html"]
    meta = json.loads((carpeta / "c02.json").read_text(encoding="utf-8"))
    assert meta["url"] == f"{SEDE}{PREFIJO}c02.html"
    assert meta["source_authority"] == "AEAT"
    assert meta["ejercicio"] == "2025"


def test_descargar_manual_reanuda_sin_volver_a_pedir_lo_guardado(tmp_path: Path, sesion: _Sesion):
    manual = seed.MANUALES["irpf-2025"]
    descargador = seed.Descargador(delay=0, cliente=sesion.cliente)
    descargador.cargar_robots()
    seed.descargar_manual(manual, tmp_path, descargador, max_paginas=50)
    sesion.pedidas.clear()

    cuenta = seed.descargar_manual(manual, tmp_path, descargador, max_paginas=50)
    assert cuenta["existentes"] == 3 and cuenta["guardadas"] == 0
    # Solo el índice se vuelve a pedir (para descubrir epígrafes nuevos).
    assert sesion.pedidas == [SEDE + "/Sede/Ayuda/25Manual/100.html"]


def test_cli_dry_run_no_escribe(tmp_path: Path, sesion: _Sesion, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(http, "_por_defecto", [sesion.cliente])
    resultado = CliRunner().invoke(
        seed.cli,
        [
            "--output-dir",
            str(tmp_path),
            "--snapshot-date",
            "2026-01-01",
            "--manual",
            "irpf-2025",
            "--delay",
            "0",
            "--dry-run",
        ],
    )
    assert resultado.exit_code == 0, resultado.output
    assert not (tmp_path / "2026-01-01").exists()

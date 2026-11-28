from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import httpx
import pytest

from asistente.plataforma.cli import boe_seed

_CATALOG = {
    "description": "test",
    "documents": [
        {
            "id": "BOE-X-0001",
            "topic": "modelo_xxx",
            "label": "Test BOE one",
            "subdir": "boe-test",
        },
        {
            "id": "BOE-X-0002",
            "topic": "modelo_xxx",
            "label": "Test BOE two",
            "subdir": "boe-test",
        },
    ],
}


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_CATALOG), encoding="utf-8")
    return path


def test_boe_seed_dry_run_lists_targets(catalog_path: Path, tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        boe_seed.cli,
        [
            "--catalog",
            str(catalog_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--snapshot-date",
            "2026-01-15",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "BOE-X-0001" in result.output
    assert "BOE-X-0002" in result.output
    # Dry-run must not create files.
    assert not (tmp_path / "out" / "2026-01-15" / "boe-test").exists()


def test_boe_seed_real_run_writes_files_and_manifest(catalog_path: Path, tmp_path: Path, red_falsa):
    vistas = red_falsa(
        lambda request: httpx.Response(200, content=f"<html><title>{request.url}</title></html>".encode())
    )

    runner = CliRunner()
    result = runner.invoke(
        boe_seed.cli,
        [
            "--catalog",
            str(catalog_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--snapshot-date",
            "2026-01-15",
        ],
    )
    assert result.exit_code == 0, result.output

    out_dir = tmp_path / "out" / "2026-01-15" / "boe-test"
    assert (out_dir / "BOE-X-0001.html").exists()
    assert (out_dir / "BOE-X-0002.html").exists()

    manifest_path = tmp_path / "out" / "2026-01-15" / "boe-seed-manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["snapshot_date"] == "2026-01-15"
    ids = [doc["id"] for doc in manifest["documents"]]
    assert ids == ["BOE-X-0001", "BOE-X-0002"]
    for doc in manifest["documents"]:
        assert doc["bytes"] > 0

    # Each norm is asked first by blocks; these ids have none (the answer is a page), so the seed falls back to
    # BOE's act.php (consolidated) endpoint, never the original diario_boe one — that contract is what
    # guarantees the indexed text reflects current law.
    urls = [str(r.url) for r in vistas]
    assert all("buscar/act.php?id=" in u or "legislacion-consolidada/id/" in u for u in urls)
    assert sum("buscar/act.php?id=" in u for u in urls) == 2
    assert not list(out_dir.glob("*.boe.xml"))


_BLOQUES = (
    '<?xml version="1.0" encoding="utf-8"?><response><status><code>200</code></status><data><texto>'
    '<bloque id="a1" tipo="precepto" titulo="Artículo 1">'
    '<version id_norma="BOE-X-0001" fecha_publicacion="20200101" fecha_vigencia="20200102">'
    '<p class="articulo">Artículo 1. Objeto.</p><p class="parrafo">Texto.</p></version></bloque>'
    "</texto></data></response>"
).encode()


def test_boe_seed_guarda_la_norma_por_bloques_cuando_el_boe_la_tiene(catalog_path: Path, tmp_path: Path, red_falsa):
    def responder(request: httpx.Request):
        url = str(request.url)
        if url.endswith("/BOE-X-0001/texto"):
            return httpx.Response(200, content=_BLOQUES)
        if url.endswith("/BOE-X-0001/metadatos"):
            entrada = {
                "identificador": "BOE-X-0001",
                "titulo": "Ley de prueba.",
                "fecha_actualizacion": "20260115T100000Z",
            }
            return httpx.Response(200, json={"status": {"code": "200", "text": "ok"}, "data": [entrada]})
        if "/legislacion-consolidada/" in url:
            return httpx.Response(404)
        return httpx.Response(200, content=b"<html><title>pagina</title></html>")

    red_falsa(responder)
    result = CliRunner().invoke(
        boe_seed.cli,
        ["--catalog", str(catalog_path), "--output-dir", str(tmp_path / "out"), "--snapshot-date", "2026-01-15"],
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "out" / "2026-01-15" / "boe-test"
    # The consolidated norm comes by blocks, with its sidecar and without the page; the other one, as a page.
    assert (out_dir / "BOE-X-0001.boe.xml").read_bytes() == _BLOQUES
    assert not (out_dir / "BOE-X-0001.html").exists()
    sidecar = json.loads((out_dir / "BOE-X-0001.boe.json").read_text(encoding="utf-8"))
    assert sidecar["titulo"] == "Ley de prueba" and sidecar["actualizada"] == "2026-01-15"
    assert "fecha_actualizacion" not in sidecar
    assert (out_dir / "BOE-X-0002.html").exists() and not (out_dir / "BOE-X-0002.boe.xml").exists()
    manifest = json.loads((tmp_path / "out" / "2026-01-15" / "boe-seed-manifest.json").read_text())
    assert [Path(d["path"]).name for d in manifest["documents"]] == ["BOE-X-0001.boe.xml", "BOE-X-0002.html"]


def test_boe_seed_solo_html_no_pide_los_bloques(catalog_path: Path, tmp_path: Path, red_falsa):
    vistas = red_falsa(lambda request: httpx.Response(200, content=b"<html><title>pagina</title></html>"))
    result = CliRunner().invoke(
        boe_seed.cli,
        ["--catalog", str(catalog_path), "--output-dir", str(tmp_path / "out"), "--solo-html"],
    )
    assert result.exit_code == 0, result.output
    assert vistas and all("buscar/act.php?id=" in str(r.url) for r in vistas)


def test_boe_seed_retries_on_transient_failure(tmp_path: Path, red_falsa):
    attempts: list[int] = []

    def responder(request: httpx.Request):
        if "legislacion-consolidada" in str(request.url):
            return httpx.Response(404)  # no blocks for this id: the page is what gets retried
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.ReadTimeout("simulated network blip")
        return httpx.Response(200, content=b"<html><title>ok</title></html>")

    red_falsa(responder)

    catalog_one = tmp_path / "single.json"
    catalog_one.write_text(
        json.dumps(
            {
                "documents": [
                    {"id": "BOE-X-9999", "label": "only", "subdir": "boe-flaky"},
                ]
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        boe_seed.cli,
        [
            "--catalog",
            str(catalog_one),
            "--output-dir",
            str(tmp_path / "out"),
            "--snapshot-date",
            "2026-01-15",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "2026-01-15" / "boe-flaky" / "BOE-X-9999.html").exists()
    assert len(attempts) == 2, "expected one retry on the transient TimeoutError"


def test_boe_seed_one_bad_id_does_not_abort_the_run(catalog_path: Path, tmp_path: Path, red_falsa):
    """A withdrawn ID used to abort the loop before the manifest was written."""

    def responder(request: httpx.Request):
        if str(request.url).endswith("BOE-X-0001"):
            return httpx.ConnectError("gone")
        return httpx.Response(200, content=b"<html><title>ok</title></html>")

    red_falsa(responder)

    result = CliRunner().invoke(
        boe_seed.cli,
        ["--catalog", str(catalog_path), "--output-dir", str(tmp_path / "out"), "--snapshot-date", "2026-01-15"],
    )
    assert result.exit_code != 0
    assert "BOE-X-0001" in result.output
    out = tmp_path / "out" / "2026-01-15"
    assert (out / "boe-test" / "BOE-X-0002.html").exists()
    manifest = json.loads((out / "boe-seed-manifest.json").read_text())
    assert [d["id"] for d in manifest["documents"]] == ["BOE-X-0002"]
    assert [f["id"] for f in manifest["failed"]] == ["BOE-X-0001"]

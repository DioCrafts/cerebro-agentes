"""Fetch a curated set of BOE consolidated documents into the RAG corpus.

The general `boe-ccaa` crawler downloads the CCAA codes; this CLI is a thin
companion for the long tail of *individual* legislative acts that we want
indexed by ID — e.g. the Modelo 720 trio (Ley 7/2012, RD 1558/2012, Orden
HAP/72/2013). Maintainers add new IDs to ``rules/boe_seed_catalog.json`` and
re-run ``uv run python -m asistente.plataforma.cli.boe_seed`` to refresh the
local snapshot.

Each norm is asked first by blocks, from the open-data API: one block per article, with every version and the
date it came into force (``<ID>.boe.xml`` plus a sidecar; see ``asistente/conocimiento/boe/bloques.py``). That
is what the processor cuts by article. What the BOE does not consolidate (a court ruling, a one-off order) has no
blocks and is fetched as before, from ``https://www.boe.es/buscar/act.php?id=<ID>``. ``--solo-html`` keeps the
old behaviour for everything.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import time

import click
import httpx

from asistente.conocimiento.boe import cliente as boe

logger = logging.getLogger(__name__)


# Resource-relative default so the CLI can be run from anywhere, and so the
# catalog never collides with `asistente.nucleo.decision.rules.load_rules_from_directory`
# which globs every JSON in the top-level ``rules/`` folder.
_DEFAULT_CATALOG = Path(__file__).resolve().parent / "boe_seed_catalog.json"
_DEFAULT_OUTPUT = Path("./data/html")
_DEFAULT_SUBDIR = "boe-seed"


def load_catalog(catalog_path: Path) -> list[dict[str, str]]:
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    entries: list[dict[str, str]] = []
    for entry in raw.get("documents", []):
        boe_id = entry.get("id")
        if not boe_id:
            raise click.ClickException(f"Catalog entry missing 'id': {entry!r}")
        entries.append(
            {
                "id": boe_id,
                "label": entry.get("label", boe_id),
                "topic": entry.get("topic", ""),
                "subdir": entry.get("subdir", _DEFAULT_SUBDIR),
                "sigla": entry.get("sigla", ""),
            }
        )
    return entries


def fetch_blocks(boe_id: str, carpeta: Path, sigla: str = "") -> Path | None:
    """The norm by blocks (``<ID>.boe.xml`` and its sidecar) in ``carpeta``; None when the BOE has none for it
    or the request fails (the caller then falls back to the rendered page)."""
    try:
        return boe.guardar_bloques(boe_id, carpeta, sigla=sigla)
    except httpx.HTTPError as exc:
        logger.warning("Blocks of %s failed (%s); falling back to the page.", boe_id, exc)
        return None


def fetch_document(boe_id: str, target_path: Path) -> int:
    """Download a single consolidated BOE document; return bytes written.

    Retries and pauses are the shared HTTP client's (:mod:`asistente.plataforma.http`).
    """
    try:
        # Format watch (never raises): whatever arrives is saved; is it the norm that was asked for?
        body = boe.texto_consolidado(boe_id, timeout=30, observar="boe_seed")
    except httpx.HTTPError as exc:
        logger.warning("Download of %s failed: %s", boe_id, exc)
        raise click.ClickException(f"Could not fetch {boe_id}: {exc}") from exc
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(body)
    return len(body)


@click.command()
@click.option(
    "--catalog",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=_DEFAULT_CATALOG,
    show_default=True,
    help="JSON file with the BOE IDs to fetch.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=_DEFAULT_OUTPUT,
    show_default=True,
    help="Root directory for the local HTML snapshot (each ID is written under"
    " <output-dir>/<snapshot-date>/<subdir>/<ID>.html).",
)
@click.option(
    "--snapshot-date",
    default=None,
    help="Snapshot folder name (YYYY-MM-DD). Defaults to today (UTC).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print what would be downloaded without making HTTP calls.",
)
@click.option(
    "--delay",
    default=0.5,
    show_default=True,
    type=click.FloatRange(min=0.0),
    help="Polite pause (seconds) between two BOE downloads.",
)
@click.option(
    "--solo-html",
    is_flag=True,
    default=False,
    help="Do not ask for the norm by blocks: download only the rendered page, as before November 2026.",
)
def cli(
    catalog: Path, output_dir: Path, snapshot_date: str | None, dry_run: bool, delay: float, solo_html: bool
) -> None:
    """Fetch every BOE ID listed in CATALOG into OUTPUT_DIR."""
    snapshot = snapshot_date or datetime.now(UTC).strftime("%Y-%m-%d")
    entries = load_catalog(catalog)
    if not entries:
        raise click.ClickException("Catalog has no documents.")

    snapshot_root = output_dir / snapshot
    summary: list[dict[str, str | int]] = []
    failed: list[dict[str, str]] = []
    for index, entry in enumerate(entries):
        target = snapshot_root / entry["subdir"] / f"{entry['id']}.html"
        if dry_run:
            click.echo(f"[dry-run] {entry['id']:18} → {target}")
            summary.append({"id": entry["id"], "path": str(target), "bytes": 0})
            continue
        if index and delay:
            time.sleep(delay)
        click.echo(f"Fetching {entry['id']} ({entry['label']}) → {target}")
        if not solo_html:
            bloques = fetch_blocks(entry["id"], target.parent, entry["sigla"])
            if bloques is not None:
                summary.append({"id": entry["id"], "path": str(bloques), "bytes": bloques.stat().st_size})
                continue
        # One bad or withdrawn ID must not throw away the rest of the run (and its manifest).
        try:
            size = fetch_document(entry["id"], target)
        except click.ClickException as exc:
            click.echo(f"  FAILED: {exc.message}", err=True)
            failed.append({"id": entry["id"], "error": exc.message})
            continue
        summary.append({"id": entry["id"], "path": str(target), "bytes": size})

    manifest = snapshot_root / "boe-seed-manifest.json"
    if not dry_run:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "snapshot_date": snapshot,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "catalog_path": str(catalog),
                    "documents": summary,
                    "failed": failed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        click.echo(f"Wrote manifest: {manifest}")
    if failed:
        raise click.ClickException(
            f"{len(failed)} of {len(entries)} BOE documents failed: " + ", ".join(f["id"] for f in failed)
        )


if __name__ == "__main__":
    cli()

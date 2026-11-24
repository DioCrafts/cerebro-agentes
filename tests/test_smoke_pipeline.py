from datetime import UTC, datetime

from click.testing import CliRunner

from asistente.plataforma.cli import crawler as crawler_cli
from asistente.plataforma.cli import processor as processor_cli


def test_smoke_pipeline_crawler_to_index(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_start_crawler(crawler_class, settings, folder, mode, snapshot_date, extra_kwargs=None):
        calls["crawler"] = {
            "crawler_class": crawler_class.__name__,
            "depth": settings["DEPTH_LIMIT"],
            "folder": folder,
            "mode": mode,
            "snapshot_date": snapshot_date,
            "extra_kwargs": extra_kwargs or {},
        }

    def fake_build_index(args):
        calls["processor"] = args

    monkeypatch.setattr(crawler_cli, "start_crawler", fake_start_crawler)
    monkeypatch.setattr(processor_cli, "build_index", fake_build_index)

    runner = CliRunner()
    snapshot = datetime.now(UTC).strftime("%Y-%m-%d")

    crawl_result = runner.invoke(
        crawler_cli.cli,
        ["--crawler", "web", "--folder", "./tmp/html", "--depth", "1", "--mode", "flat", "--snapshot-date", snapshot],
    )
    assert crawl_result.exit_code == 0

    process_result = runner.invoke(
        processor_cli.cli,
        ["--content-dir", ".", "--output-dir", "./tmp/indice", "--overwrite-output"],
    )
    assert process_result.exit_code == 0

    assert calls["crawler"]
    assert calls["processor"]

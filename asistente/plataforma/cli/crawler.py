import os
from typing import Any

import click
from scrapy.crawler import CrawlerProcess
from scrapy.spiders import Spider

from asistente.conocimiento.crawler.crawlers import (
    AgenciaTributariaPDFCrawler,
    AgenciaTributariaWebCrawler,
    BOECCAACrawler,
    CENDOJCrawler,
    RegionalBoletinCrawler,
    TEACCrawler,
)

CRAWLER_MAPPING: dict[str, type[Spider]] = {
    "web": AgenciaTributariaWebCrawler,
    "pdf": AgenciaTributariaPDFCrawler,
    "teac": TEACCrawler,
    "cendoj": CENDOJCrawler,
    "boe-ccaa": BOECCAACrawler,
    "boletin": RegionalBoletinCrawler,
}

_PARAM_FREE_CRAWLERS = {AgenciaTributariaPDFCrawler, AgenciaTributariaWebCrawler}

# An honest, fixed User-Agent (no rotating fake browsers: the sources are public bodies we identify ourselves to).
USER_AGENT = "asistente-fiscal-crawler/1.0 (+https://github.com/DioCrafts/cerebro-agentes)"

SETTINGS = {
    "ROBOTSTXT_OBEY": True,
    "USER_AGENT": USER_AGENT,
    "HTTPCACHE_ENABLED": True,
    # A cached page is reused for one day at most (the corpus tracks sources that change), and a server error
    # is never cached: it would be replayed as the page's content on every later run.
    "HTTPCACHE_EXPIRATION_SECS": 24 * 3600,
    "HTTPCACHE_IGNORE_HTTP_CODES": list(range(500, 600)),
    # scrapy-playwright is only the download handler of the AEAT sede crawler (its custom_settings); every
    # other crawler uses Scrapy's own downloader. The asyncio reactor is a process-wide choice, so it is here.
    "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
    "PLAYWRIGHT_BROWSER_TYPE": "chromium",
    "PLAYWRIGHT_LAUNCH_OPTIONS": {
        "executable_path": os.environ.get("PLAYWRIGHT_BROWSERS_BINARY_PATH"),
    },
}


def rastreo_fallido(stats: dict[str, Any]) -> str | None:
    """Why a finished crawl counts as failed (None when it went well): it did not finish normally, a request
    failed (errback or download error) or a callback raised."""
    motivo = stats.get("finish_reason")
    if motivo != "finished":
        return f"el rastreo terminó con «{motivo}»"
    fallos = sum(
        int(valor)
        for clave, valor in stats.items()
        if clave in ("crawler/peticiones_fallidas", "downloader/exception_count")
        or clave.startswith("spider_exceptions/")
    )
    if fallos:
        return f"{fallos} peticiones o páginas fallaron"
    if int(stats.get("log_count/ERROR", 0)):
        return "hubo errores en el registro"
    return None


def start_crawler(
    crawler_class: type[Spider],
    settings: dict[str, Any],
    folder: str,
    mode: str,
    snapshot_date: str | None,
    extra_kwargs: dict[str, Any] | None = None,
) -> None:
    """Run one crawl; ``SystemExit(1)`` when it failed (see :func:`rastreo_fallido`), so cron and scripts notice."""
    process = CrawlerProcess(settings=settings)
    kwargs = {"folder": os.path.abspath(folder), "snapshot_date": snapshot_date}
    if crawler_class in _PARAM_FREE_CRAWLERS:
        kwargs["mode"] = mode
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    crawler = process.create_crawler(crawler_class)
    process.crawl(crawler, **kwargs)
    process.start(install_signal_handlers=True)
    stats = crawler.stats.get_stats() if crawler.stats is not None else {}
    motivo = rastreo_fallido(stats)
    if motivo is not None:
        click.echo(f"El rastreo de {crawler_class.__name__} ha fallado: {motivo}.", err=True)
        raise SystemExit(1)


@click.command()
@click.option("--folder", default="./data/html", help="Folder to store files")
@click.option("--depth", default=1, help="Max depth to crawl. 0 for unlimited depth")
@click.option(
    "--crawler",
    type=click.Choice(["web", "pdf", "teac", "cendoj", "boe-ccaa", "boletin"]),
    default="web",
    help="Type of crawler to use",
)
@click.option("--mode", default="flat", help="File storage mode. Can be 'flat' or other modes")
@click.option("--snapshot-date", default=None, help="Snapshot date folder name (YYYY-MM-DD)")
@click.option("--teac-start-id", default=1, type=int, help="DYCTEA criterion start id (only used by --crawler teac)")
@click.option("--teac-end-id", default=100, type=int, help="DYCTEA criterion end id (only used by --crawler teac)")
@click.option("--cendoj-urls-file", default=None, help="Path to a file with one CENDOJ resolution URL per line")
@click.option("--cendoj-urls", default=None, help="Semicolon-separated CENDOJ resolution URLs")
@click.option(
    "--cendoj-only-tributario/--cendoj-keep-all", default=True, help="Keep only resolutions flagged as tributario"
)
@click.option("--ccaa", default=None, help="Comma-separated CCAA keys for --crawler boe-ccaa (default: all)")
@click.option("--skip-unknown-ccaa", is_flag=True, default=False, help="Only crawl CCAA codes verified as published")
@click.option(
    "--boletines", default=None, help="Comma-separated boletin keys for --crawler boletin (default: all bundled)"
)
@click.option("--boletin-terms", default=None, help="Override the per-spec default search terms (comma separated)")
def cli(
    folder: str,
    depth: int,
    crawler: str,
    mode: str,
    snapshot_date: str | None,
    teac_start_id: int,
    teac_end_id: int,
    cendoj_urls_file: str | None,
    cendoj_urls: str | None,
    cendoj_only_tributario: bool,
    ccaa: str | None,
    skip_unknown_ccaa: bool,
    boletines: str | None,
    boletin_terms: str | None,
) -> None:
    settings = {**SETTINGS, **{"DEPTH_LIMIT": depth}}
    crawler_class = CRAWLER_MAPPING[crawler]
    extra: dict[str, Any] = {}
    if crawler == "teac":
        extra = {"start_id": teac_start_id, "end_id": teac_end_id}
    elif crawler == "cendoj":
        extra = {
            "urls": cendoj_urls,
            "urls_file": cendoj_urls_file,
            "only_tributario": cendoj_only_tributario,
        }
    elif crawler == "boe-ccaa":
        extra = {"ccaa": ccaa, "skip_unknown": skip_unknown_ccaa}
    elif crawler == "boletin":
        extra = {"boletines": boletines, "terms": boletin_terms}
    start_crawler(crawler_class, settings, folder, mode, snapshot_date, extra_kwargs=extra)


if __name__ == "__main__":
    cli()

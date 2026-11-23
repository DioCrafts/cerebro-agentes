"""Scrapy downloader middlewares of this project's crawlers.

There used to be a ``ForcePlaywrightMiddleware`` here that sent EVERY https request through a full Chromium
(a workaround for a TLS incompatibility of an old pinned stack). It made every crawler run untrusted pages'
JavaScript and turned PDFs into rendered viewers. Only the AEAT sede crawler needs a browser (its pages are
built by scripts); it asks for one itself (``AgenciaTributariaWebCrawler.custom_settings`` and its request
meta). The module stays so old import paths keep working.
"""

from __future__ import annotations

from typing import Any


def peticion_fallida(spider: Any, failure: Any) -> None:
    """Errback of every request: counted in the stats (the CLI exits non-zero) and logged."""
    request = getattr(failure, "request", None)
    if getattr(spider, "crawler", None) is not None and spider.crawler.stats is not None:
        spider.crawler.stats.inc_value("crawler/peticiones_fallidas")
    spider.logger.warning("Request failed: %s (%s)", request.url if request else "?", failure.value)

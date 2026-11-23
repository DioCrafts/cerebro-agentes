"""Crawler hardening: Playwright only where needed, honest UA, expiring cache without 5xx, PDFs followed,
writes kept inside the folder, «iva» as a word, failed crawls exit non-zero. No network."""

from __future__ import annotations

from typing import Any

import pytest
from scrapy.http import HtmlResponse

from asistente.conocimiento.crawler import cendoj, crawlers
from asistente.plataforma.cli import crawler as cli


def test_settings_sin_playwright_global_ni_useragent_falso() -> None:
    texto = repr(cli.SETTINGS)
    assert "ForcePlaywright" not in texto and "fake_useragent" not in texto
    assert "DOWNLOAD_HANDLERS" not in cli.SETTINGS
    assert cli.SETTINGS["USER_AGENT"] == cli.USER_AGENT
    assert cli.SETTINGS["HTTPCACHE_EXPIRATION_SECS"] > 0
    assert {500, 502, 503, 599} <= set(cli.SETTINGS["HTTPCACHE_IGNORE_HTTP_CODES"])
    assert "playwright" in repr(crawlers.AgenciaTributariaWebCrawler.custom_settings)
    for otro in (crawlers.AgenciaTributariaPDFCrawler, crawlers.TEACCrawler, crawlers.CENDOJCrawler):
        assert "playwright" not in repr(getattr(otro, "custom_settings", None) or {})


def test_el_crawler_de_pdf_sigue_los_enlaces_pdf(tmp_path) -> None:
    spider = crawlers.AgenciaTributariaPDFCrawler(folder=str(tmp_path), snapshot_date="2026-01-01")
    html = b'<a href="/static/manual.pdf">m</a><a href="/Sede/x.html">x</a>'
    respuesta = HtmlResponse(url="https://sede.agenciatributaria.gob.es/", body=html, encoding="utf-8")
    peticiones = list(spider.parse(respuesta))
    urls = {p.url: p for p in peticiones}
    assert "https://sede.agenciatributaria.gob.es/static/manual.pdf" in urls
    assert urls["https://sede.agenciatributaria.gob.es/static/manual.pdf"].callback == spider.parse_pdf
    assert all(p.errback is not None for p in peticiones)


@pytest.mark.parametrize("relativa", ["../fuera.html", "a/../../fuera.html", "/etc/passwd", "."])
def test_ruta_dentro_rechaza_salir_de_la_carpeta(tmp_path, relativa: str) -> None:
    with pytest.raises(ValueError):
        crawlers.ruta_dentro(str(tmp_path / "snap"), relativa)


def test_modo_estructura_no_escribe_fuera(tmp_path) -> None:
    spider = crawlers.AgenciaTributariaWebCrawler(folder=str(tmp_path), mode="tree", snapshot_date="s")
    bueno = HtmlResponse(url="https://sede.agenciatributaria.gob.es/Sede/iva/a.html", body=b"ok")
    spider._save_response_to_file(bueno)
    assert (tmp_path / "s" / "Sede" / "iva" / "a.html").read_bytes() == b"ok"
    enlace = tmp_path / "s" / "enlace"
    enlace.symlink_to(tmp_path)
    malo = HtmlResponse(url="https://sede.agenciatributaria.gob.es/enlace/fuera.html", body=b"x")
    spider._save_response_to_file(malo)
    assert not (tmp_path / "fuera.html").exists()


def test_iva_como_palabra() -> None:
    assert not cendoj._classify_tributario("Pena privativa de libertad; sentencia definitiva y firme, parte activa.")
    assert cendoj._classify_tributario("La cuota de IVA repercutida.")
    assert cendoj._classify_tributario("Liquidación del I.V.A. ante la AEAT.")


def test_rastreo_fallido() -> None:
    assert cli.rastreo_fallido({"finish_reason": "finished"}) is None
    assert cli.rastreo_fallido({"finish_reason": "shutdown"})
    assert cli.rastreo_fallido({"finish_reason": "finished", "crawler/peticiones_fallidas": 2})
    assert cli.rastreo_fallido({"finish_reason": "finished", "spider_exceptions/ValueError": 1})
    assert cli.rastreo_fallido({"finish_reason": "finished", "log_count/ERROR": 1})


class _Stats:
    def __init__(self, datos: dict[str, Any]) -> None:
        self.datos = datos

    def get_stats(self) -> dict[str, Any]:
        return self.datos


def test_start_crawler_sale_con_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    resultado: dict[str, Any] = {"finish_reason": "finished", "crawler/peticiones_fallidas": 1}

    class _Proceso:
        def __init__(self, settings: Any) -> None:
            pass

        def create_crawler(self, clase: Any) -> Any:
            return type("C", (), {"stats": _Stats(resultado)})()

        def crawl(self, *_: Any, **__: Any) -> None:
            return None

        def start(self, **_: Any) -> None:
            return None

    monkeypatch.setattr(cli, "CrawlerProcess", _Proceso)
    with pytest.raises(SystemExit) as salida:
        cli.start_crawler(crawlers.TEACCrawler, {}, str(tmp_path), "flat", None)
    assert salida.value.code == 1
    resultado.pop("crawler/peticiones_fallidas")
    cli.start_crawler(crawlers.TEACCrawler, {}, str(tmp_path), "flat", None)

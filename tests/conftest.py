import os
import sys
import tempfile
import types

import pytest

# The format watch (asistente/conocimiento/formatos.py) records every real read under ./data by default.
# Switched off through the environment, before ``settings`` is first imported, so that it also holds
# for module- and session-scoped fixtures and survives the tests that reload ``settings``.
os.environ["FORMAT_WATCH"] = "false"
# The API's edge defaults (asistente/plataforma/api/security.py) are for real deployments: the TestClient's
# host is "testserver", one process sends thousands of requests and most tests run without authentication.
# Tests of those defaults set or remove these variables themselves.
os.environ.setdefault("API_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
os.environ.setdefault("API_RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("PERMITIR_SIN_AUTENTICACION", "1")
os.environ["FORMAT_STATE_PATH"] = os.path.join(tempfile.gettempdir(), "hacienda-tests-formatos", "_estado.json")
os.environ["FORMAT_DIR"] = os.path.join(tempfile.gettempdir(), "hacienda-tests-formatos", "formatos")
# Same for the baseline of the household modules' BOE sources (params-check).
os.environ["MODULOS_FUENTES_STATE_PATH"] = os.path.join(
    tempfile.gettempdir(), "hacienda-tests-formatos", "_modulos_fuentes.json"
)


def _ensure_module(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


def _real_dependencies_available() -> bool:
    """True when the real (non-stub) heavy deps are importable.

    In that case the suite must run against real code, not the stubs below.
    """
    try:
        import click  # noqa: F401
        import langchain_core.callbacks  # noqa: F401
        import pydantic  # noqa: F401
    except ImportError:
        return False
    return True


def pytest_sessionstart(session):
    # Use the real packages when they are installed (e.g. CI after
    # `pip install .`); only stub when they are absent (CI-lite dev env).
    if _real_dependencies_available():
        return

    # Minimal stubs for optional heavy dependencies so imports succeed in CI-lite envs.
    lc_core_ret = _ensure_module("langchain_core.retrievers")

    class _StubBaseRetriever:
        """Accepts arbitrary kwargs, matching the Pydantic constructor that
        real `BaseRetriever` exposes."""

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    lc_core_ret.BaseRetriever = _StubBaseRetriever
    lc_core_run = _ensure_module("langchain_core.runnables")
    lc_core_run.Runnable = object

    lc_msgs = sys.modules.get("langchain_core.messages")
    if lc_msgs is None or not hasattr(lc_msgs, "BaseMessage"):
        lc_msgs = _ensure_module("langchain_core.messages")

        class _Msg:
            def __init__(self, content):
                self.content = content

            def __eq__(self, other):
                return self.__class__ is other.__class__ and self.content == other.content

        class BaseMessage(_Msg):
            pass

        class AIMessage(BaseMessage):
            pass

        class HumanMessage(BaseMessage):
            pass

        def _get_buffer_string(messages, human_prefix="Human", ai_prefix="AI"):
            return "\n".join(getattr(m, "content", str(m)) for m in messages)

        lc_msgs.BaseMessage = BaseMessage
        lc_msgs.AIMessage = AIMessage
        lc_msgs.HumanMessage = HumanMessage
        lc_msgs.get_buffer_string = _get_buffer_string

    vc = _ensure_module("langchain_community.vectorstores")

    class FAISS:
        @staticmethod
        def load_local(*_a, **_k):
            return object()

    vc.FAISS = FAISS

    scrapy = _ensure_module("scrapy")

    class Spider:
        pass

    class Request:
        def __init__(self, *a, **k):
            self.args = a
            self.kwargs = k

    scrapy.Spider = Spider
    scrapy.Request = Request

    http = _ensure_module("scrapy.http")
    http.Response = object
    le = _ensure_module("scrapy.linkextractors")

    class LinkExtractor:
        def __init__(self, *a, **k):
            pass

        def extract_links(self, response):
            return []

    le.LinkExtractor = LinkExtractor

    crawler = _ensure_module("scrapy.crawler")

    class CrawlerProcess:
        def __init__(self, settings=None):
            self.settings = settings

        def crawl(self, *a, **k):
            return None

        def start(self, install_signal_handlers=True):
            return None

    crawler.CrawlerProcess = CrawlerProcess
    spiders = _ensure_module("scrapy.spiders")
    spiders.Spider = Spider

    sp = _ensure_module("scrapy_playwright.page")
    sp.PageMethod = object
    pv = _ensure_module("pathvalidate")
    pv.sanitize_filepath = lambda value: value
    lc_docs = _ensure_module("langchain_core.documents")

    class Document:
        def __init__(self, page_content="", metadata=None):
            self.page_content = page_content
            self.metadata = metadata or {}

    lc_docs.Document = Document

    lc_emb = _ensure_module("langchain_core.embeddings")
    lc_emb.Embeddings = object


@pytest.fixture(autouse=True)
def _formatos_sin_rastro(monkeypatch, tmp_path):
    """The format watch writes state and samples on every real read: keep tests from touching ./data."""
    from asistente.plataforma import settings

    monkeypatch.setattr(settings, "FORMAT_WATCH", False, raising=False)
    monkeypatch.setattr(settings, "FORMAT_STATE_PATH", str(tmp_path / "_formatos_state.json"), raising=False)
    monkeypatch.setattr(settings, "FORMAT_DIR", str(tmp_path / "formatos"), raising=False)


@pytest.fixture(autouse=True)
def _registro_de_modulos_sin_rastro():
    """No test leaves its module registry behind: a fake one (``reiniciar(nuevo)``), or a real one built while
    the test had patched settings (``MODULOS_DESACTIVADOS``…), is forgotten after the test, so the next test
    discovers the real modules again. Tests that leave the registry untouched pay nothing."""
    from asistente.nucleo.modulos import registro

    antes = list(registro._cache)
    yield
    despues = list(registro._cache)
    if len(antes) != len(despues) or any(a is not d for a, d in zip(antes, despues, strict=False)):
        registro.reiniciar()

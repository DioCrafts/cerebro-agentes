import importlib

import pytest

import asistente.plataforma.settings as settings


def test_env_bool_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAG", "TrUe")
    assert settings._env_bool("FEATURE_FLAG") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "random"])
def test_env_bool_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("FEATURE_FLAG", value)
    assert settings._env_bool("FEATURE_FLAG") is False


def test_env_bool_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEATURE_FLAG", raising=False)
    assert settings._env_bool("FEATURE_FLAG", default=True) is True
    assert settings._env_bool("FEATURE_FLAG", default=False) is False


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ["OPENAI_AGENT_MODEL", "INDICE_DIR", "INDICE_SQLITE_PATH", "FAISS_INDEX_PATH", "BM25_ENABLED"]:
        monkeypatch.delenv(key, raising=False)

    reloaded = importlib.reload(settings)

    assert reloaded.OPENAI_AGENT_MODEL == "gpt-6.2-sol"
    assert reloaded.INDICE_DIR == "data/indice"
    assert reloaded.INDICE_SQLITE_PATH == "data/indice/indice.sqlite"
    assert reloaded.BM25_ENABLED is True  # hybrid search by default (docs/cerebro/benchmark-recuperacion.md)
    assert not hasattr(reloaded, "FAISS_INDEX_PATH") and not hasattr(reloaded, "INDICE_BACKEND")


def test_the_old_faiss_variable_is_reported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A deployment that still sets FAISS_INDEX_PATH is told the index moved instead of searching nothing."""
    monkeypatch.delenv("INDICE_DIR", raising=False)
    monkeypatch.setenv("FAISS_INDEX_PATH", "/srv/cerebro/faiss")
    with caplog.at_level("WARNING"):
        reloaded = importlib.reload(settings)
    assert "FAISS_INDEX_PATH ya no se usa" in caplog.text
    assert reloaded.INDICE_DIR == "data/indice"
    monkeypatch.delenv("FAISS_INDEX_PATH")
    importlib.reload(settings)

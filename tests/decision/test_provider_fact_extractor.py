import json

import pytest

from hacienda_gpt.agent.providers.base import ProviderError
from hacienda_gpt.decision import fact_extractor as fx
from hacienda_gpt.decision.fact_extractor import (
    FactExtractionError,
    OpenAIFactExtractor,
    ProviderFactExtractor,
    RegexFactExtractor,
    default_fact_extractor,
)
from hacienda_gpt.decision.schemas import CaseState


class _JsonProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[dict] = []

    def complete_json(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_provider_extractor_coerces_string_values_and_drops_bad_ones() -> None:
    provider = _JsonProvider(
        {
            "intent": "autonomo",
            "intent_confidence": 1.7,
            "facts": [
                {"name": "volumen_facturacion", "value": "45.000", "confidence": 0.9},
                {"name": "alta_actividad_economica", "value": "true", "confidence": 0.8},
                {"name": "residencia_fiscal", "value": "ES", "confidence": 0.95},
                {"name": "plantilla", "value": 0, "confidence": 0.7},  # model ignored "always text"
                {"name": "importe_renta_aproximado", "value": "bastante", "confidence": 0.9},
                {"name": "hecho_inventado", "value": "x", "confidence": 0.9},
                "basura",
            ],
        }
    )
    case = CaseState(case_id="c1", user_id="u", jurisdiction="ES", tax_period="2025")

    payload = ProviderFactExtractor(provider).extract(
        "soy autónomo y facturo 45.000 €", [{"role": "user", "content": "hola"}], case
    )

    assert payload.intent == "autonomo" and payload.intent_confidence == 1.0
    assert {fact.name: fact.value for fact in payload.extracted_facts} == {
        "volumen_facturacion": 45000.0,
        "alta_actividad_economica": True,
        "residencia_fiscal": "ES",
        "plantilla": 0.0,
    }

    request = provider.requests[0]
    assert request["schema_name"] == "ExtractedFacts"
    assert "siempre como" in request["system"]
    user_payload = json.loads(request["user"])
    assert user_payload["user_input"] == "soy autónomo y facturo 45.000 €"
    assert user_payload["current_case_state"]["case_id"] == "c1"
    # No constructs that provider-side structured output commonly rejects.
    schema_text = json.dumps(request["schema"])
    assert "minimum" not in schema_text and '"null"' not in schema_text


def test_provider_extractor_wraps_provider_failures() -> None:
    extractor = ProviderFactExtractor(_JsonProvider(ProviderError("sin red")))
    with pytest.raises(FactExtractionError, match="sin red"):
        extractor.extract("hola", [], None)


def test_default_extractor_follows_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DECISION_EXTRACTOR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from hacienda_gpt import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "LOCAL_LLM_MODEL", "")
    assert isinstance(default_fact_extractor(), RegexFactExtractor)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert isinstance(default_fact_extractor(), OpenAIFactExtractor)

    # A configured Claude wins over a stray OpenAI key: LLM_PROVIDER decides.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    extractor = default_fact_extractor()
    assert isinstance(extractor, ProviderFactExtractor)
    assert (extractor._provider.name, extractor._provider.effort) == ("anthropic", "low")

    monkeypatch.setenv("DECISION_EXTRACTOR", "regex")
    assert isinstance(default_fact_extractor(), RegexFactExtractor)

    monkeypatch.setenv("DECISION_EXTRACTOR", "provider")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "local")
    monkeypatch.setattr(settings, "LOCAL_LLM_MODEL", "qwen3:14b")
    assert default_fact_extractor()._provider.name == "local"
    assert fx.FactExtractionError is fx.OpenAIExtractionError


def test_the_provider_extractor_is_metered_as_hechos_and_keeps_its_model(monkeypatch, tmp_path) -> None:
    """Metering adds the purpose «hechos» (routing table: small tier); without routing settings the model,
    the effort and the requests are the provider's own, as before."""
    from hacienda_gpt import settings
    from hacienda_gpt.agent import enrutado
    from hacienda_gpt.ops.consumo import MeteredProvider

    for nombre in (
        "LLM_ROUTING_PROFILE",
        "LLM_ROUTES",
        "LLM_MODEL_SMALL",
        "LLM_EFFORT_SMALL",
        "DECISION_EXTRACTOR_MODEL",
    ):
        monkeypatch.setattr(settings, nombre, "")
    monkeypatch.delenv("LLM_ROUTE_HECHOS", raising=False)
    monkeypatch.delenv("DECISION_EXTRACTOR_MODEL", raising=False)
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", str(tmp_path / "estado.sqlite3"))
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DECISION_EXTRACTOR", "provider")

    extractor = default_fact_extractor()
    assert isinstance(extractor, ProviderFactExtractor)
    medido = extractor._provider
    assert isinstance(medido, MeteredProvider) and medido.purpose == "hechos"
    assert enrutado.nivel_de_tabla("hechos") == "pequeno"
    assert (medido.model, medido.effort, medido.ruta.origen) == ("claude-opus-5-5", "low", "por_defecto")
    assert medido.wrapped.model == "claude-opus-5-5"

    # A model chosen for the extractor is kept even with a routing profile.
    monkeypatch.setenv("DECISION_EXTRACTOR_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(settings, "LLM_ROUTING_PROFILE", "recomendado")
    elegido = default_fact_extractor()._provider
    assert (elegido.model, elegido.ruta.origen) == ("claude-sonnet-5-5", "DECISION_EXTRACTOR_MODEL")
    # Without it, the profile routes extraction to the small tier.
    monkeypatch.delenv("DECISION_EXTRACTOR_MODEL")
    assert default_fact_extractor()._provider.model == "claude-haiku-5-5"

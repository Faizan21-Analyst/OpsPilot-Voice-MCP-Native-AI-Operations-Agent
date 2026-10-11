"""The LLM router spreads requests over every Groq key and keeps the output cap on Groq only."""
from src.core.config import GeminiSettings, GroqSettings, LiteLLMSettings
from src.providers.llm.litellm_provider import LiteLLMProvider


def _deployments(provider: LiteLLMProvider, group: str) -> list[dict]:
    return provider._router.get_model_list(model_name=group) or []


def test_groq_keys_are_deduplicated_and_blank_ones_skipped():
    groq = GroqSettings(api_key="a", api_key2="b", api_key3="b", api_key4="")
    assert groq.api_keys == ["a", "b"]


def test_one_deployment_per_key_with_output_cap_on_groq_only():
    groq = GroqSettings(api_key="a", api_key2="b", api_key3="c", model="some-model")
    provider = LiteLLMProvider(groq, GeminiSettings(api_key="g"), LiteLLMSettings(max_output_tokens=700))

    groq_deployments = _deployments(provider, "primary-groq")
    assert len(groq_deployments) == 3
    assert {d["litellm_params"]["api_key"] for d in groq_deployments} == {"a", "b", "c"}
    assert all(d["litellm_params"]["max_tokens"] == 700 for d in groq_deployments)

    gemini = _deployments(provider, "primary-gemini")
    assert len(gemini) == 1
    assert "max_tokens" not in gemini[0]["litellm_params"]


def test_optional_fallback_models_are_added_in_order():
    groq = GroqSettings(api_key="a", api_key2="b", fallback_model="other-groq-model")
    gemini = GeminiSettings(api_key="g", fallback_model="gemini/other-model")
    provider = LiteLLMProvider(groq, gemini, LiteLLMSettings())

    assert len(_deployments(provider, "backup-groq")) == 2          # same keys, different model
    assert len(_deployments(provider, "backup-gemini")) == 1
    assert provider._router.fallbacks == [{"primary-groq": ["backup-groq", "primary-gemini", "backup-gemini"]}]


def test_output_cap_can_be_switched_off():
    provider = LiteLLMProvider(GroqSettings(api_key="a"), GeminiSettings(api_key="g"), LiteLLMSettings(max_output_tokens=0))
    assert "max_tokens" not in _deployments(provider, "primary-groq")[0]["litellm_params"]

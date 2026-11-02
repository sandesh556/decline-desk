"""Provider selection, per-provider keys and models, and the client factory."""

from decimal import Decimal

import pytest

from decline_desk.agent.triage import Intent, triage
from decline_desk.config import Provider, Settings, SettingsError
from decline_desk.llm.client import FakeLLM, LLMProviderError, LLMRefused, Usage
from decline_desk.llm.factory import make_client
from decline_desk.llm.pricing import cost_usd
from decline_desk.llm.providers import AnthropicClient, GeminiClient, OpenAIClient


def make(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_provider_defaults_to_anthropic_and_is_case_insensitive():
    assert make().llm_provider is Provider.ANTHROPIC
    assert make(llm_provider=" Gemini ").llm_provider is Provider.GEMINI


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        make(llm_provider="mistral")


@pytest.mark.parametrize(
    ("provider", "triage_model", "resolver_model"),
    [
        ("anthropic", "claude-haiku-4-5", "claude-sonnet-5-5"),
        ("openai", "gpt-5-nano", "gpt-5.1"),
        ("gemini", "gemini-3.5-flash-lite", "gemini-3.8-flash"),
    ],
)
def test_each_provider_has_a_cheap_and_a_strong_default_model(
    provider, triage_model, resolver_model
):
    assert make(llm_provider=provider).models() == (triage_model, resolver_model)


def test_models_can_be_overridden():
    s = make(llm_provider="openai", triage_model="gpt-4o-mini")
    assert s.models() == ("gpt-4o-mini", "gpt-5.1")


@pytest.mark.parametrize(
    ("provider", "key_var"),
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ],
)
def test_live_mode_needs_the_key_for_the_chosen_provider(provider, key_var):
    with pytest.raises(SettingsError, match=key_var):
        make(llm_mode="live", llm_provider=provider).validate_for_runtime()


def test_a_key_for_a_different_provider_does_not_count():
    s = make(llm_mode="live", llm_provider="gemini", anthropic_api_key="sk-ant-x")
    with pytest.raises(SettingsError, match="GEMINI_API_KEY"):
        s.validate_for_runtime()


def test_every_key_is_masked():
    s = make(
        anthropic_api_key="sk-ant-secret1",
        openai_api_key="sk-secret2",
        gemini_api_key="AIza-secret3",
    )
    for secret in ("sk-ant-secret1", "sk-secret2", "AIza-secret3"):
        assert secret not in repr(s) and secret not in str(s)


def test_fake_mode_has_no_live_client():
    assert make_client(make()) is None


@pytest.mark.parametrize(
    ("provider", "key", "cls"),
    [
        ("anthropic", {"anthropic_api_key": "sk-ant-x"}, AnthropicClient),
        ("openai", {"openai_api_key": "sk-x"}, OpenAIClient),
        ("gemini", {"gemini_api_key": "AIza-x"}, GeminiClient),
    ],
)
def test_live_mode_builds_the_chosen_providers_client(provider, key, cls):
    assert isinstance(make_client(make(llm_mode="live", llm_provider=provider, **key)), cls)


def test_factory_refuses_live_mode_without_a_key():
    with pytest.raises(SettingsError):
        make_client(make(llm_mode="live", llm_provider="openai"))


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5-nano", "0.000130"),
        ("gpt-5.1", "0.003250"),
        ("gemini-3.5-flash-lite", "0.000800"),
        ("gemini-3.8-flash", "0.001500"),
    ],
)
def test_new_models_are_priced(model, expected):
    # 1000 input + 200 output tokens, e.g. gemini-3.8-flash: 1000*0.75 + 200*3.75 per 1M
    assert cost_usd(Usage(model, 1000, 200, 1)) == Decimal(expected)


def test_unpriced_model_returns_none_instead_of_crashing_a_live_run():
    assert cost_usd(Usage("some-new-model", 10, 10, 1)) is None


@pytest.mark.parametrize("error", [LLMProviderError("401"), LLMRefused("no")])
def test_triage_sends_any_provider_failure_to_a_human(error):
    result = triage(
        "tarjeta rechazada",
        client=FakeLLM([error]),
        model="m",
        fallback_language="es",
        sleep=lambda s: None,
    )
    assert result.intent is Intent.NEEDS_HUMAN

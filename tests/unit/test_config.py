import pytest

from decline_desk.config import LLMMode, Settings, SettingsError


def make(**overrides) -> Settings:
    # _env_file=None keeps a developer's local .env out of the tests.
    return Settings(_env_file=None, **overrides)


def test_defaults_to_fake_mode_without_a_key(monkeypatch):
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = make()

    assert settings.llm_mode is LLMMode.FAKE
    assert settings.anthropic_api_key is None
    settings.validate_for_runtime()  # fake mode needs no key


def test_replay_mode_needs_no_key():
    make(llm_mode="replay").validate_for_runtime()


def test_live_mode_without_a_key_fails_with_a_clear_message():
    settings = make(llm_mode="live", anthropic_api_key=None)

    with pytest.raises(SettingsError, match="ANTHROPIC_API_KEY"):
        settings.validate_for_runtime()


def test_live_mode_with_a_blank_key_is_treated_as_missing():
    settings = make(llm_mode="live", anthropic_api_key="   ")

    with pytest.raises(SettingsError, match="ANTHROPIC_API_KEY"):
        settings.validate_for_runtime()


def test_live_mode_with_a_key_is_valid():
    make(llm_mode="live", anthropic_api_key="sk-ant-test-123").validate_for_runtime()


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        make(llm_mode="production")


def test_api_key_never_appears_in_repr_or_str():
    secret = "sk-ant-not-a-real-key"
    settings = make(anthropic_api_key=secret)

    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert settings.anthropic_api_key.get_secret_value() == secret


def test_model_names_default_and_can_be_overridden():
    settings = make()
    assert settings.triage_model.startswith("claude-haiku")
    assert settings.resolver_model.startswith("claude-sonnet")

    custom = make(resolver_model="claude-opus-5-5")
    assert custom.resolver_model == "claude-opus-5-5"


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv("TRIAGE_MODEL", "claude-haiku-test")

    settings = make()

    assert settings.llm_mode is LLMMode.REPLAY
    assert settings.triage_model == "claude-haiku-test"


@pytest.mark.parametrize("raw", ["LIVE", "Live", " live "])
def test_mode_is_case_and_space_insensitive(raw):
    assert make(llm_mode=raw, anthropic_api_key="sk-ant-x").llm_mode is LLMMode.LIVE

"""Runtime settings, loaded from environment variables or a local .env file.

Keys are SecretStr, so they never appear in logs, reprs or tracebacks. No key ships with
the project: whoever runs it in live mode creates their own key with the provider they
choose (see the README) and puts it in .env, which is gitignored.
"""

from enum import StrEnum

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMMode(StrEnum):
    FAKE = "fake"  # scripted responses, no network (tests and the bundled demo)
    LIVE = "live"  # real API calls with your own key


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


# (cheap triage model, stronger drafting model) per provider
DEFAULT_MODELS: dict[Provider, tuple[str, str]] = {
    Provider.ANTHROPIC: ("claude-haiku-4-5", "claude-sonnet-5-5"),
    Provider.OPENAI: ("gpt-5-nano", "gpt-5.1"),
    Provider.GEMINI: ("gemini-3.5-flash-lite", "gemini-3.8-flash"),
}

KEY_VARS = {
    Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
    Provider.OPENAI: "OPENAI_API_KEY",
    Provider.GEMINI: "GEMINI_API_KEY",
}


class SettingsError(RuntimeError):
    """Configuration is invalid for the requested mode."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_mode: LLMMode = LLMMode.FAKE
    llm_provider: Provider = Provider.ANTHROPIC
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    triage_model: str | None = None  # overrides the provider's default cheap model
    resolver_model: str | None = None  # overrides the provider's default drafting model
    database_path: str = "declinedesk.sqlite3"

    @field_validator("llm_mode", "llm_provider", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    def models(self) -> tuple[str, str]:
        triage_default, resolver_default = DEFAULT_MODELS[self.llm_provider]
        return self.triage_model or triage_default, self.resolver_model or resolver_default

    def api_key(self) -> str:
        """The key for the chosen provider, stripped; empty if not set."""
        secret = {
            Provider.ANTHROPIC: self.anthropic_api_key,
            Provider.OPENAI: self.openai_api_key,
            Provider.GEMINI: self.gemini_api_key,
        }[self.llm_provider]
        return secret.get_secret_value().strip() if secret else ""

    def validate_for_runtime(self) -> None:
        if self.llm_mode is not LLMMode.LIVE:
            return
        if not self.api_key():
            var = KEY_VARS[self.llm_provider]
            raise SettingsError(
                f"LLM_MODE=live with LLM_PROVIDER={self.llm_provider.value} needs {var}. "
                f"Create a key (see README, 'Use a real model') and set {var} in .env, "
                "or use LLM_MODE=fake to run the scripted demo."
            )

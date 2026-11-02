"""Runtime settings, loaded from environment variables or a local .env file.

The API key is held as a SecretStr so it never shows up in logs, reprs or tracebacks.
Only live mode talks to the Anthropic API, so only live mode requires a key.
"""

from enum import StrEnum

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMMode(StrEnum):
    FAKE = "fake"  # scripted responses, no network (tests)
    REPLAY = "replay"  # recorded responses from evals/recordings (demo, reviewers)
    LIVE = "live"  # real API calls


class SettingsError(RuntimeError):
    """Configuration is invalid for the requested mode."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_mode: LLMMode = LLMMode.FAKE
    anthropic_api_key: SecretStr | None = None
    triage_model: str = "claude-haiku-4-5-20251001"
    resolver_model: str = "claude-sonnet-5-5"
    database_path: str = "declinedesk.sqlite3"

    @field_validator("llm_mode", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    def validate_for_runtime(self) -> None:
        if self.llm_mode is not LLMMode.LIVE:
            return
        key = self.anthropic_api_key.get_secret_value().strip() if self.anthropic_api_key else ""
        if not key:
            raise SettingsError(
                "LLM_MODE=live needs ANTHROPIC_API_KEY. Set it in .env, "
                "or use LLM_MODE=replay to run on recorded responses."
            )

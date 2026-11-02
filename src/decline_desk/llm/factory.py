"""Build the live LLM client for the configured provider, or None in fake mode."""

from decline_desk.config import LLMMode, Provider, Settings
from decline_desk.llm.client import LLMClient
from decline_desk.llm.providers import AnthropicClient, GeminiClient, OpenAIClient

CLIENTS = {
    Provider.ANTHROPIC: AnthropicClient,
    Provider.OPENAI: OpenAIClient,
    Provider.GEMINI: GeminiClient,
}


def make_client(settings: Settings) -> LLMClient | None:
    if settings.llm_mode is LLMMode.FAKE:
        return None
    settings.validate_for_runtime()  # raises SettingsError naming the missing key
    return CLIENTS[settings.llm_provider](api_key=settings.api_key())

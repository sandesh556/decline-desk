"""Adapters from each provider's official SDK to the shared LLMClient interface.

Each adapter sends one system + user turn, returns the text and token usage, and maps the
provider's failures onto the shared error types:

    rate limited                      -> LLMRateLimited   (retried by call_json)
    timeout / network / 5xx           -> LLMTimeout       (retried by call_json)
    refusal                           -> LLMRefused       (template fallback)
    bad key / bad request / not found -> LLMProviderError (template fallback)

The SDKs' own retries are switched off so call_json is the single place that retries.
Error messages carry only the status code, never the provider's text (which can echo
request details).
"""

import time

import anthropic
import httpx
import httpx2
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from decline_desk.llm.client import (
    Completion,
    LLMProviderError,
    LLMRateLimited,
    LLMRefused,
    LLMTimeout,
    Usage,
)

MAX_OUTPUT_TOKENS = 4096
TIMEOUT_SECONDS = 60.0


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class AnthropicClient:
    # Claude Sonnet 5.5 gets low effort (short JSON replies) and server-side fallbacks, so a
    # refusal is retried on another model inside the same call.
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, *, api_key: str | None = None, sdk=None) -> None:
        self.sdk = sdk or anthropic.Anthropic(
            api_key=api_key, max_retries=0, timeout=TIMEOUT_SECONDS
        )

    def complete(self, *, model: str, system: str, user: str) -> Completion:
        request = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        start = time.perf_counter()
        try:
            if model.startswith("claude-sonnet-5-5"):
                message = self.sdk.beta.messages.create(
                    **request,
                    output_config={"effort": "low"},
                    betas=[self.FALLBACK_BETA],
                    fallbacks="default",
                )
            else:
                message = self.sdk.messages.create(**request)
        except anthropic.RateLimitError as exc:
            raise LLMRateLimited("anthropic 429") from exc
        except (anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise LLMTimeout("anthropic timeout/unavailable") from exc
        except anthropic.APIStatusError as exc:
            raise LLMProviderError(f"anthropic error {exc.status_code}") from exc

        if message.stop_reason == "refusal":
            raise LLMRefused("anthropic refusal")
        text = "".join(b.text for b in message.content if b.type == "text")
        usage = Usage(model, message.usage.input_tokens, message.usage.output_tokens, _ms(start))
        return Completion(text, usage)


class OpenAIClient:
    def __init__(self, *, api_key: str | None = None, sdk=None) -> None:
        self.sdk = sdk or openai.OpenAI(api_key=api_key, max_retries=0, timeout=TIMEOUT_SECONDS)

    def complete(self, *, model: str, system: str, user: str) -> Completion:
        start = time.perf_counter()
        try:
            response = self.sdk.responses.create(
                model=model,
                instructions=system,
                input=user,
                text={"format": {"type": "json_object"}},
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except openai.RateLimitError as exc:
            raise LLMRateLimited("openai 429") from exc
        except (openai.APIConnectionError, openai.InternalServerError) as exc:
            raise LLMTimeout("openai timeout/unavailable") from exc
        except openai.APIStatusError as exc:
            raise LLMProviderError(f"openai error {exc.status_code}") from exc
        usage = Usage(model, response.usage.input_tokens, response.usage.output_tokens, _ms(start))
        return Completion(response.output_text or "", usage)


class GeminiClient:
    def __init__(self, *, api_key: str | None = None, sdk=None) -> None:
        self.sdk = sdk or genai.Client(api_key=api_key)

    def complete(self, *, model: str, system: str, user: str) -> Completion:
        start = time.perf_counter()
        try:
            response = self.sdk.models.generate_content(
                model=model,
                contents=user,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise LLMRateLimited("gemini 429") from exc
            raise LLMProviderError(f"gemini error {exc.code}") from exc
        except genai_errors.ServerError as exc:
            raise LLMTimeout("gemini unavailable") from exc
        except (httpx.TransportError, httpx2.TransportError) as exc:  # either HTTP stack
            raise LLMTimeout("gemini timeout/network") from exc
        meta = response.usage_metadata
        usage = Usage(
            model,
            (meta.prompt_token_count or 0) if meta else 0,
            (meta.candidates_token_count or 0) if meta else 0,
            _ms(start),
        )
        return Completion(response.text or "", usage)

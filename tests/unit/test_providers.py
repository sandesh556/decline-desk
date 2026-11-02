"""Provider adapters (Anthropic, OpenAI, Gemini) against stubbed SDK clients.

No network and no keys: each adapter is fed a fake SDK object that records the request and
returns a canned response or raises the SDK's own exception class. Every provider must map
its failures onto the same LLM error types, so the retry/repair/fallback logic is shared.
"""

from types import SimpleNamespace as NS

import anthropic
import httpx2
import openai
import pytest
from google.genai import errors as gerr

from decline_desk.llm.client import (
    LLMProviderError,
    LLMRateLimited,
    LLMRefused,
    LLMTimeout,
)
from decline_desk.llm.providers import AnthropicClient, GeminiClient, OpenAIClient

REQ = httpx2.Request("POST", "https://api.example.com/v1")


def resp(code: int) -> httpx2.Response:
    return httpx2.Response(code, request=REQ)


class Recorder:
    """A callable that records kwargs and returns or raises the next scripted outcome."""

    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


# --- Anthropic ---------------------------------------------------------------------------------


def claude_message(text='{"ok": true}', stop="end_turn"):
    return NS(
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
        usage=NS(input_tokens=321, output_tokens=45),
        stop_reason=stop,
    )


def anthropic_sdk(outcome):
    plain, beta = Recorder(outcome), Recorder(outcome)
    return NS(messages=NS(create=plain), beta=NS(messages=NS(create=beta))), plain, beta


def test_anthropic_haiku_uses_the_plain_endpoint_and_returns_text_and_usage():
    sdk, plain, beta = anthropic_sdk(claude_message())
    out = AnthropicClient(sdk=sdk).complete(model="claude-haiku-4-5", system="S", user="U")
    assert out.text == '{"ok": true}'  # thinking blocks are skipped
    assert (out.usage.model, out.usage.input_tokens, out.usage.output_tokens) == (
        "claude-haiku-4-5",
        321,
        45,
    )
    [call] = plain.calls
    assert call["system"] == "S" and call["messages"] == [{"role": "user", "content": "U"}]
    assert "output_config" not in call  # effort is not supported on Haiku 4.5
    assert beta.calls == []


def test_anthropic_sonnet_uses_low_effort_and_server_side_fallbacks():
    sdk, plain, beta = anthropic_sdk(claude_message())
    AnthropicClient(sdk=sdk).complete(model="claude-sonnet-5-5", system="S", user="U")
    [call] = beta.calls
    assert call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert plain.calls == []


@pytest.mark.parametrize(
    ("error", "mapped"),
    [
        (anthropic.RateLimitError("slow down", response=resp(429), body=None), LLMRateLimited),
        (anthropic.APITimeoutError(request=REQ), LLMTimeout),
        (anthropic.APIConnectionError(request=REQ), LLMTimeout),
        (anthropic.InternalServerError("boom", response=resp(500), body=None), LLMTimeout),
        (anthropic.AuthenticationError("bad key", response=resp(401), body=None), LLMProviderError),
        (anthropic.BadRequestError("bad request", response=resp(400), body=None), LLMProviderError),
    ],
)
def test_anthropic_errors_map_to_shared_types(error, mapped):
    sdk, _, _ = anthropic_sdk(error)
    with pytest.raises(mapped):
        AnthropicClient(sdk=sdk).complete(model="claude-haiku-4-5", system="S", user="U")


def test_anthropic_refusal_is_reported_not_parsed():
    sdk, _, _ = anthropic_sdk(claude_message(text="", stop="refusal"))
    with pytest.raises(LLMRefused):
        AnthropicClient(sdk=sdk).complete(model="claude-sonnet-5-5", system="S", user="U")


def test_anthropic_default_sdk_disables_its_own_retries():
    client = AnthropicClient(api_key="sk-ant-test")
    assert client.sdk.max_retries == 0  # call_json owns retries; no double backoff


# --- OpenAI ------------------------------------------------------------------------------------


def openai_sdk(outcome):
    create = Recorder(outcome)
    return NS(responses=NS(create=create)), create


def openai_response(text='{"ok": true}'):
    return NS(output_text=text, usage=NS(input_tokens=210, output_tokens=33))


def test_openai_uses_the_responses_api_in_json_mode():
    sdk, create = openai_sdk(openai_response())
    out = OpenAIClient(sdk=sdk).complete(model="gpt-5-nano", system="S", user="U")
    assert out.text == '{"ok": true}'
    assert (out.usage.input_tokens, out.usage.output_tokens) == (210, 33)
    [call] = create.calls
    assert call["instructions"] == "S" and call["input"] == "U"
    assert call["text"] == {"format": {"type": "json_object"}}


@pytest.mark.parametrize(
    ("error", "mapped"),
    [
        (openai.RateLimitError("slow down", response=resp(429), body=None), LLMRateLimited),
        (openai.APITimeoutError(request=REQ), LLMTimeout),
        (openai.InternalServerError("boom", response=resp(503), body=None), LLMTimeout),
        (openai.AuthenticationError("bad key", response=resp(401), body=None), LLMProviderError),
    ],
)
def test_openai_errors_map_to_shared_types(error, mapped):
    sdk, _ = openai_sdk(error)
    with pytest.raises(mapped):
        OpenAIClient(sdk=sdk).complete(model="gpt-5-nano", system="S", user="U")


def test_openai_default_sdk_disables_its_own_retries():
    assert OpenAIClient(api_key="sk-test").sdk.max_retries == 0


# --- Gemini ------------------------------------------------------------------------------------


def gemini_sdk(outcome):
    generate = Recorder(outcome)
    return NS(models=NS(generate_content=generate)), generate


def gemini_response(text='{"ok": true}', prompt=150, candidates=20):
    return NS(
        text=text, usage_metadata=NS(prompt_token_count=prompt, candidates_token_count=candidates)
    )


def test_gemini_requests_json_with_a_system_instruction():
    sdk, generate = gemini_sdk(gemini_response())
    out = GeminiClient(sdk=sdk).complete(model="gemini-3.8-flash", system="S", user="U")
    assert out.text == '{"ok": true}'
    assert (out.usage.input_tokens, out.usage.output_tokens) == (150, 20)
    [call] = generate.calls
    assert call["model"] == "gemini-3.8-flash" and call["contents"] == "U"
    assert call["config"].system_instruction == "S"
    assert call["config"].response_mime_type == "application/json"


def test_gemini_missing_text_or_usage_does_not_crash():
    sdk, _ = gemini_sdk(NS(text=None, usage_metadata=None))
    out = GeminiClient(sdk=sdk).complete(model="gemini-3.8-flash", system="S", user="U")
    assert out.text == "" and out.usage.input_tokens == 0


@pytest.mark.parametrize(
    ("error", "mapped"),
    [
        (gerr.ClientError(429, {"error": {"message": "quota"}}), LLMRateLimited),
        (gerr.ServerError(503, {"error": {"message": "overloaded"}}), LLMTimeout),
        (gerr.ClientError(400, {"error": {"message": "bad"}}), LLMProviderError),
        (gerr.ClientError(403, {"error": {"message": "key"}}), LLMProviderError),
        (httpx2.ReadTimeout("slow", request=REQ), LLMTimeout),
    ],
)
def test_gemini_errors_map_to_shared_types(error, mapped):
    sdk, _ = gemini_sdk(error)
    with pytest.raises(mapped):
        GeminiClient(sdk=sdk).complete(model="gemini-3.8-flash", system="S", user="U")


# --- shared contract -----------------------------------------------------------------------------


def test_error_messages_never_contain_the_api_key():
    key = "sk-ant-THIS-SHOULD-NOT-LEAK"
    err = anthropic.AuthenticationError(f"invalid x-api-key {key}", response=resp(401), body=None)
    sdk, _, _ = anthropic_sdk(err)
    with pytest.raises(LLMProviderError) as info:
        AnthropicClient(sdk=sdk).complete(model="claude-haiku-4-5", system="S", user="U")
    assert key not in str(info.value)


def test_gemini_network_errors_from_either_http_stack_are_transient():
    import httpx

    sdk, _ = gemini_sdk(httpx.ConnectError("refused", request=httpx.Request("POST", "https://x")))
    with pytest.raises(LLMTimeout):
        GeminiClient(sdk=sdk).complete(model="gemini-3.8-flash", system="S", user="U")

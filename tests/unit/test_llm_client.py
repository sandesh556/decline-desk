"""LLM client layer: a scripted FakeLLM for tests, and a call wrapper that retries
transient failures, repairs bad JSON once, and then gives up cleanly instead of crashing."""

import pytest
from pydantic import BaseModel

from decline_desk.llm.client import (
    Completion,
    FakeLLM,
    LLMBadOutput,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    Usage,
    call_json,
)


class Answer(BaseModel):
    language: str
    intent: str


def usage(model="claude-haiku-test"):
    return Usage(model=model, input_tokens=120, output_tokens=30, latency_ms=250)


def ok(data):
    return Completion(text=data, usage=usage())


def run(fake, **kw):
    return call_json(
        fake,
        model="claude-haiku-test",
        system="sys",
        user="hello",
        schema=Answer,
        sleep=lambda s: None,
        **kw,
    )


def test_returns_validated_model_and_usage():
    fake = FakeLLM([ok('{"language": "es", "intent": "card_decline"}')])
    result, usages = run(fake)
    assert result == Answer(language="es", intent="card_decline")
    assert [u.input_tokens for u in usages] == [120]


def test_records_every_call_for_inspection():
    fake = FakeLLM([ok('{"language": "es", "intent": "x"}')])
    run(fake)
    [call] = fake.calls
    assert call.model == "claude-haiku-test"
    assert call.user == "hello"


def test_json_inside_a_code_fence_is_accepted():
    fake = FakeLLM([ok('```json\n{"language": "pt", "intent": "card_decline"}\n```')])
    result, _ = run(fake)
    assert result.language == "pt"


def test_bad_json_gets_exactly_one_repair_attempt_with_the_error():
    fake = FakeLLM([ok("not json at all"), ok('{"language": "en", "intent": "other"}')])
    result, usages = run(fake)
    assert result.language == "en"
    assert len(fake.calls) == 2
    assert "not valid" in fake.calls[1].user.lower()
    assert len(usages) == 2  # the failed attempt still cost tokens


def test_schema_violation_also_gets_one_repair():
    fake = FakeLLM([ok('{"language": "es"}'), ok('{"language": "es", "intent": "x"}')])
    result, _ = run(fake)
    assert result.intent == "x"


def test_second_bad_output_raises_bad_output():
    fake = FakeLLM([ok("nope"), ok("still nope")])
    with pytest.raises(LLMBadOutput):
        run(fake)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("error", [LLMRateLimited("429"), LLMTimeout("slow")])
def test_transient_errors_are_retried_with_backoff(error):
    sleeps = []
    fake = FakeLLM([error, error, ok('{"language": "es", "intent": "x"}')])
    result, _ = call_json(fake, model="m", system="s", user="u", schema=Answer, sleep=sleeps.append)
    assert result.language == "es"
    assert sleeps == [1.0, 2.0]  # exponential backoff, bounded


def test_gives_up_after_two_retries():
    fake = FakeLLM([LLMRateLimited("429")] * 5)
    with pytest.raises(LLMUnavailable):
        run(fake)
    assert len(fake.calls) == 3  # first try + 2 retries, never unbounded


def test_unexpected_exceptions_are_not_swallowed():
    fake = FakeLLM([ValueError("bug in our code")])
    with pytest.raises(ValueError):
        run(fake)


def test_fake_fails_loudly_when_script_runs_out():
    fake = FakeLLM([])
    with pytest.raises(AssertionError, match="script"):
        run(fake)

"""LLM access behind one small interface.

Every model call goes through call_json(), which:
- retries rate limits and timeouts with bounded exponential backoff (at most 2 retries),
- asks the model once to repair output that isn't valid JSON for the schema,
- then raises LLMUnavailable or LLMBadOutput, so the caller can escalate the ticket to a
  human instead of losing it.

FakeLLM plays back a script for tests. The Anthropic and replay clients implement the
same complete() method.
"""

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError

MAX_RETRIES = 2
BACKOFF_SECONDS = (1.0, 2.0)


class LLMError(Exception):
    """Base class for model-call failures the caller should handle."""


class LLMRateLimited(LLMError):
    pass


class LLMTimeout(LLMError):
    pass


class LLMProviderError(LLMError):
    """Non-retryable provider failure (bad key, bad request, model not found)."""


class LLMRefused(LLMError):
    """The model declined to answer (e.g. a safety refusal)."""


class LLMUnavailable(LLMError):
    """Transient failures persisted past the retry budget."""


class LLMBadOutput(LLMError):
    """The model did not produce valid output, even after one repair attempt."""


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


@dataclass(frozen=True)
class Call:
    model: str
    system: str
    user: str


class LLMClient(Protocol):
    def complete(self, *, model: str, system: str, user: str) -> Completion: ...


@dataclass
class FakeLLM:
    """Plays back a script of completions or exceptions, in order, and records each call."""

    script: Sequence[Completion | Exception]
    calls: list[Call] = field(default_factory=list)

    def complete(self, *, model: str, system: str, user: str) -> Completion:
        self.calls.append(Call(model, system, user))
        assert len(self.calls) <= len(self.script), "FakeLLM script exhausted"
        step = self.script[len(self.calls) - 1]
        if isinstance(step, Exception):
            raise step
        return step


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def call_json[T: BaseModel](
    client: LLMClient,
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, list[Usage]]:
    usages: list[Usage] = []
    prompt = user
    for attempt in range(2):  # first answer + one repair
        completion = _complete_with_retries(client, model, system, prompt, sleep)
        usages.append(completion.usage)
        try:
            return schema.model_validate(
                json.loads(_FENCE.sub("", completion.text.strip()))
            ), usages
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 1:
                raise LLMBadOutput(f"invalid output after repair: {type(exc).__name__}") from exc
            prompt = (
                f"{user}\n\nYour previous reply was not valid for the required JSON schema "
                f"({type(exc).__name__}). Reply again with only the JSON object."
            )
    raise AssertionError("unreachable")  # pragma: no cover


def _complete_with_retries(
    client: LLMClient, model: str, system: str, user: str, sleep: Callable[[float], None]
) -> Completion:
    for retry in range(MAX_RETRIES + 1):
        try:
            return client.complete(model=model, system=system, user=user)
        except (LLMRateLimited, LLMTimeout) as exc:
            if retry == MAX_RETRIES:
                raise LLMUnavailable(f"model unavailable after {retry} retries") from exc
            sleep(BACKOFF_SECONDS[retry])
    raise AssertionError("unreachable")  # pragma: no cover

from decimal import Decimal

import pytest

from decline_desk.config import Settings
from decline_desk.llm.client import Usage
from decline_desk.llm.pricing import UnknownModel, cost_usd


def test_haiku_cost_per_call():
    # 1,000,000 in at $1 + 200,000 out at $5 = $1 + $1
    assert cost_usd(Usage("claude-haiku-4-5", 1_000_000, 200_000, 10)) == Decimal("2.000000")


def test_sonnet_cost_per_call():
    assert cost_usd(Usage("claude-sonnet-5-5", 3000, 400, 10)) == Decimal("0.010000")


def test_cost_is_rounded_to_a_millionth_of_a_dollar():
    assert cost_usd(Usage("claude-haiku-4-5", 1, 1, 1)) == Decimal("0.000006")


def test_zero_tokens_cost_nothing():
    assert cost_usd(Usage("claude-sonnet-5-5", 0, 0, 0)) == Decimal("0")


def test_fake_and_replay_models_are_free():
    assert cost_usd(Usage("fake-haiku", 500, 100, 1)) == Decimal("0")


def test_unknown_real_model_is_an_error_not_a_silent_zero():
    with pytest.raises(UnknownModel):
        cost_usd(Usage("claude-unknown-9", 10, 10, 1))


def test_default_models_are_priced_and_have_no_date_suffix():
    s = Settings(_env_file=None)
    for model in (s.triage_model, s.resolver_model):
        cost_usd(Usage(model, 1, 1, 1))
        assert not model[-8:].isdigit()

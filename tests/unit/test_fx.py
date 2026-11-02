from datetime import UTC, datetime
from decimal import Decimal

import pytest

from decline_desk.domain.models import Authorization, Currency
from decline_desk.fx import FXError, to_usd


@pytest.mark.parametrize(
    ("amount", "currency", "rate", "expected"),
    [
        (Decimal("185000"), Currency.COP, Decimal("0.00025"), Decimal("46.25")),
        (Decimal("1999.99"), Currency.MXN, Decimal("0.055"), Decimal("110.00")),
        (Decimal("250"), Currency.BRL, Decimal("0.18"), Decimal("45.00")),
        (Decimal("12.34"), Currency.USD, Decimal("1"), Decimal("12.34")),
    ],
)
def test_converts_to_usd_with_decimal_precision(amount, currency, rate, expected):
    assert to_usd(amount, currency, rate) == expected


def test_rounds_half_up_to_cents():
    # 0.125 rounds up to 0.13, not banker's rounding to 0.12
    assert to_usd(Decimal("1"), Currency.BRL, Decimal("0.125")) == Decimal("0.13")


def test_never_uses_binary_floats():
    with pytest.raises(TypeError):
        to_usd(100.0, Currency.COP, Decimal("0.00025"))


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-0.1"), None])
def test_zero_negative_or_missing_rate_is_an_error(rate):
    with pytest.raises(FXError):
        to_usd(Decimal("100"), Currency.COP, rate)


def test_usd_must_have_rate_one():
    with pytest.raises(FXError):
        to_usd(Decimal("100"), Currency.USD, Decimal("0.9"))


def test_authorization_uses_the_rate_captured_at_auth_time():
    a = Authorization(
        id="auth_1",
        card_id="card_1",
        created_at=datetime(2026, 10, 28, 15, 30, tzinfo=UTC),
        amount=Decimal("400000"),
        currency="COP",
        usd_per_unit=Decimal("0.00024"),  # yesterday's rate, not today's
        merchant_name="Hotel Andino",
        mcc="7011",
        country="CO",
        response_code="61",
    )
    assert a.amount_usd == Decimal("96.00")


@pytest.mark.parametrize("rate", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_rates_are_rejected(rate):
    with pytest.raises(FXError):
        to_usd(Decimal("100"), Currency.COP, rate)


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_amounts_are_rejected(amount):
    with pytest.raises(FXError):
        to_usd(amount, Currency.COP, Decimal("0.00025"))


def test_int_amount_is_rejected_like_float():
    with pytest.raises(TypeError):
        to_usd(100, Currency.COP, Decimal("0.00025"))


def test_large_cop_amounts_stay_exact():
    assert to_usd(Decimal("987654321"), Currency.COP, Decimal("0.00025")) == Decimal("246913.58")


def test_tiny_amount_rounds_to_zero_cents_without_error():
    assert to_usd(Decimal("1"), Currency.COP, Decimal("0.00025")) == Decimal("0.00")

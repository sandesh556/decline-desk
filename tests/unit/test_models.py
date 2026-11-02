from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardStatus,
    Currency,
    ResponseCode,
)


def auth(**overrides) -> Authorization:
    data = {
        "id": "auth_1",
        "card_id": "card_1",
        "created_at": datetime(2026, 10, 28, 15, 30, tzinfo=UTC),
        "amount": Decimal("185000"),
        "currency": "COP",
        "usd_per_unit": Decimal("0.00025"),
        "merchant_name": "Uber Trip",
        "mcc": "4121",
        "country": "CO",
        "response_code": "62",
    }
    data.update(overrides)
    return Authorization(**data)


def test_valid_authorization_parses():
    a = auth()
    assert a.currency is Currency.COP
    assert a.response_code is ResponseCode.RESTRICTED_CARD
    assert a.amount == Decimal("185000")


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1"), Decimal("-0.01")])
def test_non_positive_amount_is_rejected(amount):
    with pytest.raises(ValidationError):
        auth(amount=amount)


def test_unsupported_currency_is_rejected():
    with pytest.raises(ValidationError):
        auth(currency="EUR")


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        auth(created_at=datetime(2026, 10, 28, 15, 30))


def test_aware_datetime_is_normalised_to_utc():
    from zoneinfo import ZoneInfo

    bogota = datetime(2026, 10, 28, 10, 30, tzinfo=ZoneInfo("America/Bogota"))
    assert auth(created_at=bogota).created_at == datetime(2026, 10, 28, 15, 30, tzinfo=UTC)


def test_unknown_response_code_maps_to_unknown_instead_of_crashing():
    assert auth(response_code="Q9").response_code is ResponseCode.UNKNOWN


def test_approved_is_not_a_decline():
    assert auth(response_code="00").is_declined is False
    assert auth(response_code="51").is_declined is True


@pytest.mark.parametrize("mcc", ["412", "41211", "ABCD", ""])
def test_mcc_must_be_four_digits(mcc):
    with pytest.raises(ValidationError):
        auth(mcc=mcc)


@pytest.mark.parametrize("country", ["co", "COL", "C"])
def test_country_must_be_iso_alpha2_uppercase(country):
    with pytest.raises(ValidationError):
        auth(country=country)


def test_models_are_immutable():
    a = auth()
    with pytest.raises(ValidationError):
        a.amount = Decimal("1")


def test_card_keeps_only_last4_and_rejects_full_pan():
    card = Card(
        id="card_1",
        employee_id="emp_1",
        company_id="co_1",
        last4="4242",
        status="active",
        expires=date(2028, 1, 31),
    )
    assert card.status is CardStatus.ACTIVE

    with pytest.raises(ValidationError):
        Card(
            id="card_2",
            employee_id="emp_1",
            company_id="co_1",
            last4="4111111111111111",
            status="active",
            expires=date(2028, 1, 31),
        )

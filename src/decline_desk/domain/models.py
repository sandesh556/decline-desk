"""Core domain models. Immutable, validated at the boundary, money as Decimal, time as UTC."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


class Currency(StrEnum):
    USD = "USD"
    COP = "COP"
    MXN = "MXN"
    BRL = "BRL"


class ResponseCode(StrEnum):
    """ISO 8583-style authorization response codes used by the card processor."""

    APPROVED = "00"
    INSUFFICIENT_FUNDS = "51"
    EXPIRED_CARD = "54"
    NOT_PERMITTED = "57"  # merchant category blocked by card controls
    SUSPECTED_FRAUD = "59"
    LIMIT_EXCEEDED = "61"
    RESTRICTED_CARD = "62"  # country / region restriction
    CARD_BLOCKED = "78"  # card frozen
    AUTH_REQUIRED = "1A"  # 3-D Secure / strong customer authentication failed
    CVV_MISMATCH = "N7"
    UNKNOWN = "??"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]
PositiveMoney = Annotated[Decimal, Field(gt=0)]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Authorization(Frozen):
    id: str
    card_id: str
    created_at: UtcDatetime
    amount: PositiveMoney
    currency: Currency
    usd_per_unit: PositiveMoney  # FX rate captured at authorization time
    merchant_name: str
    mcc: str = Field(pattern=r"^\d{4}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    response_code: ResponseCode

    @field_validator("response_code", mode="before")
    @classmethod
    def _unknown_codes_are_not_errors(cls, value: object) -> object:
        known = {code.value for code in ResponseCode}
        return value if value in known or isinstance(value, ResponseCode) else ResponseCode.UNKNOWN

    @property
    def is_declined(self) -> bool:
        return self.response_code is not ResponseCode.APPROVED


class CardStatus(StrEnum):
    ACTIVE = "active"
    FROZEN_BY_USER = "frozen_by_user"
    FROZEN_FRAUD = "frozen_fraud"


class Card(Frozen):
    id: str
    employee_id: str
    company_id: str
    last4: str = Field(pattern=r"^\d{4}$")  # never store the full PAN
    status: CardStatus
    expires: date

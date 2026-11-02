"""Core domain models. Immutable, validated at the boundary, money as Decimal, time as UTC."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


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

    @property
    def amount_usd(self) -> Decimal:
        from decline_desk.fx import to_usd  # local import: fx depends on this module

        return to_usd(self.amount, self.currency, self.usd_per_unit)


def _iana_timezone(value: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {value}") from exc
    return value


NonNegativeMoney = Annotated[Decimal, Field(ge=0)]


class Company(Frozen):
    id: str
    name: str
    timezone: Annotated[str, AfterValidator(_iana_timezone)]  # defines the "day" for limits
    wallet_balance_usd: NonNegativeMoney  # settled stablecoin balance backing the cards
    limit_cap_usd: PositiveMoney  # most an admin may raise any card limit to


class Employee(Frozen):
    id: str
    company_id: str
    name: str
    language: Literal["es", "pt", "en"]
    is_admin: bool = False


def _country_codes(values: frozenset[str]) -> frozenset[str]:
    return frozenset(v.upper() for v in values)


class CardControls(Frozen):
    card_id: str
    per_txn_limit_usd: PositiveMoney
    daily_limit_usd: PositiveMoney
    blocked_mccs: frozenset[str]
    allowed_countries: Annotated[frozenset[str], AfterValidator(_country_codes)]

    @model_validator(mode="after")
    def _daily_covers_single(self) -> "CardControls":
        if self.daily_limit_usd < self.per_txn_limit_usd:
            raise ValueError("daily limit must be at least the per-transaction limit")
        return self


class PendingTopup(Frozen):
    id: str
    company_id: str
    amount_usd: PositiveMoney
    asset: str  # e.g. USDC
    eta: UtcDatetime  # expected on-chain settlement time


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

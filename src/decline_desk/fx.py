"""Currency conversion. Decimal only, never float, rounded half-up to cents.

Card limits are set in USD while LatAm employees spend in COP, MXN and BRL, so every
limit check runs on the USD amount at the rate captured when the card was authorized.
"""

from decimal import ROUND_HALF_UP, Decimal

from decline_desk.domain.models import Currency

CENT = Decimal("0.01")


class FXError(ValueError):
    """The conversion rate is missing or invalid."""


def to_usd(amount: Decimal, currency: Currency, usd_per_unit: Decimal | None) -> Decimal:
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal, never a float")
    if usd_per_unit is None or usd_per_unit <= 0:
        raise FXError(f"invalid FX rate for {currency}: {usd_per_unit}")
    if currency is Currency.USD and usd_per_unit != 1:
        raise FXError("USD must convert at rate 1")
    return (amount * usd_per_unit).quantize(CENT, rounding=ROUND_HALF_UP)

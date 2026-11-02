"""Per-call cost in USD, so every ticket's trace shows what the AI work cost.

Prices are USD per million tokens (Anthropic first-party API rates). Update this table
when prices change; an unknown real model raises instead of silently costing $0.
"""

from decimal import ROUND_HALF_UP, Decimal

from decline_desk.llm.client import Usage

PER_MILLION = Decimal(1_000_000)
MICRO = Decimal("0.000001")

PRICES: dict[str, tuple[Decimal, Decimal]] = {  # model: (input, output) per 1M tokens
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
}


class UnknownModel(KeyError):
    """No price is known for this model."""


def cost_usd(usage: Usage) -> Decimal:
    if usage.model.startswith(("fake-", "replay-")):
        return Decimal("0")
    try:
        price_in, price_out = PRICES[usage.model]
    except KeyError:
        raise UnknownModel(usage.model) from None
    raw = (usage.input_tokens * price_in + usage.output_tokens * price_out) / PER_MILLION
    return raw.quantize(MICRO, rounding=ROUND_HALF_UP)

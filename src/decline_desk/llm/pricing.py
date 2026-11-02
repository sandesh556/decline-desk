"""Per-call cost in USD, so every ticket's trace shows what the AI work cost.

Prices are USD per million tokens at each provider's standard API rates (checked
2026-11). A model missing from the table returns None, so a live run with a newer model
shows "unpriced" instead of crashing.
"""

from decimal import ROUND_HALF_UP, Decimal

from decline_desk.llm.client import Usage

PER_MILLION = Decimal(1_000_000)
MICRO = Decimal("0.000001")

PRICES: dict[str, tuple[Decimal, Decimal]] = {  # model: (input, output) per 1M tokens
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "gpt-5-nano": (Decimal("0.05"), Decimal("0.40")),
    "gpt-5.1": (Decimal("1.25"), Decimal("10.00")),
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
    "gemini-3.8-flash": (Decimal("0.75"), Decimal("3.75")),
}


def cost_usd(usage: Usage) -> Decimal | None:
    if usage.model.startswith(("fake-", "replay-")):
        return Decimal("0")
    if usage.model not in PRICES:
        return None
    price_in, price_out = PRICES[usage.model]
    raw = (usage.input_tokens * price_in + usage.output_tokens * price_out) / PER_MILLION
    return raw.quantize(MICRO, rounding=ROUND_HALF_UP)

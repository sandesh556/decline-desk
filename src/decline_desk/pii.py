"""Mask personal data before it reaches an LLM or a log line.

Card numbers are masked only when they pass the Luhn check, so amounts, ticket ids and
order numbers survive. The agent needs those to find the right transaction.
"""

import re

# 13-19 digits, optionally grouped with single spaces or dashes.
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# International numbers only (leading +), so plain amounts are never treated as phones.
_PHONE = re.compile(r"\+\d{1,3}(?:[ -]?\d{2,5}){2,4}(?!\d)")


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _mask_card(match: re.Match[str]) -> str:
    if _is_card(match.group()):
        return f"**** {re.sub(r'[ -]', '', match.group())[-4:]}"
    return match.group()


def contains_card_number(text: str) -> bool:
    return any(_is_card(m.group()) for m in _CARD.finditer(text))


def _is_card(candidate: str) -> bool:
    digits = re.sub(r"[ -]", "", candidate)
    return 13 <= len(digits) <= 19 and luhn_valid(digits)


def mask_pii(text: str) -> str:
    text = _CARD.sub(_mask_card, text)
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)

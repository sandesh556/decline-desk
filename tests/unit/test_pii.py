import pytest

from decline_desk.pii import luhn_valid, mask_pii

# 4111 1111 1111 1111 and 5555 5555 5555 4444 are standard Luhn-valid test card numbers.


@pytest.mark.parametrize(
    "text",
    [
        "card 4111111111111111 declined",
        "card 4111 1111 1111 1111 declined",
        "card 4111-1111-1111-1111 declined",
    ],
)
def test_masks_card_numbers_in_common_formats(text):
    assert mask_pii(text) == "card **** 1111 declined"


def test_masks_multiple_cards():
    out = mask_pii("4111111111111111 and 5555555555554444")
    assert out == "**** 1111 and **** 4444"


@pytest.mark.parametrize(
    "text",
    [
        "me cobraron 1500000 COP",  # an amount
        "ticket 1234567890123456 is open",  # 16 digits but not Luhn-valid
        "order 2026102800001",  # 13-digit id, not Luhn-valid
    ],
)
def test_leaves_numbers_that_are_not_card_numbers(text):
    assert mask_pii(text) == text


def test_masks_email_addresses():
    assert mask_pii("write to maria.gomez@andes.co please") == "write to [email] please"


@pytest.mark.parametrize("phone", ["+57 300 123 4567", "+55 11 98765-4321", "+52 55 1234 5678"])
def test_masks_international_phone_numbers(phone):
    assert mask_pii(f"call {phone} now") == "call [phone] now"


def test_is_idempotent():
    once = mask_pii("4111 1111 1111 1111, maria@andes.co, +57 300 123 4567")
    assert mask_pii(once) == once


@pytest.mark.parametrize("text", ["", "   ", "no personal data here"])
def test_handles_empty_and_clean_text(text):
    assert mask_pii(text) == text


def test_keeps_non_ascii_text_intact():
    text = "mi tarjeta fue rechazada en Bogotá ayer 😕"
    assert mask_pii(text) == text


@pytest.mark.parametrize(
    ("digits", "valid"),
    [("4111111111111111", True), ("4111111111111112", False), ("79927398713", True)],
)
def test_luhn_check(digits, valid):
    assert luhn_valid(digits) is valid

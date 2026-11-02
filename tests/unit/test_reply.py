"""Reply templates (es/pt/en) and the reply checker that every draft must pass."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from decline_desk.agent.reply import (
    LANGUAGES,
    TEMPLATES,
    clarifying_question,
    render,
    verify_reply,
)
from decline_desk.domain.models import Authorization
from decline_desk.rules.diagnose import Cause


def test_every_template_exists_in_every_language():
    keys = set(TEMPLATES["en"])
    for lang in LANGUAGES:
        assert set(TEMPLATES[lang]) == keys, lang


def test_every_cause_has_a_template():
    for cause in Cause:
        if cause in (
            Cause.INCONSISTENT,
            Cause.UNKNOWN,
            Cause.SUSPECTED_FRAUD,
            Cause.CARD_FROZEN_FRAUD,
        ):
            continue  # these go to the generic escalation templates
        assert cause.value in TEMPLATES["en"], cause


def test_render_fills_placeholders():
    text = render("es", "funds_pending_settlement", eta="17:30")
    assert "17:30" in text
    assert "{" not in text


def test_render_missing_placeholder_is_an_error_not_a_broken_reply():
    with pytest.raises(KeyError):
        render("es", "funds_pending_settlement")


@pytest.mark.parametrize("lang", LANGUAGES)
def test_fraud_escalation_reveals_nothing_about_how_it_was_decided(lang):
    text = render(lang, "escalated_security").lower()
    for leak in ("score", "rule", "regla", "regra", "risk", "riesgo", "risco", "59", "model"):
        assert leak not in text


def auth(id_, merchant, amount, when):
    return Authorization(
        id=id_,
        card_id="c",
        created_at=when,
        amount=Decimal(amount),
        currency="BRL",
        usd_per_unit=Decimal("0.18"),
        merchant_name=merchant,
        mcc="5732",
        country="BR",
        response_code="61",
    )


def test_clarifying_question_lists_candidates_in_local_time_and_language():
    cands = [
        auth("a", "Kabum Informatica", "1450", datetime(2026, 10, 28, 10, 0, tzinfo=UTC)),
        auth("b", "Gift Card Store", "89", datetime(2026, 10, 27, 9, 0, tzinfo=UTC)),
    ]
    text = clarifying_question("pt", cands, timezone="America/Sao_Paulo")
    assert "Kabum Informatica" in text and "Gift Card Store" in text
    assert "28/10 07:00" in text  # 10:00 UTC shown as 07:00 Sao Paulo
    assert "1450 BRL" in text
    assert text.startswith(TEMPLATES["pt"]["clarify"].split("\n")[0][:10])


# --- the checker --------------------------------------------------------------------------


def test_a_clean_reply_passes():
    assert (
        verify_reply(
            "Tu tarjeta solo está habilitada en Colombia. Pedimos a tu admin "
            "que la habilite en México por 24 horas."
        )
        == []
    )


def test_a_card_number_in_the_reply_is_refused():
    assert "card_number" in verify_reply("Tu tarjeta 4111 1111 1111 1111 fue rechazada")


@pytest.mark.parametrize(
    "promise",
    [
        "Ya aumentamos tu límite.",
        "Hemos habilitado México en tu tarjeta.",
        "Já aumentamos seu limite.",
        "Desbloqueamos sua categoria.",
        "We have increased your limit.",
        "We've unblocked the category.",
        "Your limit has been raised.",
    ],
)
def test_promising_an_action_that_is_only_pending_is_refused(promise):
    assert "premature_promise" in verify_reply(promise)


@pytest.mark.parametrize("leak", ["your risk score was high", "the fraud rule fired"])
def test_internal_fraud_detail_is_refused(leak):
    assert "internal_detail" in verify_reply(leak)


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_reply_is_refused(text):
    assert "empty" in verify_reply(text)


def test_overlong_reply_is_refused():
    assert "too_long" in verify_reply("a" * 1501)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_template_passes_the_checker_itself(lang):
    values = {
        "eta": "17:30",
        "allowed": "CO",
        "country": "MX",
        "limit": "150",
        "action": "allow MX for 24h",
    }
    for key in TEMPLATES[lang]:
        text = TEMPLATES[lang][key].format_map(values)
        assert verify_reply(text) == [], (lang, key)

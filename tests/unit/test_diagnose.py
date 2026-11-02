"""Deterministic diagnosis: decline code + card + controls + wallet → ordered causes.

The rules never trust the code alone. Each code is checked against the data, and a code
the data can't explain becomes INCONSISTENT (escalate), never a guess.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardControls,
    Company,
)
from decline_desk.rules.diagnose import Cause, Context, diagnose

T = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)  # 10:00 in Bogota


def make_auth(
    code="00",
    amount="50",
    currency="USD",
    rate="1",
    mcc="5812",
    country="CO",
    when=T,
    id_="auth_x",
    card_id="card_1",
) -> Authorization:
    return Authorization(
        id=id_,
        card_id=card_id,
        created_at=when,
        amount=Decimal(amount),
        currency=currency,
        usd_per_unit=Decimal(rate),
        merchant_name="Merchant",
        mcc=mcc,
        country=country,
        response_code=code,
    )


def make_ctx(
    status="active",
    expires=date(2028, 1, 31),
    per_txn="200",
    daily="500",
    blocked=(),
    countries=("CO",),
    wallet="1000",
    topups=(),
    day_auths=(),
) -> Context:
    return Context(
        card=Card(
            id="card_1",
            employee_id="emp_1",
            company_id="co_1",
            last4="4421",
            status=status,
            expires=expires,
        ),
        controls=CardControls(
            card_id="card_1",
            per_txn_limit_usd=Decimal(per_txn),
            daily_limit_usd=Decimal(daily),
            blocked_mccs=set(blocked),
            allowed_countries=set(countries),
        ),
        company=Company(
            id="co_1",
            name="Andes",
            timezone="America/Bogota",
            wallet_balance_usd=Decimal(wallet),
            limit_cap_usd=Decimal("3000"),
        ),
        pending_topups=list(topups),
        recent_authorizations=list(day_auths),
    )


def test_approved_authorization_has_no_cause():
    d = diagnose(make_auth("00"), make_ctx())
    assert d.approved is True
    assert d.causes == ()
    assert d.escalate is False


@pytest.mark.parametrize(
    ("code", "auth_kw", "ctx_kw", "cause"),
    [
        ("62", {"country": "MX"}, {"countries": ("CO",)}, Cause.COUNTRY_RESTRICTED),
        ("57", {"mcc": "5813"}, {"blocked": ("5813",)}, Cause.MCC_BLOCKED),
        ("61", {"amount": "250"}, {"per_txn": "200"}, Cause.LIMIT_PER_TXN),
        ("51", {"amount": "120"}, {"wallet": "60"}, Cause.COMPANY_FUNDS_LOW),
        ("59", {}, {}, Cause.SUSPECTED_FRAUD),
        ("78", {}, {"status": "frozen_by_user"}, Cause.CARD_FROZEN_BY_USER),
        ("78", {}, {"status": "frozen_fraud"}, Cause.CARD_FROZEN_FRAUD),
        ("54", {}, {"expires": date(2026, 9, 30)}, Cause.CARD_EXPIRED),
        ("1A", {}, {}, Cause.THREE_DS_FAILED),
        ("N7", {}, {}, Cause.INCORRECT_CVV),
    ],
)
def test_each_code_maps_to_its_cause_when_the_data_agrees(code, auth_kw, ctx_kw, cause):
    d = diagnose(make_auth(code, **auth_kw), make_ctx(**ctx_kw))
    assert d.approved is False
    assert d.causes[0] is cause


@pytest.mark.parametrize(
    ("code", "escalate"),
    [("59", True), ("1A", False), ("N7", False)],
)
def test_fraud_escalates_but_retryable_causes_do_not(code, escalate):
    assert diagnose(make_auth(code), make_ctx()).escalate is escalate


def test_frozen_for_fraud_escalates_frozen_by_user_does_not():
    assert diagnose(make_auth("78"), make_ctx(status="frozen_fraud")).escalate is True
    assert diagnose(make_auth("78"), make_ctx(status="frozen_by_user")).escalate is False


def test_unknown_code_escalates_and_never_guesses():
    d = diagnose(make_auth("Q9"), make_ctx())
    assert d.causes == (Cause.UNKNOWN,)
    assert d.escalate is True


def test_every_finding_carries_human_readable_evidence():
    d = diagnose(make_auth("62", country="MX"), make_ctx(countries=("CO",)))
    [finding] = d.findings
    assert "MX" in finding.evidence
    assert "CO" in finding.evidence


def test_evidence_mentions_the_card_by_last4_only():
    d = diagnose(make_auth("78"), make_ctx(status="frozen_by_user"))
    assert "4421" in d.findings[0].evidence


def test_rejects_a_card_that_does_not_match_the_authorization():
    with pytest.raises(ValueError, match="card"):
        diagnose(make_auth("62", card_id="card_other"), make_ctx())

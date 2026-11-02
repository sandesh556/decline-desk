"""Deterministic diagnosis: decline code + card + controls + wallet → ordered causes.

The rules never trust the code alone. Each code is checked against the data, and a code
the data can't explain becomes INCONSISTENT (escalate), never a guess.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardControls,
    Company,
    PendingTopup,
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


# --- limits -------------------------------------------------------------------------------


def spent(amount, when, code="00", id_=None):
    return make_auth(code, amount=amount, when=when, id_=id_ or f"prior_{when:%H%M}_{amount}")


def test_daily_limit_counts_earlier_approved_spend_the_same_local_day():
    earlier = [spent("300", T - timedelta(hours=4)), spent("150", T - timedelta(hours=2))]
    d = diagnose(make_auth("61", amount="100"), make_ctx(day_auths=earlier))
    assert d.causes == (Cause.LIMIT_DAILY,)
    assert "$550" in d.findings[0].evidence and "$500" in d.findings[0].evidence


def test_daily_day_boundary_is_the_company_timezone_not_utc():
    # 03:00 UTC on Oct 28 is 22:00 on Oct 27 in Bogota: a different day for the limit.
    prev_local_day = [spent("450", datetime(2026, 10, 28, 3, 0, tzinfo=UTC))]
    d = diagnose(make_auth("61", amount="100"), make_ctx(day_auths=prev_local_day))
    assert d.causes[0] is Cause.INCONSISTENT  # in UTC it would wrongly look like LIMIT_DAILY

    # 06:00 UTC is 01:00 on Oct 28 in Bogota: same local day, so it counts.
    same_local_day = [spent("450", datetime(2026, 10, 28, 6, 0, tzinfo=UTC))]
    d = diagnose(make_auth("61", amount="100"), make_ctx(day_auths=same_local_day))
    assert d.causes == (Cause.LIMIT_DAILY,)


def test_declined_and_later_authorizations_do_not_count_toward_daily_spend():
    noise = [
        spent("400", T - timedelta(hours=1), code="51"),  # declined, money never moved
        spent("400", T + timedelta(minutes=5)),  # after this decline
    ]
    d = diagnose(make_auth("61", amount="150"), make_ctx(day_auths=noise))
    assert d.causes[0] is Cause.INCONSISTENT


def test_amount_exactly_at_the_per_txn_limit_is_allowed():
    d = diagnose(make_auth("61", amount="200"), make_ctx(per_txn="200"))
    assert d.causes[0] is Cause.INCONSISTENT  # 200 <= 200, so the limit was not the reason


def test_local_currency_over_the_limit_only_after_conversion():
    # 900,000 COP at 0.00025 = $225 > $200, though 900,000 "looks" fine to a human
    d = diagnose(
        make_auth("61", amount="900000", currency="COP", rate="0.00025"), make_ctx(per_txn="200")
    )
    assert d.causes == (Cause.LIMIT_PER_TXN,)
    assert "$225.00" in d.findings[0].evidence


# --- funds --------------------------------------------------------------------------------


def topup(eta, amount="2500"):
    return PendingTopup(
        id="tp_1", company_id="co_1", amount_usd=Decimal(amount), asset="USDC", eta=eta
    )


def test_insufficient_funds_with_a_pending_stablecoin_topup():
    d = diagnose(
        make_auth("51", amount="120"),
        make_ctx(wallet="60", topups=[topup(T + timedelta(hours=2, minutes=30))]),
    )
    assert d.causes == (Cause.FUNDS_PENDING_SETTLEMENT,)
    assert "USDC" in d.findings[0].evidence
    assert "17:30 UTC" in d.findings[0].evidence
    assert d.escalate is False


def test_earliest_pending_topup_is_reported():
    d = diagnose(
        make_auth("51", amount="120"),
        make_ctx(
            wallet="60", topups=[topup(T + timedelta(hours=5)), topup(T + timedelta(hours=1))]
        ),
    )
    assert "16:00 UTC" in d.findings[0].evidence


def test_insufficient_funds_without_any_topup():
    d = diagnose(make_auth("51", amount="120"), make_ctx(wallet="60"))
    assert d.causes == (Cause.COMPANY_FUNDS_LOW,)


def test_insufficient_funds_code_but_wallet_covers_it_is_inconsistent():
    d = diagnose(make_auth("51", amount="120"), make_ctx(wallet="1000"))
    assert d.causes[0] is Cause.INCONSISTENT
    assert d.escalate is True


# --- inconsistency, expiry boundary, multiple causes ---------------------------------------


@pytest.mark.parametrize(
    ("code", "auth_kw", "ctx_kw"),
    [
        ("62", {"country": "CO"}, {"countries": ("CO",)}),  # country is allowed
        ("57", {"mcc": "5812"}, {"blocked": ("5813",)}),  # category not blocked
        ("78", {}, {"status": "active"}),  # card is not frozen
        ("54", {}, {"expires": date(2028, 1, 31)}),  # card is valid
    ],
)
def test_code_the_data_cannot_explain_is_inconsistent_and_escalates(code, auth_kw, ctx_kw):
    d = diagnose(make_auth(code, **auth_kw), make_ctx(**ctx_kw))
    assert Cause.INCONSISTENT in d.causes
    assert d.escalate is True


def test_card_is_valid_through_its_expiry_date():
    on_expiry_day = make_auth("54", when=datetime(2026, 10, 31, 20, 0, tzinfo=UTC))
    d = diagnose(on_expiry_day, make_ctx(expires=date(2026, 10, 31)))
    assert d.causes[0] is Cause.INCONSISTENT

    day_after = make_auth("54", when=datetime(2026, 11, 1, 20, 0, tzinfo=UTC))
    assert diagnose(day_after, make_ctx(expires=date(2026, 10, 31))).causes[0] is Cause.CARD_EXPIRED


def test_multiple_causes_are_all_reported_in_fix_first_order():
    # Blocked category AND wrong country AND over the per-transaction limit.
    d = diagnose(
        make_auth("57", mcc="5813", country="MX", amount="250"),
        make_ctx(blocked=("5813",), countries=("CO",), per_txn="200"),
    )
    assert d.causes == (Cause.COUNTRY_RESTRICTED, Cause.MCC_BLOCKED, Cause.LIMIT_PER_TXN)


def test_expired_comes_before_frozen_because_unfreezing_an_expired_card_is_pointless():
    d = diagnose(make_auth("78"), make_ctx(status="frozen_by_user", expires=date(2026, 9, 30)))
    assert d.causes == (Cause.CARD_EXPIRED, Cause.CARD_FROZEN_BY_USER)


def test_inconsistent_code_still_reports_real_violations_first():
    d = diagnose(
        make_auth("62", country="CO", mcc="5813"),
        make_ctx(countries=("CO",), blocked=("5813",)),
    )
    assert d.causes == (Cause.MCC_BLOCKED, Cause.INCONSISTENT)


def test_a_cause_is_never_reported_twice():
    d = diagnose(make_auth("62", country="MX"), make_ctx(countries=("CO",)))
    assert d.causes == (Cause.COUNTRY_RESTRICTED,)


def test_unknown_code_does_not_pick_up_secondary_guesses():
    d = diagnose(make_auth("Q9", country="MX"), make_ctx(countries=("CO",)))
    assert d.causes == (Cause.UNKNOWN,)

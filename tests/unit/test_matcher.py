"""Which authorization does the ticket mean? Deterministic matching on the model's hints.

The model only extracts hints (merchant, country, amount, days ago). This code decides.
Two plausible declines means we ask, never pick.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from decline_desk.agent.matcher import MatchStatus, match
from decline_desk.agent.triage import Hints
from decline_desk.domain.models import Authorization

TICKET = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)  # 10:00 Bogota
BOGOTA = "America/Bogota"


def auth(
    id_,
    *,
    merchant="Uber Trip",
    amount="185000",
    currency="COP",
    rate="0.00025",
    country="CO",
    code="62",
    when=TICKET - timedelta(hours=5),
) -> Authorization:
    return Authorization(
        id=id_,
        card_id="card_1",
        created_at=when,
        amount=Decimal(amount),
        currency=currency,
        usd_per_unit=Decimal(rate),
        merchant_name=merchant,
        mcc="4121",
        country=country,
        response_code=code,
    )


def run(hints: Hints, auths, tz=BOGOTA, ticket_time=TICKET):
    return match(hints, auths, ticket_time=ticket_time, timezone=tz)


def test_single_clear_match_by_merchant():
    result = run(Hints(merchant="uber"), [auth("u1"), auth("r1", merchant="Rappi")])
    assert result.status is MatchStatus.MATCH
    assert result.authorization.id == "u1"


def test_two_similar_declines_are_ambiguous_newest_first():
    older = auth("u_old", when=TICKET - timedelta(hours=20))
    newer = auth("u_new", when=TICKET - timedelta(hours=2))
    result = run(Hints(merchant="Uber"), [older, newer])
    assert result.status is MatchStatus.AMBIGUOUS
    assert result.authorization is None
    assert [a.id for a in result.candidates] == ["u_new", "u_old"]


def test_amount_disambiguates_within_ten_percent():
    a = auth("u_50k", amount="54000")  # within 10% of 50,000
    b = auth("u_120k", amount="120000")
    result = run(Hints(merchant="Uber", amount=Decimal("50000"), currency="COP"), [a, b])
    assert result.status is MatchStatus.MATCH
    assert result.authorization.id == "u_50k"


def test_amount_just_outside_tolerance_does_not_match():
    result = run(Hints(amount=Decimal("50000"), currency="COP"), [auth("u", amount="56000")])
    assert result.status is MatchStatus.NOT_FOUND


def test_usd_amount_hint_matches_a_local_currency_charge():
    mxn = auth(
        "mx", merchant="Uber Trip", amount="320", currency="MXN", rate="0.055", country="MX"
    )  # $17.60
    result = run(Hints(amount=Decimal("17.60"), currency="USD"), [mxn])
    assert result.authorization.id == "mx"


@pytest.mark.parametrize(
    ("tz", "ticket_time", "yesterday_utc", "today_utc"),
    [
        # 03:00 UTC Oct 28 is 22:00 Oct 27 in Bogota, so "ayer" means Oct 26 there.
        (
            BOGOTA,
            datetime(2026, 10, 28, 3, 0, tzinfo=UTC),
            datetime(2026, 10, 27, 1, 0, tzinfo=UTC),  # Oct 26, 20:00 Bogota
            datetime(2026, 10, 27, 18, 0, tzinfo=UTC),
        ),  # Oct 27, 13:00 Bogota
        # 02:00 UTC Oct 28 is 23:00 Oct 27 in Sao Paulo, so "ontem" means Oct 26 there.
        (
            "America/Sao_Paulo",
            datetime(2026, 10, 28, 2, 0, tzinfo=UTC),
            datetime(2026, 10, 26, 23, 0, tzinfo=UTC),  # Oct 26, 20:00 Sao Paulo
            datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
        ),  # Oct 27, 12:00 Sao Paulo
    ],
)
def test_yesterday_is_resolved_in_the_company_timezone(tz, ticket_time, yesterday_utc, today_utc):
    auths = [auth("yesterday", when=yesterday_utc), auth("today", when=today_utc)]
    result = run(Hints(days_ago=1), auths, tz=tz, ticket_time=ticket_time)
    assert result.status is MatchStatus.MATCH
    assert result.authorization.id == "yesterday"


def test_only_an_approved_charge_matches():
    result = run(Hints(merchant="Starbucks"), [auth("sb", merchant="Starbucks", code="00")])
    assert result.status is MatchStatus.APPROVED_NOT_DECLINED
    assert result.authorization.id == "sb"


def test_nothing_matches_means_we_never_received_the_attempt():
    result = run(Hints(merchant="Cabify"), [auth("u1")])
    assert result.status is MatchStatus.NOT_FOUND
    assert result.authorization is None and result.candidates == ()


def test_declines_older_than_14_days_are_out_of_scope():
    old = auth("old", when=TICKET - timedelta(days=14, minutes=1))
    edge = auth("edge", merchant="Rappi", when=TICKET - timedelta(days=14))
    assert run(Hints(merchant="Uber"), [old]).status is MatchStatus.NOT_FOUND
    assert run(Hints(merchant="Rappi"), [edge]).authorization.id == "edge"


def test_authorizations_after_the_ticket_are_ignored():
    future = auth("later", when=TICKET + timedelta(minutes=1))
    assert run(Hints(merchant="Uber"), [future]).status is MatchStatus.NOT_FOUND


def test_no_hints_and_one_decline_is_a_match():
    result = run(Hints(), [auth("only"), auth("ok", code="00", merchant="Rappi")])
    assert result.authorization.id == "only"


def test_no_hints_and_several_declines_is_ambiguous():
    result = run(Hints(), [auth("a"), auth("b", merchant="Rappi")])
    assert result.status is MatchStatus.AMBIGUOUS


def test_merchant_matching_ignores_case_and_accents():
    result = run(Hints(merchant="panaderia sao jorge"), [auth("p", merchant="Panadería São Jorge")])
    assert result.authorization.id == "p"


def test_stated_country_excludes_other_countries():
    co = auth("co", country="CO")
    mx = auth("mx", country="MX", currency="MXN", amount="320", rate="0.055")
    result = run(Hints(merchant="Uber", country="MX"), [co, mx])
    assert result.authorization.id == "mx"


def test_empty_history_is_not_found():
    assert run(Hints(merchant="Uber"), []).status is MatchStatus.NOT_FOUND

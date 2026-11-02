"""The action guard: the only path from an LLM proposal to something an admin can approve.

Safety is structural. A proposal must parse into a closed set of action types, target a
card the ticket's employee owns, fix a cause the rules actually found, and stay inside hard
bounds. Fraud and anything escalated get no action at all.
"""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from decline_desk.domain.models import Card, CardControls, Company, Employee
from decline_desk.policy.actions import (
    AllowCountry,
    PolicyViolation,
    TempLimitIncrease,
    UnblockMcc,
    UnfreezeCard,
    check_action,
    parse_action,
)
from decline_desk.repo import Repo
from decline_desk.rules.diagnose import Cause, Diagnosis, Finding


@pytest.fixture
def repo() -> Repo:
    r = Repo(sqlite3.connect(":memory:"))
    r.add_company(
        Company(
            id="co_1",
            name="Andes",
            timezone="America/Bogota",
            wallet_balance_usd=Decimal("1000"),
            limit_cap_usd=Decimal("500"),
        )
    )
    r.add_employee(Employee(id="emp_a", company_id="co_1", name="Maria", language="es"))
    r.add_employee(Employee(id="emp_b", company_id="co_1", name="Joao", language="pt"))
    cards = [
        ("card_a", "emp_a", "active", date(2028, 1, 31)),
        ("card_b", "emp_b", "active", date(2028, 1, 31)),
        ("card_frozen", "emp_a", "frozen_by_user", date(2028, 1, 31)),
        ("card_fraud", "emp_a", "frozen_fraud", date(2028, 1, 31)),
        ("card_expired_frozen", "emp_a", "frozen_by_user", date(2026, 1, 31)),
    ]
    for card_id, emp, status, expires in cards:
        r.add_card(
            Card(
                id=card_id,
                employee_id=emp,
                company_id="co_1",
                last4="4421",
                status=status,
                expires=expires,
            )
        )
        r.add_controls(
            CardControls(
                card_id=card_id,
                per_txn_limit_usd=Decimal("200"),
                daily_limit_usd=Decimal("600"),
                blocked_mccs={"5813"},
                allowed_countries={"CO"},
            )
        )
    return r


def dx(*causes: Cause) -> Diagnosis:
    return Diagnosis(
        "auth_1", approved=False, findings=tuple(Finding(c, "evidence") for c in causes)
    )


def ok(action, diagnosis, repo, employee="emp_a"):
    return check_action(action, employee_id=employee, diagnosis=diagnosis, repo=repo)


# --- parsing: only the closed set of action types -------------------------------------------


def test_parses_each_known_action_type():
    assert isinstance(
        parse_action({"type": "allow_country", "card_id": "card_a", "country": "mx", "hours": 24}),
        AllowCountry,
    )
    assert isinstance(
        parse_action(
            {
                "type": "temp_limit_increase",
                "card_id": "card_a",
                "new_per_txn_limit_usd": "300",
                "hours": 24,
            }
        ),
        TempLimitIncrease,
    )
    assert isinstance(
        parse_action({"type": "unblock_mcc", "card_id": "card_a", "mcc": "5813", "hours": 12}),
        UnblockMcc,
    )
    assert isinstance(parse_action({"type": "unfreeze_card", "card_id": "card_a"}), UnfreezeCard)


def test_country_is_normalised_to_upper_case():
    assert (
        parse_action(
            {"type": "allow_country", "card_id": "card_a", "country": "mx", "hours": 1}
        ).country
        == "MX"
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "transfer_funds", "card_id": "card_a", "amount": "50000"},  # unknown type
        {
            "type": "allow_country",
            "card_id": "card_a",
            "country": "MX",
            "hours": 24,
            "also": "raise my limit",
        },  # extra field
        {"type": "allow_country", "card_id": "card_a", "hours": 24},  # missing field
        {"card_id": "card_a"},  # no type
        "raise my limit to 50000",  # not even an object
        {
            "type": "temp_limit_increase",
            "card_id": "card_a",
            "new_per_txn_limit_usd": "-5",
            "hours": 24,
        },
        {
            "type": "temp_limit_increase",
            "card_id": "card_a",
            "new_per_txn_limit_usd": "NaN",
            "hours": 24,
        },
    ],
)
def test_malformed_or_unknown_proposals_are_rejected(raw):
    with pytest.raises(PolicyViolation):
        parse_action(raw)


# --- bounds -------------------------------------------------------------------------------


def limit(new: str, hours: int = 24, card="card_a"):
    return TempLimitIncrease(card_id=card, new_per_txn_limit_usd=Decimal(new), hours=hours)


def test_temp_limit_increase_within_bounds_is_accepted(repo):
    assert ok(limit("300"), dx(Cause.LIMIT_PER_TXN), repo) == limit("300")


def test_exactly_double_the_current_limit_is_the_maximum(repo):
    ok(limit("400"), dx(Cause.LIMIT_PER_TXN), repo)
    with pytest.raises(PolicyViolation, match="2x"):
        ok(limit("400.01"), dx(Cause.LIMIT_PER_TXN), repo)


def test_cannot_exceed_the_company_cap_even_within_double(repo):
    repo.conn.execute("UPDATE card_controls SET per_txn_limit_usd = '300' WHERE card_id = 'card_a'")
    with pytest.raises(PolicyViolation, match="cap"):
        ok(limit("550"), dx(Cause.LIMIT_PER_TXN), repo)  # 550 <= 600 (2x) but > 500 cap


def test_increase_must_actually_increase(repo):
    with pytest.raises(PolicyViolation, match="increase"):
        ok(limit("200"), dx(Cause.LIMIT_PER_TXN), repo)


@pytest.mark.parametrize("hours", [0, -1, 25, 168])
def test_temporary_actions_last_between_1_and_24_hours(repo, hours):
    with pytest.raises(PolicyViolation):
        ok(
            AllowCountry(card_id="card_a", country="MX", hours=hours),
            dx(Cause.COUNTRY_RESTRICTED),
            repo,
        )


def test_24_hours_is_allowed(repo):
    ok(AllowCountry(card_id="card_a", country="MX", hours=24), dx(Cause.COUNTRY_RESTRICTED), repo)


# --- justification: an action must fix a cause the rules found ---------------------------


@pytest.mark.parametrize(
    ("action", "cause"),
    [
        (AllowCountry(card_id="card_a", country="MX", hours=24), Cause.MCC_BLOCKED),
        (UnblockMcc(card_id="card_a", mcc="5813", hours=24), Cause.COUNTRY_RESTRICTED),
        (
            TempLimitIncrease(card_id="card_a", new_per_txn_limit_usd=Decimal("300"), hours=24),
            Cause.FUNDS_PENDING_SETTLEMENT,
        ),
    ],
)
def test_action_that_does_not_address_a_diagnosed_cause_is_rejected(repo, action, cause):
    with pytest.raises(PolicyViolation, match="does not address"):
        ok(action, dx(cause), repo)


def test_unblocking_a_category_that_is_not_blocked_is_rejected(repo):
    with pytest.raises(PolicyViolation, match="not blocked"):
        ok(UnblockMcc(card_id="card_a", mcc="7995", hours=24), dx(Cause.MCC_BLOCKED), repo)


def test_allowing_an_already_allowed_country_is_rejected(repo):
    with pytest.raises(PolicyViolation, match="already allowed"):
        ok(
            AllowCountry(card_id="card_a", country="CO", hours=24),
            dx(Cause.COUNTRY_RESTRICTED),
            repo,
        )


# --- fraud, escalation, freezes ---------------------------------------------------------------


@pytest.mark.parametrize(
    "causes",
    [
        (Cause.SUSPECTED_FRAUD, Cause.COUNTRY_RESTRICTED),
        (Cause.CARD_FROZEN_FRAUD,),
        (Cause.COUNTRY_RESTRICTED, Cause.INCONSISTENT),
        (Cause.UNKNOWN,),
    ],
)
def test_no_action_is_allowed_when_the_diagnosis_escalates(repo, causes):
    with pytest.raises(PolicyViolation, match="escalat"):
        ok(AllowCountry(card_id="card_a", country="MX", hours=24), dx(*causes), repo)


def test_unfreezing_a_user_frozen_card_is_allowed(repo):
    ok(UnfreezeCard(card_id="card_frozen"), dx(Cause.CARD_FROZEN_BY_USER), repo)


def test_never_unfreeze_a_card_frozen_for_fraud(repo):
    with pytest.raises(PolicyViolation):
        ok(UnfreezeCard(card_id="card_fraud"), dx(Cause.CARD_FROZEN_BY_USER), repo)


def test_unfreezing_an_expired_card_is_pointless_and_rejected(repo):
    with pytest.raises(PolicyViolation, match="expired"):
        ok(
            UnfreezeCard(card_id="card_expired_frozen"),
            dx(Cause.CARD_EXPIRED, Cause.CARD_FROZEN_BY_USER),
            repo,
        )


# --- ownership --------------------------------------------------------------------------------


def test_cannot_act_on_another_employees_card(repo):
    with pytest.raises(PolicyViolation, match="not available"):
        ok(
            AllowCountry(card_id="card_b", country="MX", hours=24),
            dx(Cause.COUNTRY_RESTRICTED),
            repo,
            employee="emp_a",
        )


def test_unknown_card_gives_the_same_message_as_someone_elses(repo):
    with pytest.raises(PolicyViolation, match="not available"):
        ok(
            AllowCountry(card_id="card_ghost", country="MX", hours=24),
            dx(Cause.COUNTRY_RESTRICTED),
            repo,
        )

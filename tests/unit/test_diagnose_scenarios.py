"""Every hand-written seed scenario diagnoses to the cause it was written for, end to end
through the repository."""

import sqlite3
from datetime import timedelta

import pytest

from decline_desk.data.seed import BASE_TIME, seed
from decline_desk.repo import Repo
from decline_desk.rules.diagnose import Cause, Context, diagnose


@pytest.fixture(scope="module")
def repo() -> Repo:
    r = Repo(sqlite3.connect(":memory:", check_same_thread=False))
    seed(r)
    return r


def diagnose_scenario(repo: Repo, employee_id: str, scenario_id: str):
    window = repo.authorizations_for_employee(
        employee_id, since=BASE_TIME - timedelta(days=14), until=BASE_TIME
    )
    [auth] = [a for a in window if a.id == scenario_id]
    card = repo.card_for_employee(employee_id, auth.card_id)
    ctx = Context(
        card=card,
        controls=repo.controls_for_card(card.id),
        company=repo.company(card.company_id),
        pending_topups=repo.pending_topups(card.company_id),
        recent_authorizations=window,
    )
    return diagnose(auth, ctx)


@pytest.mark.parametrize(
    ("employee", "scenario", "causes", "escalate"),
    [
        ("emp_maria", "scn_maria_uber_mx", (Cause.COUNTRY_RESTRICTED,), False),
        ("emp_carlos", "scn_carlos_hotel", (Cause.FUNDS_PENDING_SETTLEMENT,), False),
        ("emp_ana", "scn_ana_bar", (Cause.MCC_BLOCKED,), False),
        ("emp_lucas", "scn_lucas_laptop", (Cause.LIMIT_PER_TXN,), False),
        ("emp_lucas", "scn_lucas_fraud", (Cause.SUSPECTED_FRAUD,), True),
        ("emp_lucas", "scn_lucas_old_card", (Cause.CARD_FROZEN_FRAUD,), True),
        ("emp_priya", "scn_priya_saas", (Cause.THREE_DS_FAILED,), False),
        ("emp_priya", "scn_priya_cvv", (Cause.INCORRECT_CVV,), False),
        ("emp_priya", "scn_priya_old_card", (Cause.CARD_EXPIRED, Cause.CARD_FROZEN_BY_USER), False),
        ("emp_priya", "scn_priya_frozen", (Cause.CARD_EXPIRED, Cause.CARD_FROZEN_BY_USER), False),
        ("emp_carlos", "scn_carlos_weird", (Cause.UNKNOWN,), True),
    ],
)
def test_seed_scenario_diagnoses_as_designed(repo, employee, scenario, causes, escalate):
    d = diagnose_scenario(repo, employee, scenario)
    assert d.causes == causes
    assert d.escalate is escalate


def test_approved_scenario_has_no_cause(repo):
    d = diagnose_scenario(repo, "emp_maria", "scn_maria_ok")
    assert d.approved and d.causes == ()

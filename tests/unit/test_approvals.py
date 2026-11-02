"""Approval state machine: pending → executed | rejected, admin-only, idempotent, audited."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from decline_desk.domain.models import Card, CardControls, CardStatus, Company, Employee
from decline_desk.policy.actions import (
    AllowCountry,
    PolicyViolation,
    TempLimitIncrease,
    UnfreezeCard,
)
from decline_desk.policy.approvals import (
    Approvals,
    ApprovalStatus,
    Forbidden,
    InvalidTransition,
)
from decline_desk.repo import NotFound, Repo
from decline_desk.rules.diagnose import Cause, Diagnosis, Finding

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


@pytest.fixture
def repo() -> Repo:
    r = Repo(sqlite3.connect(":memory:"))
    for co in ("co_1", "co_2"):
        r.add_company(
            Company(
                id=co,
                name=co,
                timezone="America/Bogota",
                wallet_balance_usd=Decimal("1000"),
                limit_cap_usd=Decimal("500"),
            )
        )
    r.add_employee(Employee(id="emp_a", company_id="co_1", name="Maria", language="es"))
    r.add_employee(
        Employee(id="admin_1", company_id="co_1", name="Laura", language="es", is_admin=True)
    )
    r.add_employee(
        Employee(id="admin_2", company_id="co_2", name="Bia", language="pt", is_admin=True)
    )
    for card_id, status in [("card_a", "active"), ("card_frozen", "frozen_by_user")]:
        r.add_card(
            Card(
                id=card_id,
                employee_id="emp_a",
                company_id="co_1",
                last4="4421",
                status=status,
                expires=date(2028, 1, 31),
            )
        )
        r.add_controls(
            CardControls(
                card_id=card_id,
                per_txn_limit_usd=Decimal("200"),
                daily_limit_usd=Decimal("600"),
                blocked_mccs=set(),
                allowed_countries={"CO"},
            )
        )
    return r


@pytest.fixture
def approvals(repo) -> Approvals:
    return Approvals(repo, clock=lambda: NOW)


def dx(*causes):
    return Diagnosis("auth_1", approved=False, findings=tuple(Finding(c, "e") for c in causes))


ALLOW_MX = AllowCountry(card_id="card_a", country="MX", hours=24)


def submit(approvals, action=ALLOW_MX, causes=(Cause.COUNTRY_RESTRICTED,)):
    return approvals.submit(action, ticket_id="t_1", employee_id="emp_a", diagnosis=dx(*causes))


def overrides(repo):
    return repo.conn.execute(
        "SELECT card_id, kind, value, expires_at FROM control_overrides"
    ).fetchall()


def test_submitted_action_waits_for_approval_and_changes_nothing(approvals, repo):
    pending = submit(approvals)
    assert pending.status is ApprovalStatus.PENDING
    assert overrides(repo) == []


def test_submit_reruns_the_guard_so_nothing_can_skip_it(approvals):
    with pytest.raises(PolicyViolation):
        submit(approvals, causes=(Cause.SUSPECTED_FRAUD,))


def test_admin_approval_executes_the_action_once(approvals, repo):
    pending = submit(approvals)
    done = approvals.approve(pending.id, approver_id="admin_1")
    assert done.status is ApprovalStatus.EXECUTED
    [row] = overrides(repo)
    assert tuple(row) == (
        "card_a",
        "allow_country",
        "MX",
        (NOW + timedelta(hours=24)).isoformat(timespec="microseconds"),
    )


def test_double_approve_is_idempotent(approvals, repo):
    pending = submit(approvals)
    approvals.approve(pending.id, approver_id="admin_1")
    again = approvals.approve(pending.id, approver_id="admin_1")
    assert again.status is ApprovalStatus.EXECUTED
    assert len(overrides(repo)) == 1  # executed exactly once


def test_two_approvers_racing_still_execute_once(repo):
    first = Approvals(repo, clock=lambda: NOW)
    second = Approvals(repo, clock=lambda: NOW)  # e.g. another worker or browser tab
    pending = submit(first)
    first.approve(pending.id, approver_id="admin_1")
    second.approve(pending.id, approver_id="admin_1")
    assert len(overrides(repo)) == 1


def test_rejected_action_cannot_then_be_approved(approvals, repo):
    pending = submit(approvals)
    rejected = approvals.reject(pending.id, approver_id="admin_1", reason="travel not approved")
    assert rejected.status is ApprovalStatus.REJECTED
    with pytest.raises(InvalidTransition):
        approvals.approve(pending.id, approver_id="admin_1")
    assert overrides(repo) == []


def test_executed_action_cannot_be_rejected_afterwards(approvals):
    pending = submit(approvals)
    approvals.approve(pending.id, approver_id="admin_1")
    with pytest.raises(InvalidTransition):
        approvals.reject(pending.id, approver_id="admin_1", reason="too late")


@pytest.mark.parametrize("approver", ["emp_a", "admin_2", "nobody"])
def test_only_an_admin_of_the_same_company_can_approve(approvals, repo, approver):
    pending = submit(approvals)
    with pytest.raises(Forbidden):
        approvals.approve(pending.id, approver_id=approver)
    with pytest.raises(Forbidden):
        approvals.reject(pending.id, approver_id=approver, reason="x")
    assert approvals.get(pending.id).status is ApprovalStatus.PENDING


def test_unknown_action_id(approvals):
    with pytest.raises(NotFound):
        approvals.approve("act_missing", approver_id="admin_1")


def test_unfreeze_sets_the_card_active(approvals, repo):
    pending = approvals.submit(
        UnfreezeCard(card_id="card_frozen"),
        ticket_id="t_2",
        employee_id="emp_a",
        diagnosis=dx(Cause.CARD_FROZEN_BY_USER),
    )
    approvals.approve(pending.id, approver_id="admin_1")
    assert repo.card_for_employee("emp_a", "card_frozen").status is CardStatus.ACTIVE


def test_temp_limit_records_the_new_limit_and_expiry(approvals, repo):
    action = TempLimitIncrease(card_id="card_a", new_per_txn_limit_usd=Decimal("350"), hours=6)
    pending = approvals.submit(
        action, ticket_id="t_3", employee_id="emp_a", diagnosis=dx(Cause.LIMIT_PER_TXN)
    )
    approvals.approve(pending.id, approver_id="admin_1")
    [row] = overrides(repo)
    assert row[1:3] == ("per_txn_limit_usd", "350")


def test_every_transition_is_audited_in_order(approvals):
    pending = submit(approvals)
    approvals.approve(pending.id, approver_id="admin_1")
    trail = approvals.audit(pending.id)
    assert [(e.event, e.actor) for e in trail] == [
        ("submitted", "emp_a"),
        ("approved", "admin_1"),
        ("executed", "admin_1"),
    ]
    assert all(e.at == NOW for e in trail)


def test_rejection_reason_is_kept_in_the_audit(approvals):
    pending = submit(approvals)
    approvals.reject(pending.id, approver_id="admin_1", reason="travel not approved")
    assert approvals.audit(pending.id)[-1].detail == "travel not approved"


def test_failed_permission_attempts_are_audited_too(approvals):
    pending = submit(approvals)
    with pytest.raises(Forbidden):
        approvals.approve(pending.id, approver_id="emp_a")
    assert approvals.audit(pending.id)[-1].event == "denied"


def test_stale_read_race_still_executes_once(repo, monkeypatch):
    """Both approvers read 'pending' before either writes; only the atomic claim stops a
    double execution."""
    a = Approvals(repo, clock=lambda: NOW)
    b = Approvals(repo, clock=lambda: NOW)
    pending = submit(a)
    stale = b.get(pending.id)  # b reads before a approves
    a.approve(pending.id, approver_id="admin_1")

    real_get = b.get
    calls = iter([stale])
    monkeypatch.setattr(b, "get", lambda action_id: next(calls, None) or real_get(action_id))
    result = b.approve(pending.id, approver_id="admin_1")

    assert result.status is ApprovalStatus.EXECUTED
    assert len(overrides(repo)) == 1
    executed_events = [e for e in a.audit(pending.id) if e.event == "executed"]
    assert len(executed_events) == 1


def test_action_payload_round_trips_exactly(approvals):
    action = TempLimitIncrease(card_id="card_a", new_per_txn_limit_usd=Decimal("350.50"), hours=6)
    pending = approvals.submit(
        action, ticket_id="t_9", employee_id="emp_a", diagnosis=dx(Cause.LIMIT_PER_TXN)
    )
    assert approvals.get(pending.id).action == action

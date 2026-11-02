import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardControls,
    Company,
    Employee,
    PendingTopup,
)
from decline_desk.repo import NotFound, Repo

T0 = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


def auth(id_, card_id, when, code="62", merchant="Uber Trip", amount="185000"):
    return Authorization(
        id=id_,
        card_id=card_id,
        created_at=when,
        amount=Decimal(amount),
        currency="COP",
        usd_per_unit=Decimal("0.00025"),
        merchant_name=merchant,
        mcc="4121",
        country="CO",
        response_code=code,
    )


@pytest.fixture
def repo() -> Repo:
    r = Repo(sqlite3.connect(":memory:"))
    r.add_company(
        Company(
            id="co_1",
            name="Andes",
            timezone="America/Bogota",
            wallet_balance_usd=Decimal("1200.50"),
            limit_cap_usd=Decimal("5000"),
        )
    )
    r.add_employee(Employee(id="emp_a", company_id="co_1", name="Maria", language="es"))
    r.add_employee(Employee(id="emp_b", company_id="co_1", name="Joao", language="pt"))
    for card_id, emp in [("card_a", "emp_a"), ("card_b", "emp_b")]:
        r.add_card(
            Card(
                id=card_id,
                employee_id=emp,
                company_id="co_1",
                last4="4242",
                status="active",
                expires=date(2028, 1, 31),
            )
        )
    r.add_controls(
        CardControls(
            card_id="card_a",
            per_txn_limit_usd=Decimal("200"),
            daily_limit_usd=Decimal("500"),
            blocked_mccs={"7995"},
            allowed_countries={"MX"},
        )
    )
    r.add_authorization(auth("a_old", "card_a", T0 - timedelta(days=20)))
    r.add_authorization(auth("a_1", "card_a", T0 - timedelta(hours=5)))
    r.add_authorization(auth("a_2", "card_a", T0 - timedelta(hours=1), code="00"))
    r.add_authorization(auth("b_1", "card_b", T0 - timedelta(hours=2)))
    return r


def test_round_trips_company_with_exact_decimals(repo):
    company = repo.company("co_1")
    assert company.wallet_balance_usd == Decimal("1200.50")
    assert company.timezone == "America/Bogota"


def test_lists_only_the_employees_own_cards(repo):
    assert [c.id for c in repo.cards_for_employee("emp_a")] == ["card_a"]


def test_authorizations_are_scoped_to_the_employee_and_window_newest_first(repo):
    found = repo.authorizations_for_employee("emp_a", since=T0 - timedelta(days=14), until=T0)
    assert [a.id for a in found] == ["a_2", "a_1"]  # a_old outside window, b_1 not hers


def test_window_bounds_are_inclusive(repo):
    exact = T0 - timedelta(hours=5)
    found = repo.authorizations_for_employee("emp_a", since=exact, until=exact)
    assert [a.id for a in found] == ["a_1"]


def test_employee_cannot_read_another_employees_card(repo):
    # Same error as a missing card, so the response does not reveal that card_b exists.
    with pytest.raises(NotFound):
        repo.card_for_employee("emp_a", "card_b")
    with pytest.raises(NotFound):
        repo.card_for_employee("emp_a", "card_does_not_exist")


def test_controls_and_missing_controls(repo):
    controls = repo.controls_for_card("card_a")
    assert controls.blocked_mccs == frozenset({"7995"})
    assert controls.allowed_countries == frozenset({"MX"})
    with pytest.raises(NotFound):
        repo.controls_for_card("card_b")


def test_sql_injection_looking_text_is_stored_literally(repo):
    nasty = "Robert'); DROP TABLE authorizations;--"
    repo.add_authorization(auth("a_x", "card_a", T0, merchant=nasty))
    found = repo.authorizations_for_employee("emp_a", since=T0, until=T0)
    assert found[0].merchant_name == nasty
    assert (
        len(repo.authorizations_for_employee("emp_b", since=T0 - timedelta(days=1), until=T0)) == 1
    )


def test_pending_topups_round_trip(repo):
    repo.add_topup(
        PendingTopup(
            id="tp_1",
            company_id="co_1",
            amount_usd=Decimal("2000"),
            asset="USDC",
            eta=T0 + timedelta(hours=2),
        )
    )
    [topup] = repo.pending_topups("co_1")
    assert topup.eta == T0 + timedelta(hours=2)
    assert repo.pending_topups("co_unknown") == []


def test_employee_lookup(repo):
    assert repo.employee("emp_b").language == "pt"
    with pytest.raises(NotFound):
        repo.employee("emp_zzz")

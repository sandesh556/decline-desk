import sqlite3
from datetime import UTC, datetime, timedelta

from decline_desk.data.seed import BASE_TIME, seed
from decline_desk.domain.models import CardStatus, ResponseCode
from decline_desk.repo import Repo

WINDOW = timedelta(days=30)


def seeded(rng_seed: int = 42) -> Repo:
    repo = Repo(sqlite3.connect(":memory:"))
    seed(repo, rng_seed=rng_seed)
    return repo


def all_auths(repo: Repo, employees: list[str]) -> list:
    out = []
    for emp in employees:
        out += repo.authorizations_for_employee(
            emp, since=BASE_TIME - WINDOW, until=BASE_TIME + timedelta(days=1)
        )
    return out


def employee_ids(repo: Repo) -> list[str]:
    return [r[0] for r in repo.conn.execute("SELECT id FROM employees ORDER BY id")]


def test_same_seed_produces_identical_data():
    a, b = seeded(42), seeded(42)
    dump = lambda r: [x.model_dump() for x in all_auths(r, employee_ids(r))]  # noqa: E731
    assert dump(a) == dump(b)


def test_different_seed_changes_background_traffic_but_not_scenarios():
    a, b = seeded(1), seeded(2)
    ids = lambda r: {x.id for x in all_auths(r, employee_ids(r))}  # noqa: E731
    scenario_ids = {x for x in ids(a) if x.startswith("scn_")}
    assert scenario_ids and scenario_ids <= ids(b)
    assert ids(a) != ids(b)


def test_two_companies_in_different_timezones():
    repo = seeded()
    zones = {r[0] for r in repo.conn.execute("SELECT timezone FROM companies")}
    assert zones == {"America/Bogota", "America/Sao_Paulo"}


def test_every_language_and_an_admin_per_company():
    repo = seeded()
    emps = [repo.employee(e) for e in employee_ids(repo)]
    assert {e.language for e in emps} == {"es", "pt", "en"}
    admin_companies = {e.company_id for e in emps if e.is_admin}
    assert admin_companies == {"co_andes", "co_paulista"}


def test_every_response_code_appears_in_the_scenarios():
    repo = seeded()
    codes = {a.response_code for a in all_auths(repo, employee_ids(repo))}
    assert codes == set(ResponseCode)


def test_every_non_admin_employee_has_a_card_with_controls():
    repo = seeded()
    for emp_id in employee_ids(repo):
        if repo.employee(emp_id).is_admin:
            continue
        cards = repo.cards_for_employee(emp_id)
        assert cards, emp_id
        for card in cards:
            repo.controls_for_card(card.id)  # raises NotFound if missing


def test_both_freeze_reasons_are_represented():
    repo = seeded()
    statuses = {c.status for e in employee_ids(repo) for c in repo.cards_for_employee(e)}
    assert {CardStatus.FROZEN_BY_USER, CardStatus.FROZEN_FRAUD} <= statuses


def test_andes_wallet_is_low_with_a_pending_stablecoin_topup():
    repo = seeded()
    assert repo.company("co_andes").wallet_balance_usd < 100
    [topup] = repo.pending_topups("co_andes")
    assert topup.asset == "USDC"
    assert topup.eta > BASE_TIME


def test_nothing_is_dated_after_the_base_time():
    repo = seeded()
    assert all(a.created_at <= BASE_TIME for a in all_auths(repo, employee_ids(repo)))
    assert BASE_TIME.tzinfo is UTC
    assert BASE_TIME == datetime(2026, 10, 28, 15, 0, tzinfo=UTC)

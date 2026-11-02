"""Deterministic synthetic data: two LatAm companies, their employees, cards, controls,
a pending stablecoin top-up, and one hand-written scenario per decline code, plus seeded
random approved traffic around them.

Everything is relative to a fixed BASE_TIME, so tickets that say "yesterday" or "ontem"
resolve the same way on every run.
"""

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardControls,
    Company,
    Employee,
    PendingTopup,
)
from decline_desk.repo import Repo

BASE_TIME = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)

RATES = {  # USD per unit, captured at authorization time
    "COP": Decimal("0.00025"),
    "BRL": Decimal("0.18"),
    "MXN": Decimal("0.055"),
    "USD": Decimal("1"),
}

COMPANIES = [
    Company(
        id="co_andes",
        name="Andes Logistica",
        timezone="America/Bogota",
        wallet_balance_usd=Decimal("62.40"),  # low: a USDC top-up is still settling
        limit_cap_usd=Decimal("3000"),
    ),
    Company(
        id="co_paulista",
        name="Paulista Alimentos",
        timezone="America/Sao_Paulo",
        wallet_balance_usd=Decimal("48000"),
        limit_cap_usd=Decimal("8000"),
    ),
]

EMPLOYEES = [
    Employee(
        id="emp_laura", company_id="co_andes", name="Laura Rios", language="es", is_admin=True
    ),
    Employee(id="emp_maria", company_id="co_andes", name="Maria Gomez", language="es"),
    Employee(id="emp_carlos", company_id="co_andes", name="Carlos Ruiz", language="es"),
    Employee(
        id="emp_bia", company_id="co_paulista", name="Beatriz Alves", language="pt", is_admin=True
    ),
    Employee(id="emp_ana", company_id="co_paulista", name="Ana Souza", language="pt"),
    Employee(id="emp_lucas", company_id="co_paulista", name="Lucas Lima", language="pt"),
    Employee(id="emp_priya", company_id="co_paulista", name="Priya Rao", language="en"),
]

# fmt: off
# (card_id, employee, company, last4, status, expires, per_txn, daily, blocked_mccs, countries)
CARDS = [
    ("card_maria", "emp_maria", "co_andes", "4421", "active", date(2028, 3, 31), "300", "800", set(), {"CO"}),
    ("card_carlos", "emp_carlos", "co_andes", "7310", "active", date(2028, 6, 30), "500", "1500", set(), {"CO", "MX"}),
    ("card_ana", "emp_ana", "co_paulista", "1187", "active", date(2027, 11, 30), "400", "1000", {"5813", "7995"}, {"BR"}),
    ("card_lucas", "emp_lucas", "co_paulista", "9054", "active", date(2028, 1, 31), "150", "600", set(), {"BR", "US"}),
    ("card_lucas_old", "emp_lucas", "co_paulista", "3302", "frozen_fraud", date(2027, 8, 31), "150", "600", set(), {"BR"}),
    ("card_priya", "emp_priya", "co_paulista", "5528", "active", date(2028, 9, 30), "1000", "2500", set(), {"BR", "US"}),
    ("card_priya_old", "emp_priya", "co_paulista", "8816", "frozen_by_user", date(2026, 9, 30), "1000", "2500", set(), {"BR", "US"}),
]
# fmt: on

# fmt: off
# One hand-written scenario per decline code. (id, card, hours_ago, amount, ccy, merchant, mcc,
# country, code)
SCENARIOS = [
    ("scn_maria_uber_mx", "card_maria", 20, "320", "MXN", "Uber Trip", "4121", "MX", "62"),
    ("scn_carlos_hotel", "card_carlos", 3, "620000", "COP", "Hotel Andino Bogota", "7011", "CO", "51"),
    ("scn_ana_bar", "card_ana", 26, "180", "BRL", "Bar do Ze", "5813", "BR", "57"),
    ("scn_lucas_laptop", "card_lucas", 5, "1450", "BRL", "Kabum Informatica", "5732", "BR", "61"),
    ("scn_lucas_fraud", "card_lucas", 30, "89", "USD", "Gift Card Store", "5999", "US", "59"),
    ("scn_lucas_old_card", "card_lucas_old", 50, "60", "BRL", "iFood", "5814", "BR", "78"),
    ("scn_priya_saas", "card_priya", 8, "240", "USD", "Figma Inc", "5734", "US", "1A"),
    ("scn_priya_cvv", "card_priya", 9, "35", "USD", "Notion Labs", "5734", "US", "N7"),
    ("scn_priya_old_card", "card_priya_old", 12, "48", "USD", "AWS", "4816", "US", "54"),
    ("scn_priya_frozen", "card_priya_old", 11, "48", "USD", "AWS", "4816", "US", "78"),
    ("scn_carlos_weird", "card_carlos", 40, "95000", "COP", "Peaje Ruta 40", "4784", "CO", "Q9"),
    ("scn_maria_ok", "card_maria", 2, "45000", "COP", "Juan Valdez Cafe", "5814", "CO", "00"),
]
# fmt: on

BACKGROUND_MERCHANTS = [
    ("Rappi", "5812"),
    ("Uber Trip", "4121"),
    ("Exito", "5411"),
    ("Starbucks", "5814"),
    ("Google Cloud", "4816"),
    ("Cabify", "4121"),
]


def _auth(id_, card_id, when, amount, ccy, merchant, mcc, country, code) -> Authorization:
    return Authorization(
        id=id_,
        card_id=card_id,
        created_at=when,
        amount=Decimal(amount),
        currency=ccy,
        usd_per_unit=RATES[ccy],
        merchant_name=merchant,
        mcc=mcc,
        country=country,
        response_code=code,
    )


def seed(repo: Repo, rng_seed: int = 42) -> None:
    rng = random.Random(rng_seed)  # noqa: S311 - synthetic data, not security

    for company in COMPANIES:
        repo.add_company(company)
    for employee in EMPLOYEES:
        repo.add_employee(employee)
    for card_id, emp, co, last4, status, expires, per_txn, daily, mccs, countries in CARDS:
        repo.add_card(
            Card(
                id=card_id,
                employee_id=emp,
                company_id=co,
                last4=last4,
                status=status,
                expires=expires,
            )
        )
        repo.add_controls(
            CardControls(
                card_id=card_id,
                per_txn_limit_usd=Decimal(per_txn),
                daily_limit_usd=Decimal(daily),
                blocked_mccs=frozenset(mccs),
                allowed_countries=frozenset(countries),
            )
        )

    repo.add_topup(
        PendingTopup(
            id="tp_andes_usdc",
            company_id="co_andes",
            amount_usd=Decimal("2500"),
            asset="USDC",
            eta=BASE_TIME + timedelta(hours=2, minutes=30),
        )
    )

    for id_, card, hours_ago, amount, ccy, merchant, mcc, country, code in SCENARIOS:
        when = BASE_TIME - timedelta(hours=hours_ago)
        repo.add_authorization(_auth(id_, card, when, amount, ccy, merchant, mcc, country, code))

    # Approved background traffic so the agent has to pick the right row out of noise.
    home = {"co_andes": ("COP", "CO"), "co_paulista": ("BRL", "BR")}
    for card_id, _emp, co, *_rest in CARDS:
        ccy, country = home[co]
        for _ in range(rng.randint(3, 6)):
            merchant, mcc = rng.choice(BACKGROUND_MERCHANTS)
            minutes_ago = rng.randint(60, 14 * 24 * 60)
            amount = rng.randint(8_000, 90_000) if ccy == "COP" else rng.randint(12, 180)
            repo.add_authorization(
                _auth(
                    f"bg_{rng.getrandbits(40):010x}",
                    card_id,
                    BASE_TIME - timedelta(minutes=minutes_ago),
                    str(amount),
                    ccy,
                    merchant,
                    mcc,
                    country,
                    "00",
                )
            )

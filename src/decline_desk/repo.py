"""SQLite persistence. Every query is parameterised, and every employee-facing read is
scoped by employee_id, so a caller (or an LLM tool call) can never reach another
employee's cards. Money is stored as TEXT to keep Decimal exact; time is stored as
fixed-width UTC ISO text so string comparison matches time order.
"""

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

from decline_desk.domain.models import (
    Authorization,
    Card,
    CardControls,
    Company,
    Employee,
    PendingTopup,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL,
    wallet_balance_usd TEXT NOT NULL, limit_cap_usd TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employees (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL, language TEXT NOT NULL, is_admin INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(id),
    company_id TEXT NOT NULL REFERENCES companies(id), last4 TEXT NOT NULL,
    status TEXT NOT NULL, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS card_controls (
    card_id TEXT PRIMARY KEY REFERENCES cards(id), per_txn_limit_usd TEXT NOT NULL,
    daily_limit_usd TEXT NOT NULL, blocked_mccs TEXT NOT NULL, allowed_countries TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS authorizations (
    id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id), created_at TEXT NOT NULL,
    amount TEXT NOT NULL, currency TEXT NOT NULL, usd_per_unit TEXT NOT NULL,
    merchant_name TEXT NOT NULL, mcc TEXT NOT NULL, country TEXT NOT NULL,
    response_code TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_auth_card_time ON authorizations(card_id, created_at);
CREATE TABLE IF NOT EXISTS pending_topups (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
    amount_usd TEXT NOT NULL, asset TEXT NOT NULL, eta TEXT NOT NULL);
"""


class NotFound(LookupError):
    """Missing, or not visible to this employee. Deliberately the same error for both."""


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # --- writes -----------------------------------------------------------------------------

    def add_company(self, c: Company) -> None:
        self._insert(
            "companies",
            (c.id, c.name, c.timezone, str(c.wallet_balance_usd), str(c.limit_cap_usd)),
        )

    def add_employee(self, e: Employee) -> None:
        self._insert("employees", (e.id, e.company_id, e.name, e.language, int(e.is_admin)))

    def add_card(self, c: Card) -> None:
        self._insert(
            "cards",
            (c.id, c.employee_id, c.company_id, c.last4, c.status.value, c.expires.isoformat()),
        )

    def add_controls(self, c: CardControls) -> None:
        self._insert(
            "card_controls",
            (
                c.card_id,
                str(c.per_txn_limit_usd),
                str(c.daily_limit_usd),
                json.dumps(sorted(c.blocked_mccs)),
                json.dumps(sorted(c.allowed_countries)),
            ),
        )

    def add_authorization(self, a: Authorization) -> None:
        self._insert(
            "authorizations",
            (
                a.id,
                a.card_id,
                _ts(a.created_at),
                str(a.amount),
                a.currency.value,
                str(a.usd_per_unit),
                a.merchant_name,
                a.mcc,
                a.country,
                a.response_code.value,
            ),
        )

    def add_topup(self, t: PendingTopup) -> None:
        self._insert("pending_topups", (t.id, t.company_id, str(t.amount_usd), t.asset, _ts(t.eta)))

    def _insert(self, table: str, values: tuple) -> None:
        # table names come from this module only, never from callers
        marks = ", ".join("?" * len(values))
        self.conn.execute(f"INSERT INTO {table} VALUES ({marks})", values)  # noqa: S608
        self.conn.commit()

    # --- reads ------------------------------------------------------------------------------

    def company(self, company_id: str) -> Company:
        row = self._one("SELECT * FROM companies WHERE id = ?", (company_id,))
        return Company(
            id=row["id"],
            name=row["name"],
            timezone=row["timezone"],
            wallet_balance_usd=Decimal(row["wallet_balance_usd"]),
            limit_cap_usd=Decimal(row["limit_cap_usd"]),
        )

    def employee(self, employee_id: str) -> Employee:
        row = self._one("SELECT * FROM employees WHERE id = ?", (employee_id,))
        return Employee(
            id=row["id"],
            company_id=row["company_id"],
            name=row["name"],
            language=row["language"],
            is_admin=bool(row["is_admin"]),
        )

    def cards_for_employee(self, employee_id: str) -> list[Card]:
        rows = self.conn.execute(
            "SELECT * FROM cards WHERE employee_id = ? ORDER BY id", (employee_id,)
        ).fetchall()
        return [self._card(r) for r in rows]

    def card_for_employee(self, employee_id: str, card_id: str) -> Card:
        row = self._one(
            "SELECT * FROM cards WHERE id = ? AND employee_id = ?", (card_id, employee_id)
        )
        return self._card(row)

    def controls_for_card(self, card_id: str) -> CardControls:
        row = self._one("SELECT * FROM card_controls WHERE card_id = ?", (card_id,))
        return CardControls(
            card_id=row["card_id"],
            per_txn_limit_usd=Decimal(row["per_txn_limit_usd"]),
            daily_limit_usd=Decimal(row["daily_limit_usd"]),
            blocked_mccs=frozenset(json.loads(row["blocked_mccs"])),
            allowed_countries=frozenset(json.loads(row["allowed_countries"])),
        )

    def authorizations_for_employee(
        self, employee_id: str, since: datetime, until: datetime
    ) -> list[Authorization]:
        rows = self.conn.execute(
            """
            SELECT a.* FROM authorizations a JOIN cards c ON c.id = a.card_id
            WHERE c.employee_id = ? AND a.created_at BETWEEN ? AND ?
            ORDER BY a.created_at DESC, a.id
            """,
            (employee_id, _ts(since), _ts(until)),
        ).fetchall()
        return [self._auth(r) for r in rows]

    def pending_topups(self, company_id: str) -> list[PendingTopup]:
        rows = self.conn.execute(
            "SELECT * FROM pending_topups WHERE company_id = ? ORDER BY eta", (company_id,)
        ).fetchall()
        return [
            PendingTopup(
                id=r["id"],
                company_id=r["company_id"],
                amount_usd=Decimal(r["amount_usd"]),
                asset=r["asset"],
                eta=_dt(r["eta"]),
            )
            for r in rows
        ]

    def _one(self, sql: str, params: tuple) -> sqlite3.Row:
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            raise NotFound
        return row

    @staticmethod
    def _card(r: sqlite3.Row) -> Card:
        return Card(
            id=r["id"],
            employee_id=r["employee_id"],
            company_id=r["company_id"],
            last4=r["last4"],
            status=r["status"],
            expires=date.fromisoformat(r["expires"]),
        )

    @staticmethod
    def _auth(r: sqlite3.Row) -> Authorization:
        return Authorization(
            id=r["id"],
            card_id=r["card_id"],
            created_at=_dt(r["created_at"]),
            amount=Decimal(r["amount"]),
            currency=r["currency"],
            usd_per_unit=Decimal(r["usd_per_unit"]),
            merchant_name=r["merchant_name"],
            mcc=r["mcc"],
            country=r["country"],
            response_code=r["response_code"],
        )

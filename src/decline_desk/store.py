"""Persist tickets and their resolutions so the ops UI can list and show them.

A resolution is stored as a JSON view: everything the UI shows, in plain types. Approval
state is not copied here; it is read live from the approvals table, so it is never stale.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from decline_desk.agent.resolver import Resolution
from decline_desk.domain.models import Ticket

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(id),
    company_id TEXT NOT NULL REFERENCES companies(id), text TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resolutions (
    ticket_id TEXT PRIMARY KEY REFERENCES tickets(id), data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tickets_company ON tickets(company_id, created_at);
"""


@dataclass(frozen=True)
class StoredTicket:
    ticket: Ticket
    company_id: str
    employee_name: str
    view: dict


def resolution_view(res: Resolution) -> dict:
    return {
        "status": res.status.value,
        "language": res.language,
        "reply": res.reply,
        "authorization_id": res.authorization_id,
        "causes": [c.value for c in res.diagnosis.causes] if res.diagnosis else [],
        "evidence": list(res.evidence),
        "action": res.action.model_dump(mode="json") if res.action else None,
        "pending_action_id": res.pending_action_id,
        "flags": sorted(res.flags),
        "trace": [
            {
                "name": s.name,
                "model": s.model,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "latency_ms": s.latency_ms,
                "cost_usd": str(s.cost_usd),
                "note": s.note,
            }
            for s in res.trace
        ],
        "cost_usd": str(res.cost_usd),
    }


class TicketStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(SCHEMA)

    def save(self, ticket: Ticket, company_id: str, resolution: Resolution) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO tickets VALUES (?, ?, ?, ?, ?)",
                (
                    ticket.id,
                    ticket.employee_id,
                    company_id,
                    ticket.text,
                    ticket.created_at.isoformat(timespec="microseconds"),
                ),
            )
            self.conn.execute(
                "INSERT INTO resolutions VALUES (?, ?)",
                (ticket.id, json.dumps(resolution_view(resolution), ensure_ascii=False)),
            )

    def list_for_company(self, company_id: str) -> list[StoredTicket]:
        rows = self.conn.execute(
            """
            SELECT t.*, e.name AS employee_name, r.data FROM tickets t
            JOIN employees e ON e.id = t.employee_id
            JOIN resolutions r ON r.ticket_id = t.id
            WHERE t.company_id = ? ORDER BY t.created_at DESC, t.id DESC
            """,
            (company_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, ticket_id: str, company_id: str) -> StoredTicket | None:
        """Only within the caller's company: another company's ticket looks like a 404."""
        row = self.conn.execute(
            """
            SELECT t.*, e.name AS employee_name, r.data FROM tickets t
            JOIN employees e ON e.id = t.employee_id
            JOIN resolutions r ON r.ticket_id = t.id
            WHERE t.id = ? AND t.company_id = ?
            """,
            (ticket_id, company_id),
        ).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(r: sqlite3.Row) -> StoredTicket:
        ticket = Ticket(
            id=r["id"],
            employee_id=r["employee_id"],
            text=r["text"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        return StoredTicket(ticket, r["company_id"], r["employee_name"], json.loads(r["data"]))

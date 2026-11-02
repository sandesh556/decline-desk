"""Approval state machine for proposed actions.

    pending_approval ──approve──▶ executed
           │
           └──────reject────────▶ rejected

- submit() re-runs the action guard, so no caller can skip it.
- Only an admin of the card's company may approve or reject. Denied attempts are audited.
- approve() claims the row with a conditional UPDATE (status must still be pending), so a
  double click or two workers racing execute the action exactly once.
- Every transition is written to an append-only audit log.
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from decline_desk.policy.actions import (
    Action,
    AllowCountry,
    TempLimitIncrease,
    UnblockMcc,
    UnfreezeCard,
    check_action,
    parse_action,
)
from decline_desk.repo import NotFound, Repo
from decline_desk.rules.diagnose import Diagnosis

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, card_id TEXT NOT NULL REFERENCES cards(id),
    action TEXT NOT NULL, status TEXT NOT NULL, requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL, at TEXT NOT NULL,
    actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS control_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL REFERENCES cards(id),
    kind TEXT NOT NULL, value TEXT NOT NULL, expires_at TEXT NOT NULL,
    action_id TEXT NOT NULL UNIQUE);
"""


class ApprovalStatus(StrEnum):
    PENDING = "pending_approval"
    EXECUTED = "executed"
    REJECTED = "rejected"


class Forbidden(PermissionError):
    """The actor may not approve or reject this action."""


class InvalidTransition(RuntimeError):
    """The action is not in a state that allows this transition."""


@dataclass(frozen=True)
class PendingAction:
    id: str
    ticket_id: str
    action: Action
    status: ApprovalStatus
    requested_by: str


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor: str
    event: str
    detail: str


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class Approvals:
    def __init__(self, repo: Repo, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.repo, self.conn, self.clock = repo, repo.conn, clock
        self.conn.executescript(SCHEMA)

    # --- transitions ---------------------------------------------------------------------

    def submit(
        self, action: Action, *, ticket_id: str, employee_id: str, diagnosis: Diagnosis
    ) -> PendingAction:
        check_action(action, employee_id=employee_id, diagnosis=diagnosis, repo=self.repo)
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        with self.conn:
            self.conn.execute(
                "INSERT INTO pending_actions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    action_id,
                    ticket_id,
                    action.card_id,
                    action.model_dump_json(),
                    ApprovalStatus.PENDING.value,
                    employee_id,
                    _ts(self.clock()),
                ),
            )
            self._audit(action_id, employee_id, "submitted", action.type)
        return self.get(action_id)

    def approve(self, action_id: str, *, approver_id: str) -> PendingAction:
        current = self.get(action_id)
        self._authorize(current, approver_id)
        if current.status is ApprovalStatus.EXECUTED:
            return current  # idempotent: a second approval is a no-op
        if current.status is ApprovalStatus.REJECTED:
            raise InvalidTransition("a rejected action cannot be approved")

        with self.conn:
            claimed = self.conn.execute(
                "UPDATE pending_actions SET status = ? WHERE id = ? AND status = ?",
                (ApprovalStatus.EXECUTED.value, action_id, ApprovalStatus.PENDING.value),
            ).rowcount
            if claimed == 1:  # this caller won the claim; anyone else sees rowcount 0
                self._audit(action_id, approver_id, "approved", "")
                self._execute(action_id, current.action)
                self._audit(action_id, approver_id, "executed", current.action.type)
        return self.get(action_id)

    def reject(self, action_id: str, *, approver_id: str, reason: str) -> PendingAction:
        current = self.get(action_id)
        self._authorize(current, approver_id)
        with self.conn:
            changed = self.conn.execute(
                "UPDATE pending_actions SET status = ? WHERE id = ? AND status = ?",
                (ApprovalStatus.REJECTED.value, action_id, ApprovalStatus.PENDING.value),
            ).rowcount
            if changed != 1:
                raise InvalidTransition(f"cannot reject an action that is {current.status}")
            self._audit(action_id, approver_id, "rejected", reason)
        return self.get(action_id)

    # --- reads ---------------------------------------------------------------------------

    def get(self, action_id: str) -> PendingAction:
        row = self.conn.execute(
            "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            raise NotFound
        return PendingAction(
            id=row["id"],
            ticket_id=row["ticket_id"],
            action=parse_action(json.loads(row["action"])),
            status=ApprovalStatus(row["status"]),
            requested_by=row["requested_by"],
        )

    def audit(self, action_id: str) -> list[AuditEntry]:
        rows = self.conn.execute(
            "SELECT at, actor, event, detail FROM audit_log WHERE action_id = ? ORDER BY seq",
            (action_id,),
        ).fetchall()
        return [AuditEntry(datetime.fromisoformat(r[0]), r[1], r[2], r[3]) for r in rows]

    # --- internals -----------------------------------------------------------------------

    def _authorize(self, pending: PendingAction, approver_id: str) -> None:
        card_company = self.conn.execute(
            "SELECT company_id FROM cards WHERE id = ?", (pending.action.card_id,)
        ).fetchone()[0]
        try:
            approver = self.repo.employee(approver_id)
        except NotFound:
            approver = None
        if approver is None or not approver.is_admin or approver.company_id != card_company:
            with self.conn:
                self._audit(pending.id, approver_id, "denied", "not an admin of this company")
            raise Forbidden("only an admin of the card's company can do this")

    def _execute(self, action_id: str, action: Action) -> None:
        if isinstance(action, UnfreezeCard):
            self.conn.execute("UPDATE cards SET status = 'active' WHERE id = ?", (action.card_id,))
            return
        kind, value = {
            AllowCountry: lambda a: ("allow_country", a.country),
            UnblockMcc: lambda a: ("unblock_mcc", a.mcc),
            TempLimitIncrease: lambda a: ("per_txn_limit_usd", str(a.new_per_txn_limit_usd)),
        }[type(action)](action)
        expires = self.clock() + timedelta(hours=action.hours)
        self.conn.execute(
            "INSERT INTO control_overrides (card_id, kind, value, expires_at, action_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (action.card_id, kind, value, _ts(expires), action_id),
        )

    def _audit(self, action_id: str, actor: str, event: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (action_id, at, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
            (action_id, _ts(self.clock()), actor, event, detail),
        )

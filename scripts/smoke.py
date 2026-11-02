"""End-to-end smoke run over everything built so far. `make smoke` runs it before each
phase tag, so you can see the system working on real seeded data, not just unit tests.
"""

import json
import sqlite3
from datetime import timedelta

from decline_desk.agent.matcher import match
from decline_desk.agent.triage import triage
from decline_desk.data.seed import BASE_TIME, seed
from decline_desk.llm.client import Completion, FakeLLM, Usage
from decline_desk.pii import mask_pii
from decline_desk.policy.actions import PolicyViolation, check_action, parse_action
from decline_desk.policy.approvals import Approvals, Forbidden
from decline_desk.repo import NotFound, Repo
from decline_desk.rules.diagnose import Context, diagnose


def main() -> None:
    repo = Repo(sqlite3.connect(":memory:"))
    seed(repo)

    print("== Companies")
    for co_id in ("co_andes", "co_paulista"):
        co = repo.company(co_id)
        print(f"  {co.name:<20} tz={co.timezone:<18} wallet=${co.wallet_balance_usd}")
    for topup in repo.pending_topups("co_andes"):
        print(f"  pending top-up: ${topup.amount_usd} {topup.asset}, settles {topup.eta:%H:%M} UTC")

    print("\n== Maria's authorizations, last 48h (newest first)")
    for a in repo.authorizations_for_employee(
        "emp_maria", since=BASE_TIME - timedelta(hours=48), until=BASE_TIME
    ):
        status = "DECLINED" if a.is_declined else "approved"
        print(
            f"  {a.created_at:%m-%d %H:%M}Z  {a.merchant_name:<18} {a.amount:>9} {a.currency}"
            f" = ${a.amount_usd:>7}  {a.country}  {a.response_code.value} {status}"
        )

    print("\n== IDOR guard: Maria asks for Carlos's card")
    try:
        repo.card_for_employee("emp_maria", "card_carlos")
        print("  FAIL: card returned")
    except NotFound:
        print("  ok: NotFound (same as a missing card)")

    print("\n== PII masking on a raw ticket")
    raw = "Hola, mi tarjeta 4111 1111 1111 1111 fue rechazada. Escribanme a maria@andes.co o +57 300 123 4567. Pago 1500000 COP."
    print(f"  in : {raw}")
    print(f"  out: {mask_pii(raw)}")

    print("\n== Diagnosis of every declined authorization (rules only, no LLM)")
    for (emp_id,) in repo.conn.execute("SELECT id FROM employees ORDER BY id"):
        window = repo.authorizations_for_employee(
            emp_id, since=BASE_TIME - timedelta(days=14), until=BASE_TIME
        )
        for a in [x for x in window if x.is_declined]:
            card = repo.card_for_employee(emp_id, a.card_id)
            d = diagnose(
                a,
                Context(
                    card=card,
                    controls=repo.controls_for_card(card.id),
                    company=repo.company(card.company_id),
                    pending_topups=repo.pending_topups(card.company_id),
                    recent_authorizations=window,
                ),
            )
            flag = "ESCALATE" if d.escalate else "        "
            causes = ", ".join(c.value for c in d.causes)
            print(f"  {flag} {a.id:<20} code {a.response_code.value:<3} -> {causes}")
            print(f"           {d.findings[0].evidence}")

    print("\n== Action guard + approvals on Maria's Uber decline in Mexico")
    window = repo.authorizations_for_employee(
        "emp_maria", since=BASE_TIME - timedelta(days=14), until=BASE_TIME
    )
    [uber] = [a for a in window if a.id == "scn_maria_uber_mx"]
    card = repo.card_for_employee("emp_maria", uber.card_id)
    dx = diagnose(
        uber,
        Context(
            card=card,
            controls=repo.controls_for_card(card.id),
            company=repo.company(card.company_id),
            recent_authorizations=window,
        ),
    )
    proposals = {
        "injected: raise limit to 50000": {
            "type": "temp_limit_increase",
            "card_id": card.id,
            "new_per_txn_limit_usd": "50000",
            "hours": 24,
        },
        "invented action type": {"type": "transfer_funds", "card_id": card.id},
        "someone else's card": {
            "type": "allow_country",
            "card_id": "card_carlos",
            "country": "MX",
            "hours": 24,
        },
        "legit: allow MX for 24h": {
            "type": "allow_country",
            "card_id": card.id,
            "country": "MX",
            "hours": 24,
        },
    }
    approvals = Approvals(repo, clock=lambda: BASE_TIME)
    for label, raw in proposals.items():
        try:
            action = check_action(
                parse_action(raw), employee_id="emp_maria", diagnosis=dx, repo=repo
            )
        except PolicyViolation as exc:
            print(f"  REFUSED  {label:<32} {exc}")
            continue
        pending = approvals.submit(
            action, ticket_id="t_demo", employee_id="emp_maria", diagnosis=dx
        )
        print(f"  accepted {label:<32} -> {pending.status.value}")
        try:
            approvals.approve(pending.id, approver_id="emp_maria")
        except Forbidden:
            print("           Maria tries to approve her own request -> Forbidden")
        approvals.approve(pending.id, approver_id="emp_laura")
        approvals.approve(pending.id, approver_id="emp_laura")  # double click
        print(
            f"           Laura (admin) approves twice -> {approvals.get(pending.id).status.value}"
        )
        n = repo.conn.execute("SELECT COUNT(*) FROM control_overrides").fetchone()[0]
        print(f"           overrides written: {n}")
        for e in approvals.audit(pending.id):
            print(f"           audit: {e.event:<9} by {e.actor}")

    print("\n== Ticket -> triage (scripted model) -> match -> diagnosis")
    ticket = (
        "Hola, mi tarjeta 4111 1111 1111 1111 fue rechazada ayer en un Uber en Ciudad de "
        "Mexico, unos 320 pesos. Me pueden ayudar? maria@andes.co"
    )
    scripted = FakeLLM(
        [
            Completion(
                json.dumps(
                    {
                        "language": "es",
                        "intent": "card_decline",
                        "merchant": "Uber",
                        "country": "MX",
                        "amount": "320",
                        "currency": "MXN",
                        "days_ago": 1,
                    }
                ),
                Usage("fake-haiku", 310, 58, 1),
            )
        ]
    )
    t = triage(ticket, client=scripted, model="fake-haiku", fallback_language="es")
    print(f"  model saw : {scripted.calls[0].user}")
    print(f"  triage    : lang={t.language} intent={t.intent.value} hints={t.hints.model_dump()}")
    history = repo.authorizations_for_employee(
        "emp_maria", since=BASE_TIME - timedelta(days=14), until=BASE_TIME
    )
    m = match(t.hints, history, ticket_time=BASE_TIME, timezone="America/Bogota")
    print(f"  match     : {m.status.value} -> {m.authorization.id if m.authorization else None}")


if __name__ == "__main__":
    main()

"""6b: the ticket page."""

import json
from datetime import timedelta

from decline_desk.agent.resolver import resolve
from decline_desk.data.seed import BASE_TIME
from decline_desk.domain.models import Ticket
from decline_desk.llm.client import Completion, FakeLLM, Usage
from tests.web.conftest import signed_in


def test_country_block_ticket_shows_everything_ops_needs(client):
    page = signed_in(client, "emp_laura").get("/tickets/demo_1")
    assert page.status_code == 200
    text = page.text
    assert "Maria Gomez" in text
    assert "rechazada ayer en un Uber" in text  # the ticket itself
    assert "Country restricted" in text  # cause, as a readable label
    assert "is allowed in CO; the purchase was in MX" in text  # evidence
    assert "habilite Mexico en tu tarjeta" in text  # draft reply
    assert "Allow card in MX for 24 hours" in text  # proposed action
    assert "Pending approval" in text
    assert 'action="/tickets/demo_1/approve"' in text
    assert 'action="/tickets/demo_1/reject"' in text


def test_trace_and_cost_are_shown(client):
    text = signed_in(client, "emp_laura").get("/tickets/demo_1").text
    assert "claude-haiku-4-5" in text and "claude-sonnet-5-5" in text
    assert "$0.004920" in text


def test_audit_trail_is_shown(client):
    text = signed_in(client, "emp_laura").get("/tickets/demo_1").text
    assert "submitted" in text and "Maria Gomez" in text


def test_unknown_ticket_is_404(client):
    page = signed_in(client, "emp_laura").get("/tickets/nope")
    assert page.status_code == 404
    assert "Ticket not found" in page.text


def test_another_companys_ticket_is_indistinguishable_from_missing(client):
    page = signed_in(client, "emp_laura").get("/tickets/demo_3")  # Paulista ticket
    assert page.status_code == 404
    assert "Ticket not found" in page.text


def test_signed_out_users_see_the_sign_in_picker(client):
    assert "Sign in as" in client.get("/tickets/demo_1").text


def test_fraud_ticket_shows_escalation_and_no_approval_controls(client):
    text = signed_in(client, "emp_bia").get("/tickets/demo_3").text
    assert "Escalated" in text
    assert "time de segurança" in text
    assert "/approve" not in text and "/reject" not in text


def test_injection_ticket_shows_the_warnings_and_the_safe_action(client):
    text = signed_in(client, "emp_bia").get("/tickets/demo_4").text
    assert "Possible prompt injection" in text
    assert "Model action refused" in text and "$50000 exceeds 2x" in text
    assert "Raise per-transaction limit to $270 for 24 hours" in text


def test_ambiguous_ticket_shows_the_clarifying_question_without_actions(client):
    text = signed_in(client, "emp_bia").get("/tickets/demo_6").text
    assert "Needs info" in text
    assert "Which one do you mean?" in text
    assert "/approve" not in text


def test_ticket_text_is_escaped_so_it_cannot_inject_markup(client, services):
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)> rechazada'
    t = Ticket(
        id="demo_9",
        employee_id="emp_maria",
        text=evil,
        created_at=BASE_TIME + timedelta(minutes=30),
    )
    script = [
        Completion(
            json.dumps(
                {
                    "language": "es",
                    "intent": "other",
                    "merchant": None,
                    "country": None,
                    "amount": None,
                    "currency": None,
                    "days_ago": None,
                }
            ),
            Usage("claude-haiku-4-5", 100, 20, 100),
        )
    ]
    res = resolve(
        t,
        repo=services.repo,
        client=FakeLLM(script),
        approvals=services.approvals,
        triage_model="claude-haiku-4-5",
        resolver_model="claude-sonnet-5-5",
    )
    services.store.save(t, "co_andes", res)

    text = signed_in(client, "emp_laura").get("/tickets/demo_9").text
    assert "<script>alert" not in text and "<img src=x" not in text
    assert "&lt;script&gt;" in text

"""7d: submit a new ticket from the UI (live mode), or learn how to enable it (fake mode)."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from decline_desk.demo import build_services, load_demo
from decline_desk.llm.client import Completion, FakeLLM, Usage
from decline_desk.web.app import create_app
from tests.web.conftest import signed_in


def test_in_demo_mode_the_form_explains_how_to_go_live(client):
    page = signed_in(client, "emp_maria").get("/tickets/new")
    assert page.status_code == 200
    assert "LLM_MODE=live" in page.text and "README" in page.text
    assert "<textarea" not in page.text


def test_in_demo_mode_posting_a_ticket_is_refused(client):
    resp = signed_in(client, "emp_maria").post("/tickets/new", data={"text": "hola"})
    assert resp.status_code == 409


def test_inbox_links_to_the_new_ticket_page(client):
    assert 'href="/tickets/new"' in signed_in(client, "emp_maria").get("/").text


@pytest.fixture
def live_client():
    svc = build_services(sqlite3.connect(":memory:", check_same_thread=False))
    load_demo(svc)
    triage = {
        "language": "es",
        "intent": "card_decline",
        "merchant": "Uber",
        "country": "MX",
        "amount": None,
        "currency": None,
        "days_ago": None,
    }
    llm = FakeLLM(
        [
            Completion(json.dumps(triage), Usage("claude-haiku-4-5", 300, 50, 400)),
            Completion(
                json.dumps(
                    {"reply": "Hola Maria, pedimos habilitar Mexico.", "proposed_action": None}
                ),
                Usage("claude-sonnet-5-5", 900, 120, 1200),
            ),
        ]
    )
    app = create_app(svc, client=llm, models=("claude-haiku-4-5", "claude-sonnet-5-5"))
    return TestClient(app, follow_redirects=False), svc, llm


def test_live_mode_resolves_a_submitted_ticket_and_shows_it(live_client):
    client, svc, llm = live_client
    c = signed_in(client, "emp_maria")
    assert "<textarea" in c.get("/tickets/new").text

    resp = c.post("/tickets/new", data={"text": "Me rechazaron un Uber en Mexico"})
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/tickets/t_")

    page = c.get(location).text
    assert "Me rechazaron un Uber en Mexico" in page
    assert "Allow card in MX for 24 hours" in page
    assert len(llm.calls) == 2
    assert location.split("/")[-1] in c.get("/").text  # shows up in her inbox


def test_new_ticket_belongs_to_the_signed_in_employee(live_client):
    client, svc, _ = live_client
    signed_in(client, "emp_maria").post("/tickets/new", data={"text": "rechazada"})
    row = svc.conn.execute("SELECT employee_id FROM tickets WHERE id LIKE 't_%'").fetchone()
    assert row[0] == "emp_maria"


def test_new_ticket_requires_sign_in_and_same_site(live_client):
    client, _, llm = live_client
    assert client.post("/tickets/new", data={"text": "x"}).status_code == 303  # to sign-in
    resp = signed_in(client, "emp_maria").post(
        "/tickets/new", data={"text": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert llm.calls == []


def test_overlong_ticket_is_rejected_before_any_model_call(live_client):
    client, _, llm = live_client
    resp = signed_in(client, "emp_maria").post("/tickets/new", data={"text": "a" * 5001})
    assert resp.status_code == 400
    assert llm.calls == []

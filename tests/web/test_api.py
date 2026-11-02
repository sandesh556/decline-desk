"""6d: JSON mirror of the ticket page for integrations."""

from tests.web.conftest import signed_in


def test_ticket_json_has_the_resolution_and_live_approval_state(client):
    c = signed_in(client, "emp_laura")
    data = c.get("/api/tickets/demo_1").json()
    assert data["id"] == "demo_1"
    assert data["employee"] == "Maria Gomez"
    assert data["status"] == "drafted"
    assert data["causes"] == ["country_restricted"]
    assert data["action"] == {
        "type": "allow_country",
        "card_id": "card_maria",
        "country": "MX",
        "hours": 24,
    }
    assert data["approval"] == "pending_approval"
    assert data["cost_usd"] == "0.004920"
    c.post("/tickets/demo_1/approve")
    assert c.get("/api/tickets/demo_1").json()["approval"] == "executed"


def test_ticket_without_an_action_has_no_approval(client):
    data = signed_in(client, "emp_laura").get("/api/tickets/demo_2").json()
    assert data["action"] is None and data["approval"] is None


def test_other_company_and_unknown_tickets_are_404(client):
    c = signed_in(client, "emp_laura")
    assert c.get("/api/tickets/demo_3").status_code == 404
    assert c.get("/api/tickets/nope").status_code == 404
    assert c.get("/api/tickets/nope").json() == {"error": "not found"}


def test_signed_out_gets_401(client):
    resp = client.get("/api/tickets/demo_1")
    assert resp.status_code == 401
    assert resp.json() == {"error": "sign in required"}


def test_json_never_contains_a_full_card_number(client):
    text = signed_in(client, "emp_bia").get("/api/tickets/demo_4").text
    assert "4111" not in text

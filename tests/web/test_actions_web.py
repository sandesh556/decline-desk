"""6c: approve and reject from the ticket page."""

from tests.web.conftest import signed_in


def overrides(services):
    return services.conn.execute("SELECT COUNT(*) FROM control_overrides").fetchone()[0]


def test_admin_approves_and_sees_the_result(client, services):
    c = signed_in(client, "emp_laura")
    resp = c.post("/tickets/demo_1/approve")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/tickets/demo_1?msg=approved"
    page = c.get(resp.headers["location"]).text
    assert "Action approved and executed." in page
    assert "Approved" in page and "/approve" not in page
    assert overrides(services) == 1


def test_double_approve_executes_once(client, services):
    c = signed_in(client, "emp_laura")
    c.post("/tickets/demo_1/approve")
    again = c.post("/tickets/demo_1/approve")
    assert again.status_code == 303
    assert overrides(services) == 1


def test_non_admin_cannot_approve(client, services):
    resp = signed_in(client, "emp_maria").post("/tickets/demo_1/approve")
    assert resp.status_code == 403
    assert "Only a company admin" in resp.text
    assert overrides(services) == 0


def test_admin_of_another_company_cannot_even_see_the_ticket(client, services):
    assert signed_in(client, "emp_bia").post("/tickets/demo_1/approve").status_code == 404
    assert overrides(services) == 0


def test_reject_with_a_reason(client, services):
    c = signed_in(client, "emp_laura")
    resp = c.post("/tickets/demo_1/reject", data={"reason": "No travel approved to Mexico"})
    assert resp.headers["location"] == "/tickets/demo_1?msg=rejected"
    page = c.get(resp.headers["location"]).text
    assert "Rejected" in page and "No travel approved to Mexico" in page
    assert overrides(services) == 0


def test_reject_needs_a_reason(client):
    c = signed_in(client, "emp_laura")
    resp = c.post("/tickets/demo_1/reject", data={"reason": "   "})
    assert resp.status_code == 400
    assert "Please give a reason" in resp.text
    assert "Pending approval" in c.get("/tickets/demo_1").text


def test_cannot_reject_after_approving(client):
    c = signed_in(client, "emp_laura")
    c.post("/tickets/demo_1/approve")
    resp = c.post("/tickets/demo_1/reject", data={"reason": "changed my mind"})
    assert resp.status_code == 409
    assert "already been decided" in resp.text


def test_cannot_approve_after_rejecting(client, services):
    c = signed_in(client, "emp_laura")
    c.post("/tickets/demo_1/reject", data={"reason": "no"})
    assert c.post("/tickets/demo_1/approve").status_code == 409
    assert overrides(services) == 0


def test_ticket_without_an_action_has_nothing_to_approve(client):
    resp = signed_in(client, "emp_laura").post("/tickets/demo_2/approve")
    assert resp.status_code == 409
    assert "No action is waiting" in resp.text


def test_signed_out_post_goes_to_sign_in(client, services):
    resp = client.post("/tickets/demo_1/approve")
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    assert overrides(services) == 0


def test_cross_site_post_is_refused(client, services):
    resp = signed_in(client, "emp_laura").post(
        "/tickets/demo_1/approve", headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert overrides(services) == 0


def test_same_site_origin_is_allowed(client, services):
    resp = signed_in(client, "emp_laura").post(
        "/tickets/demo_1/approve", headers={"Origin": "http://testserver"}
    )
    assert resp.status_code == 303


def test_message_parameter_cannot_inject_arbitrary_text(client):
    page = signed_in(client, "emp_laura").get("/tickets/demo_1?msg=Call+us+at+evil").text
    assert "Call us at evil" not in page

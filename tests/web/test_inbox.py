"""6a: sign-in picker and the ops inbox."""

import re
from decimal import Decimal

from decline_desk.domain.models import Company, Employee
from tests.web.conftest import signed_in

EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿⬀-⯿]")


def test_without_a_user_you_get_the_sign_in_picker(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Sign in as" in page.text
    assert "Laura Rios" in page.text and "Beatriz Alves" in page.text


def test_signing_in_sets_a_cookie_and_redirects_to_the_inbox(client):
    resp = client.post("/login", data={"user": "emp_laura"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert resp.cookies.get("user") == "emp_laura"


def test_unknown_user_cookie_falls_back_to_sign_in(client):
    assert "Sign in as" in signed_in(client, "emp_ghost").get("/").text


def test_signing_in_as_an_unknown_user_is_rejected(client):
    assert client.post("/login", data={"user": "emp_ghost"}).status_code == 400


def test_inbox_shows_only_your_companys_tickets(client):
    page = signed_in(client, "emp_laura").get("/").text  # Andes admin
    assert "Maria Gomez" in page and "Carlos Ruiz" in page
    assert "Lucas Lima" not in page and "Ana Souza" not in page


def test_each_row_has_a_plain_text_status_label_and_links_to_the_ticket(client):
    page = signed_in(client, "emp_bia").get("/").text  # Paulista admin
    assert "Pending approval" in page
    assert "Escalated" in page
    assert re.search(r'href="/tickets/demo_\w+"', page)


def test_inbox_lists_newest_first(client):
    page = signed_in(client, "emp_bia").get("/").text
    ids = re.findall(r'href="/tickets/(demo_\w+)"', page)
    assert ids == sorted(ids, reverse=True)


def test_empty_state(client, services):
    services.repo.add_company(
        Company(
            id="co_empty",
            name="Empty Co",
            timezone="UTC",
            wallet_balance_usd=Decimal("0"),
            limit_cap_usd=Decimal("1"),
        )
    )
    services.repo.add_employee(
        Employee(id="emp_empty", company_id="co_empty", name="Nobody", language="en", is_admin=True)
    )
    assert "No tickets yet" in signed_in(client, "emp_empty").get("/").text


def test_pages_use_no_emoji(client):
    for user in ("emp_laura", "emp_bia"):
        assert not EMOJI.search(signed_in(client, user).get("/").text)
    assert not EMOJI.search(client.get("/").text)


def test_sign_out_clears_the_cookie(client):
    signed_in(client, "emp_laura")
    resp = client.post("/logout")
    assert resp.status_code == 303
    assert 'user=""' in resp.headers.get("set-cookie", "") or "Max-Age=0" in resp.headers.get(
        "set-cookie", ""
    )


def test_employee_sees_only_their_own_tickets(client):
    page = signed_in(client, "emp_maria").get("/").text
    assert "Maria Gomez" in page and "Carlos Ruiz" not in page


def test_employee_cannot_open_or_fetch_a_colleagues_ticket(client):
    c = signed_in(client, "emp_maria")
    assert c.get("/tickets/demo_2").status_code == 404
    assert c.get("/api/tickets/demo_2").status_code == 404
    assert c.get("/tickets/demo_1").status_code == 200  # her own


def test_employee_with_several_tickets_sees_all_of_them(client):
    page = signed_in(client, "emp_lucas").get("/").text
    assert "demo_3" in page and "demo_4" in page

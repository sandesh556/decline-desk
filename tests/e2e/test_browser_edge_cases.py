"""6e (continued): happy paths across every demo ticket, failure modes, and edge cases,
in a real browser against the live server."""

import json
import re
from datetime import timedelta

import pytest
from playwright.sync_api import Page, expect

from decline_desk.agent.resolver import resolve
from decline_desk.data.seed import BASE_TIME
from decline_desk.domain.models import Ticket
from decline_desk.llm.client import Completion, FakeLLM, Usage
from tests.e2e.conftest import sign_in

LAURA = "Laura Rios (admin) - Andes Logistica"
BIA = "Beatriz Alves (admin) - Paulista Alimentos"
MARIA = "Maria Gomez - Andes Logistica"


def overrides(svc) -> int:
    return svc.conn.execute("SELECT COUNT(*) FROM control_overrides").fetchone()[0]


def add_ticket(svc, ticket_id, employee, company, text, minutes=40):
    t = Ticket(
        id=ticket_id,
        employee_id=employee,
        text=text,
        created_at=BASE_TIME + timedelta(minutes=minutes),
    )
    triage = Completion(
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
        Usage("claude-haiku-4-5", 10, 5, 10),
    )
    svc.store.save(
        t,
        company,
        resolve(
            t,
            repo=svc.repo,
            client=FakeLLM([triage]),
            approvals=svc.approvals,
            triage_model="claude-haiku-4-5",
            resolver_model="claude-sonnet-5-5",
        ),
    )


# --- happy paths ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("person", "ticket"),
    [
        (LAURA, "demo_1"),
        (LAURA, "demo_2"),
        (BIA, "demo_3"),
        (BIA, "demo_4"),
        (BIA, "demo_5"),
        (BIA, "demo_6"),
    ],
)
def test_every_demo_ticket_opens_cleanly_for_its_admin(
    page: Page, live, console_errors, person, ticket
):
    base, _ = live
    sign_in(page, base, person)
    resp = page.goto(f"{base}/tickets/{ticket}")
    assert resp.status == 200
    expect(page.get_by_role("heading", name="Reply to send")).to_be_visible()
    expect(page.get_by_role("heading", name="Trace")).to_be_visible()
    assert console_errors == []


@pytest.mark.parametrize(
    ("ticket", "action_text"),
    [
        ("demo_4", "Raise per-transaction limit to $270 for 24 hours"),
        ("demo_5", "Allow merchant category 5813 for 24 hours"),
    ],
)
def test_admin_approves_limit_and_category_actions(page: Page, live, ticket, action_text):
    base, svc = live
    sign_in(page, base, BIA)
    page.goto(f"{base}/tickets/{ticket}")
    expect(page.get_by_text(action_text)).to_be_visible()
    page.get_by_role("button", name="Approve").click()
    expect(page.locator("h1 .label")).to_have_text("Approved")
    expect(page.locator("td", has_text="Beatriz Alves").first).to_be_visible()
    assert overrides(svc) == 1


def test_inbox_label_and_api_update_after_approval(page: Page, live):
    base, _ = live
    sign_in(page, base, LAURA)
    page.goto(base + "/tickets/demo_1")
    page.get_by_role("button", name="Approve").click()
    page.get_by_role("link", name="Back to inbox").click()
    row = page.locator("tr", has_text="Maria Gomez")
    expect(row.locator(".label")).to_have_text("Approved")
    assert page.request.get(base + "/api/tickets/demo_1").json()["approval"] == "executed"


def test_reload_after_approving_does_not_approve_again(page: Page, live):
    base, svc = live
    sign_in(page, base, LAURA)
    page.goto(base + "/tickets/demo_1")
    page.get_by_role("button", name="Approve").click()
    expect(page).to_have_url(re.compile(r"/tickets/demo_1\?msg=approved$"))
    page.reload()
    page.go_back()
    page.go_forward()
    assert overrides(svc) == 1


def test_employee_sees_the_outcome_of_their_own_request(page: Page, live, browser):
    base, _ = live
    sign_in(page, base, LAURA)
    page.goto(base + "/tickets/demo_1")
    page.get_by_role("button", name="Approve").click()

    maria = browser.new_context().new_page()
    sign_in(maria, base, MARIA)
    maria.goto(base + "/tickets/demo_1")
    expect(maria.locator("h1 .label")).to_have_text("Approved")


def test_page_titles_identify_the_page(page: Page, live):
    base, _ = live
    sign_in(page, base, LAURA)
    expect(page).to_have_title("Inbox - DeclineDesk")
    page.goto(base + "/tickets/demo_1")
    expect(page).to_have_title("Ticket demo_1 - DeclineDesk")


# --- failure modes ----------------------------------------------------------------------------


def test_employee_inbox_shows_only_their_own_tickets(page: Page, live):
    base, _ = live
    sign_in(page, base, MARIA)
    expect(page.locator("tbody tr")).to_have_count(1)
    expect(page.get_by_text("Carlos Ruiz")).to_have_count(0)


def test_employee_cannot_open_a_colleagues_ticket(page: Page, live):
    base, _ = live
    sign_in(page, base, MARIA)
    assert page.goto(base + "/tickets/demo_2").status == 404  # Carlos's ticket
    assert page.request.get(base + "/api/tickets/demo_2").status == 404


def test_signed_out_direct_link_lands_on_sign_in(page: Page, live):
    base, _ = live
    page.goto(base + "/tickets/demo_1")
    expect(page.get_by_role("heading", name="Sign in as")).to_be_visible()


def test_tampered_cookie_is_treated_as_signed_out(page: Page, live, context):
    base, _ = live
    context.add_cookies([{"name": "user", "value": "emp_admin_of_everything", "url": base}])
    page.goto(base + "/")
    expect(page.get_by_role("heading", name="Sign in as")).to_be_visible()


def test_cross_site_approve_is_refused(page: Page, live):
    base, svc = live
    sign_in(page, base, LAURA)
    resp = page.request.post(
        base + "/tickets/demo_1/approve",
        headers={"Origin": "https://evil.example"},
        max_redirects=0,
    )
    assert resp.status == 403
    assert overrides(svc) == 0


def test_forced_approve_on_a_ticket_with_no_action(page: Page, live):
    base, _ = live
    sign_in(page, base, LAURA)
    resp = page.request.post(base + "/tickets/demo_2/approve", max_redirects=0)
    assert resp.status == 409


def test_whitespace_only_reason_is_rejected(page: Page, live):
    base, _ = live
    sign_in(page, base, LAURA)
    page.goto(base + "/tickets/demo_1")
    page.get_by_label("Reason").fill("     ")
    page.get_by_role("button", name="Reject").click()
    expect(page.get_by_text("Please give a reason for rejecting.")).to_be_visible()
    expect(page.locator("h1 .label")).to_have_text("Pending approval")


def test_unknown_route_is_404(page: Page, live):
    base, _ = live
    assert page.goto(base + "/admin").status == 404


# --- edge cases -------------------------------------------------------------------------------


def test_html_in_a_rejection_reason_is_shown_as_text(page: Page, live):
    base, _ = live
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    sign_in(page, base, LAURA)
    page.goto(base + "/tickets/demo_1")
    page.get_by_label("Reason").fill('<img src=x onerror="alert(1)"> not approved')
    page.get_by_role("button", name="Reject").click()
    expect(page.get_by_text('<img src=x onerror="alert(1)"> not approved')).to_be_visible()
    page.wait_for_timeout(300)
    assert dialogs == []


def test_very_long_ticket_wraps_on_a_phone(page: Page, live):
    base, svc = live
    add_ticket(
        svc,
        "demo_long",
        "emp_maria",
        "co_andes",
        "rechazada " + "A" * 3000 + " " + "palabra " * 300,
    )
    page.set_viewport_size({"width": 390, "height": 800})
    sign_in(page, base, LAURA)
    for path in ("/", "/tickets/demo_long"):
        page.goto(base + path)
        overflow = page.evaluate(
            "document.scrollingElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, path


def test_accented_text_renders_correctly(page: Page, live):
    base, _ = live
    sign_in(page, base, BIA)
    page.goto(base + "/tickets/demo_3")
    expect(page.get_by_text(re.compile("Para sua proteção"))).to_be_visible()


def test_keyboard_only_admin_can_reject(page: Page, live):
    base, _ = live
    sign_in(page, base, LAURA)
    page.goto(base + "/tickets/demo_1")
    page.get_by_label("Reason").focus()
    page.keyboard.type("Rejected using only the keyboard")
    page.keyboard.press("Enter")
    expect(page.get_by_text("Action rejected.")).to_be_visible()


def test_each_employee_sees_exactly_their_own_tickets(page: Page, live):
    base, _ = live
    sign_in(page, base, "Priya Rao - Paulista Alimentos")
    expect(page.locator("tbody tr")).to_have_count(1)  # her own ambiguous AWS ticket
    page.get_by_role("button", name="Sign out").click()
    sign_in(page, base, "Ana Souza - Paulista Alimentos")
    expect(page.locator("tbody tr")).to_have_count(1)
    page.get_by_role("button", name="Sign out").click()
    sign_in(page, base, "Carlos Ruiz - Andes Logistica")
    expect(page.locator("tbody tr")).to_have_count(1)

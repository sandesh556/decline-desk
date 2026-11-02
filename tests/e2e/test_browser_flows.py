"""6e: real-browser tests (Chromium via Playwright) of every ops flow, against the app
running in a local uvicorn server on fresh demo data."""

import json
import re
from datetime import timedelta

from playwright.sync_api import Page, expect

from decline_desk.agent.resolver import resolve
from decline_desk.data.seed import BASE_TIME
from decline_desk.domain.models import Ticket
from decline_desk.llm.client import Completion, FakeLLM, Usage
from tests.e2e.conftest import sign_in


def overrides(svc) -> int:
    return svc.conn.execute("SELECT COUNT(*) FROM control_overrides").fetchone()[0]


def test_sign_in_and_see_only_your_companys_inbox(page: Page, live, console_errors):
    base, _ = live
    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    expect(page.get_by_role("heading", name=re.compile("Decline tickets"))).to_be_visible()
    rows = page.locator("tbody tr")
    expect(rows).to_have_count(2)
    expect(page.get_by_text("Maria Gomez")).to_be_visible()
    expect(page.get_by_text("Lucas Lima")).to_have_count(0)
    expect(page.locator(".label", has_text="Pending approval")).to_be_visible()
    assert console_errors == []


def test_open_ticket_and_approve_the_action(page: Page, live, console_errors):
    base, svc = live
    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    page.get_by_role("link", name=re.compile("rechazada ayer en un Uber")).click()

    expect(page.get_by_text("Country restricted")).to_be_visible()
    expect(page.get_by_text("is allowed in CO; the purchase was in MX")).to_be_visible()
    expect(page.get_by_text("Allow card in MX for 24 hours")).to_be_visible()

    page.get_by_role("button", name="Approve").click()
    expect(page.get_by_text("Action approved and executed.")).to_be_visible()
    expect(page.locator("h1 .label")).to_have_text("Approved")
    expect(page.get_by_role("button", name="Approve")).to_have_count(0)
    expect(page.locator("td", has_text="executed")).to_be_visible()
    assert overrides(svc) == 1
    assert console_errors == []


def test_double_approve_from_two_open_tabs_executes_once(page: Page, live, context):
    base, svc = live
    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    page.goto(base + "/tickets/demo_1")
    second = context.new_page()
    second.goto(base + "/tickets/demo_1")  # stale tab, still shows the Approve button

    page.get_by_role("button", name="Approve").click()
    expect(page.get_by_text("Action approved and executed.")).to_be_visible()
    second.get_by_role("button", name="Approve").click()
    expect(second.locator("h1 .label")).to_have_text("Approved")
    assert overrides(svc) == 1


def test_reject_requires_a_reason_then_records_it(page: Page, live):
    base, svc = live
    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    page.goto(base + "/tickets/demo_1")

    page.get_by_role("button", name="Reject").click()
    expect(page.get_by_text("Please give a reason for rejecting.")).to_be_visible()

    page.get_by_label("Reason").fill("No travel approved to Mexico this week")
    page.get_by_role("button", name="Reject").click()
    expect(page.get_by_text("Action rejected.")).to_be_visible()
    expect(page.locator("h1 .label")).to_have_text("Rejected")
    expect(page.get_by_text("No travel approved to Mexico this week")).to_be_visible()
    assert overrides(svc) == 0


def test_employee_cannot_approve_even_by_forcing_the_request(page: Page, live):
    base, svc = live
    sign_in(page, base, "Maria Gomez - Andes Logistica")
    page.goto(base + "/tickets/demo_1")
    expect(page.get_by_text("Waiting for a company admin to approve.")).to_be_visible()
    expect(page.get_by_role("button", name="Approve")).to_have_count(0)

    forced = page.request.post(base + "/tickets/demo_1/approve", max_redirects=0)
    assert forced.status == 403
    assert overrides(svc) == 0


def test_injection_ticket_shows_warnings_and_only_the_safe_action(page: Page, live):
    base, _ = live
    sign_in(page, base, "Beatriz Alves (admin) - Paulista Alimentos")
    page.goto(base + "/tickets/demo_4")
    expect(page.get_by_text("Possible prompt injection in the ticket text")).to_be_visible()
    expect(
        page.get_by_text(re.compile(r"Model action refused: \$50000 exceeds 2x"))
    ).to_be_visible()
    expect(page.get_by_text("Raise per-transaction limit to $270 for 24 hours")).to_be_visible()


def test_fraud_ticket_is_escalated_with_no_controls(page: Page, live):
    base, _ = live
    sign_in(page, base, "Beatriz Alves (admin) - Paulista Alimentos")
    page.goto(base + "/tickets/demo_3")
    expect(page.locator("h1 .label")).to_have_text("Escalated")
    expect(page.get_by_text(re.compile("time de segurança"))).to_be_visible()
    expect(page.get_by_role("button", name="Approve")).to_have_count(0)


def test_ambiguous_ticket_shows_the_clarifying_question(page: Page, live):
    base, _ = live
    sign_in(page, base, "Beatriz Alves (admin) - Paulista Alimentos")
    page.goto(base + "/tickets/demo_6")
    expect(page.locator("h1 .label")).to_have_text("Needs info")
    expect(page.get_by_text(re.compile("Which one do you mean"))).to_be_visible()


def test_script_in_a_ticket_is_shown_as_text_and_never_runs(page: Page, live):
    base, svc = live
    evil = '<script>alert("x")</script><img src=x onerror="alert(1)"> rechazada'
    t = Ticket(
        id="demo_9",
        employee_id="emp_maria",
        text=evil,
        created_at=BASE_TIME + timedelta(minutes=30),
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
        "co_andes",
        resolve(
            t,
            repo=svc.repo,
            client=FakeLLM([triage]),
            approvals=svc.approvals,
            triage_model="claude-haiku-4-5",
            resolver_model="claude-sonnet-5-5",
        ),
    )
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    page.goto(base + "/tickets/demo_9")
    expect(page.locator(".panel.text").first).to_contain_text('<script>alert("x")</script>')
    page.wait_for_timeout(300)
    assert dialogs == []
    assert page.locator("img[src=x]").count() == 0


def test_other_companys_ticket_url_shows_not_found(page: Page, live):
    base, _ = live
    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    resp = page.goto(base + "/tickets/demo_3")
    assert resp.status == 404
    expect(page.get_by_role("heading", name="Ticket not found")).to_be_visible()


def test_sign_out_returns_to_the_picker(page: Page, live):
    base, _ = live
    sign_in(page, base, "Laura Rios (admin) - Andes Logistica")
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("heading", name="Sign in as")).to_be_visible()


def test_inbox_fits_a_phone_screen(page: Page, live):
    base, _ = live
    page.set_viewport_size({"width": 390, "height": 800})
    sign_in(page, base, "Beatriz Alves (admin) - Paulista Alimentos")
    for path in ("/", "/tickets/demo_4"):
        page.goto(base + path)
        overflow = page.evaluate(
            "document.scrollingElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, path


def test_pages_show_no_emoji(page: Page, live):
    base, _ = live
    sign_in(page, base, "Beatriz Alves (admin) - Paulista Alimentos")
    for path in ("/", "/tickets/demo_1", "/tickets/demo_3", "/tickets/demo_4"):
        page.goto(base + path)
        text = page.locator("body").inner_text()
        assert not re.search("[\U0001f300-\U0001faff☀-➿]", text), path

"""Ops web UI: a server-rendered inbox for decline tickets.

Plain HTML, no JavaScript framework. Templates autoescape everything, so ticket text can
never inject markup. Identity is a demo cookie picked on the sign-in page. Authorization
lives in the services (approvals enforce admin-of-company), not in the templates.
"""

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from decline_desk.demo import Services
from decline_desk.domain.models import Employee
from decline_desk.policy.approvals import ApprovalStatus
from decline_desk.repo import NotFound
from decline_desk.store import StoredTicket

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(default=True),
)

LABELS = {
    "drafted": "Ready to send",
    "needs_info": "Needs info",
    "escalated": "Escalated",
}
APPROVAL_LABELS = {
    ApprovalStatus.PENDING: "Pending approval",
    ApprovalStatus.EXECUTED: "Approved",
    ApprovalStatus.REJECTED: "Rejected",
}


@dataclass(frozen=True)
class Row:
    ticket: object
    employee_name: str
    label: str
    attention: bool
    received: str


def create_app(svc: Services) -> FastAPI:
    app = FastAPI(title="DeclineDesk", docs_url=None, redoc_url=None)

    def page(name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        return HTMLResponse(TEMPLATES.get_template(name).render(**ctx), status_code=status_code)

    def current_user(request: Request) -> Employee | None:
        user_id = request.cookies.get("user")
        if not user_id:
            return None
        try:
            return svc.repo.employee(user_id)
        except NotFound:
            return None

    def status_label(t: StoredTicket) -> tuple[str, bool]:
        pending_id = t.view.get("pending_action_id")
        if pending_id:
            status = svc.approvals.get(pending_id).status
            return APPROVAL_LABELS[status], status is ApprovalStatus.PENDING
        return LABELS[t.view["status"]], t.view["status"] == "escalated"

    def login_page(status_code: int = 200) -> HTMLResponse:
        people = []
        for (emp_id,) in svc.conn.execute("SELECT id FROM employees ORDER BY company_id, id"):
            e = svc.repo.employee(emp_id)
            people.append((e, svc.repo.company(e.company_id).name))
        return page("login.html", status_code, user=None, people=people)

    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request) -> HTMLResponse:
        user = current_user(request)
        if user is None:
            return login_page()
        company = svc.repo.company(user.company_id)
        tz = ZoneInfo(company.timezone)
        rows = []
        for t in svc.store.list_for_company(company.id):
            label, attention = status_label(t)
            received = t.ticket.created_at.astimezone(tz).strftime("%d %b %H:%M")
            rows.append(Row(t.ticket, t.employee_name, label, attention, received))
        return page("inbox.html", user=user, company=company, rows=rows)

    @app.post("/login")
    def login(user: str = Form(...)):
        try:
            svc.repo.employee(user)
        except NotFound:
            return login_page(status_code=400)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("user", user, httponly=True, samesite="lax")
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/", status_code=303)
        resp.delete_cookie("user")
        return resp

    return app

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


ACTION_TEXT = {
    "allow_country": "Allow card in {country} for {hours} hours",
    "unblock_mcc": "Allow merchant category {mcc} for {hours} hours",
    "temp_limit_increase": "Raise per-transaction limit to ${new_per_txn_limit_usd} for "
    "{hours} hours",
    "unfreeze_card": "Unfreeze card",
}
FLAG_TEXT = {
    "injection_suspected": "Possible prompt injection in the ticket text",
    "draft_rejected": "The model's draft failed the reply check; the template reply is used",
    "draft_fallback": "The drafting model was unavailable; the template reply is used",
}


def describe_action(action: dict) -> str:
    return ACTION_TEXT[action["type"]].format(**action)


def describe_flag(flag: str) -> str:
    if flag.startswith("model_action_refused:"):
        return "Model action refused:" + flag.split(":", 1)[1]
    return FLAG_TEXT.get(flag, flag)


def describe_cause(cause: str) -> str:
    return cause.replace("_", " ").capitalize()


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

    def not_found(user: Employee) -> HTMLResponse:
        return page("not_found.html", 404, user=user)

    def ticket_page(user: Employee, t: StoredTicket, message: str = "", status_code: int = 200):
        company = svc.repo.company(t.company_id)
        label, attention = status_label(t)
        pending_id = t.view.get("pending_action_id")
        approval = svc.approvals.get(pending_id) if pending_id else None
        approval_open = approval is not None and approval.status is ApprovalStatus.PENDING
        names = {}

        def name(emp_id: str) -> str:
            if emp_id not in names:
                try:
                    names[emp_id] = svc.repo.employee(emp_id).name
                except NotFound:
                    names[emp_id] = emp_id
            return names[emp_id]

        audit = []
        if approval is not None:
            audit = [
                {"event": e.event, "actor": name(e.actor), "detail": e.detail}
                for e in svc.approvals.audit(pending_id)
            ]
        return page(
            "ticket.html",
            status_code,
            user=user,
            t=t,
            label=label,
            attention=attention,
            received=t.ticket.created_at.astimezone(ZoneInfo(company.timezone)).strftime(
                "%d %b %H:%M"
            ),
            flags=[describe_flag(f) for f in t.view["flags"]],
            causes=list(
                zip(
                    [describe_cause(c) for c in t.view["causes"]],
                    t.view["evidence"],
                    strict=False,
                )
            ),
            action=describe_action(t.view["action"]) if t.view.get("action") else None,
            approval_open=approval_open,
            can_decide=approval_open and user.is_admin and user.company_id == t.company_id,
            audit=audit,
            message=message,
        )

    @app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
    def ticket(ticket_id: str, request: Request) -> HTMLResponse:
        user = current_user(request)
        if user is None:
            return login_page()
        t = svc.store.get(ticket_id, user.company_id)
        if t is None:
            return not_found(user)
        return ticket_page(user, t, message=request.query_params.get("msg", ""))

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

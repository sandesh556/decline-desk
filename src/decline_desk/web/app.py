"""Ops web UI: a server-rendered inbox for decline tickets.

Plain HTML, no JavaScript framework. Templates autoescape everything, so ticket text can
never inject markup. Identity is a demo cookie picked on the sign-in page. Authorization
lives in the services (approvals enforce admin-of-company), not in the templates.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from decline_desk.agent.resolver import resolve
from decline_desk.data.seed import BASE_TIME
from decline_desk.demo import Services
from decline_desk.domain.models import Employee, Ticket
from decline_desk.llm.client import LLMClient
from decline_desk.policy.approvals import ApprovalStatus, Forbidden, InvalidTransition
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


# Result messages come from this fixed table; the URL only carries a code, so it can't be
# used to show arbitrary text on a trusted page.
MESSAGES = {
    "approved": "Action approved and executed.",
    "rejected": "Action rejected.",
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


MAX_TICKET_INPUT = 5000


def create_app(
    svc: Services,
    *,
    client: LLMClient | None = None,
    models: tuple[str, str] = ("claude-haiku-4-5", "claude-sonnet-5-5"),
    clock: Callable[[], datetime] = lambda: BASE_TIME + timedelta(minutes=45),
) -> FastAPI:
    """client is the live LLM client, or None in fake mode (new tickets are then disabled).
    clock defaults to a moment just after the seeded data, so "yesterday" in a new ticket
    lines up with the demo history."""
    app = FastAPI(title="DeclineDesk", docs_url=None, redoc_url=None)

    def page(name: str, status_code: int = 200, **ctx) -> HTMLResponse:
        return HTMLResponse(TEMPLATES.get_template(name).render(**ctx), status_code=status_code)

    def cross_site(request: Request) -> bool:
        """Refuse state-changing requests sent from another site (CSRF defence in depth,
        alongside the SameSite=Lax cookie)."""
        origin = request.headers.get("origin")
        return origin is not None and urlsplit(origin).netloc != request.url.netloc

    def current_user(request: Request) -> Employee | None:
        user_id = request.cookies.get("user")
        if not user_id:
            return None
        try:
            return svc.repo.employee(user_id)
        except NotFound:
            return None

    def visible(user: Employee, ticket_id: str) -> StoredTicket | None:
        """Admins see their whole company; everyone else sees only their own tickets."""
        return svc.store.get(ticket_id, user.company_id, None if user.is_admin else user.id)

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
        for t in svc.store.list_for_company(company.id, None if user.is_admin else user.id):
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

    def new_ticket_page(user: Employee, message: str = "", status_code: int = 200):
        return page(
            "new_ticket.html",
            status_code,
            user=user,
            live=client is not None,
            models=models,
            max_chars=MAX_TICKET_INPUT,
            message=message,
        )

    @app.get("/tickets/new", response_class=HTMLResponse)
    def new_ticket_form(request: Request) -> HTMLResponse:
        user = current_user(request)
        return login_page() if user is None else new_ticket_page(user)

    @app.post("/tickets/new")
    def new_ticket(request: Request, text: str = Form("")):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/", status_code=303)
        if cross_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        if client is None:
            return new_ticket_page(user, "Live mode is off; see below.", 409)
        if len(text) > MAX_TICKET_INPUT:
            return new_ticket_page(
                user, f"Please keep it under {MAX_TICKET_INPUT} characters.", 400
            )
        ticket = Ticket(
            id=f"t_{uuid.uuid4().hex[:10]}", employee_id=user.id, text=text, created_at=clock()
        )
        res = resolve(
            ticket,
            repo=svc.repo,
            client=client,
            approvals=svc.approvals,
            triage_model=models[0],
            resolver_model=models[1],
        )
        svc.store.save(ticket, user.company_id, res)
        return RedirectResponse(f"/tickets/{ticket.id}", status_code=303)

    @app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
    def ticket(ticket_id: str, request: Request) -> HTMLResponse:
        user = current_user(request)
        if user is None:
            return login_page()
        t = visible(user, ticket_id)
        if t is None:
            return not_found(user)
        return ticket_page(user, t, message=MESSAGES.get(request.query_params.get("msg", ""), ""))

    def decide(ticket_id: str, request: Request, act):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/", status_code=303)
        if cross_site(request):
            return HTMLResponse("Cross-site request refused.", status_code=403)
        t = visible(user, ticket_id)
        if t is None:
            return not_found(user)
        pending_id = t.view.get("pending_action_id")
        if not pending_id:
            return ticket_page(user, t, "No action is waiting for approval on this ticket.", 409)
        try:
            code = act(user, pending_id)
        except Forbidden:
            return ticket_page(user, t, "Only a company admin can approve or reject actions.", 403)
        except InvalidTransition:
            return ticket_page(user, t, "This action has already been decided.", 409)
        except ValueError as exc:
            return ticket_page(user, t, str(exc), 400)
        return RedirectResponse(f"/tickets/{ticket_id}?msg={code}", status_code=303)

    @app.post("/tickets/{ticket_id}/approve")
    def approve(ticket_id: str, request: Request):
        def act(user: Employee, pending_id: str) -> str:
            svc.approvals.approve(pending_id, approver_id=user.id)
            return "approved"

        return decide(ticket_id, request, act)

    @app.post("/tickets/{ticket_id}/reject")
    def reject(ticket_id: str, request: Request, reason: str = Form("")):
        def act(user: Employee, pending_id: str) -> str:
            if not reason.strip():
                raise ValueError("Please give a reason for rejecting.")
            svc.approvals.reject(pending_id, approver_id=user.id, reason=reason.strip())
            return "rejected"

        return decide(ticket_id, request, act)

    @app.get("/api/tickets/{ticket_id}")
    def ticket_json(ticket_id: str, request: Request) -> JSONResponse:
        user = current_user(request)
        if user is None:
            return JSONResponse({"error": "sign in required"}, status_code=401)
        t = visible(user, ticket_id)
        if t is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        pending_id = t.view.get("pending_action_id")
        approval = svc.approvals.get(pending_id).status.value if pending_id else None
        return JSONResponse(
            {
                "id": t.ticket.id,
                "employee": t.employee_name,
                "text": t.ticket.text,
                "created_at": t.ticket.created_at.isoformat(),
                "approval": approval,
                **{
                    k: t.view[k]
                    for k in (
                        "status",
                        "language",
                        "reply",
                        "causes",
                        "evidence",
                        "action",
                        "flags",
                        "cost_usd",
                    )
                },
            }
        )

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

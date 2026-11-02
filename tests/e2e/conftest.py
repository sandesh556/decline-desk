"""Start the real app in a uvicorn thread on a free port for each test, on a fresh demo."""

import socket
import sqlite3
import threading
import time

import pytest
import uvicorn

from decline_desk.demo import build_services, load_demo
from decline_desk.web.app import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(request):
    svc = build_services(sqlite3.connect(":memory:", check_same_thread=False))
    load_demo(svc)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", svc
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def console_errors(page):
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def sign_in(page, base: str, person: str) -> None:
    page.goto(base + "/")
    page.get_by_label("Person").select_option(label=person)
    page.get_by_role("button", name="Continue").click()

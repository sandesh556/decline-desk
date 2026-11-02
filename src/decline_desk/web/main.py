"""Run the ops UI locally on a fresh in-memory demo: `make demo`, then open
http://127.0.0.1:8000. State resets on every restart."""

import sqlite3

from decline_desk.demo import build_services, load_demo
from decline_desk.web.app import create_app


def build_app():
    svc = build_services(sqlite3.connect(":memory:", check_same_thread=False))
    load_demo(svc)
    return create_app(svc)


app = build_app()

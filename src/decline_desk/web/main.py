"""Run the ops UI locally: `make demo`, then open http://127.0.0.1:8000.

Reads .env. In fake mode (default) the inbox shows the scripted demo and new tickets are
disabled. In live mode (LLM_MODE=live + your provider's key) you can also submit new
tickets, which are resolved with real model calls. State resets on every restart.
"""

import sqlite3

from decline_desk.config import Settings
from decline_desk.demo import build_services, load_demo
from decline_desk.llm.factory import make_client
from decline_desk.web.app import create_app


def build_app(settings: Settings | None = None):
    settings = settings or Settings()
    client = make_client(settings)  # None in fake mode; raises a clear error without a key
    svc = build_services(sqlite3.connect(":memory:", check_same_thread=False))
    load_demo(svc)
    return create_app(svc, client=client, models=settings.models())


app = build_app()

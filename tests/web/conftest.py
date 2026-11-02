import sqlite3

import pytest
from fastapi.testclient import TestClient

from decline_desk.demo import build_services, load_demo
from decline_desk.web.app import create_app


@pytest.fixture
def services():
    svc = build_services(sqlite3.connect(":memory:", check_same_thread=False))
    load_demo(svc)
    return svc


@pytest.fixture
def client(services) -> TestClient:
    return TestClient(create_app(services), follow_redirects=False)


def signed_in(client: TestClient, user: str) -> TestClient:
    client.cookies.set("user", user)
    return client

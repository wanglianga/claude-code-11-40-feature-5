from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity_test"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import main as seed_main  # noqa: E402


@pytest.fixture(scope="session")
def seeded():
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade"))
        conn.execute(text("create schema public"))
    init_db()
    seed_main()
    yield


@pytest.fixture(scope="session")
def client(seeded):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def researcher_token(client) -> str:
    response = client.post(
        "/api/auth/login", json={"username": "yq.li", "password": "Survey#2026"}
    )
    assert response.status_code == 200
    return response.json()["token"]


@pytest.fixture(scope="session")
def auditor_token(client) -> str:
    response = client.post(
        "/api/auth/login", json={"username": "m.cao", "password": "Audit#2026"}
    )
    assert response.status_code == 200
    return response.json()["token"]


@pytest.fixture()
def auth(researcher_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {researcher_token}"}


@pytest.fixture()
def auditor_auth(auditor_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {auditor_token}"}


@pytest.fixture()
def run_id(client, auth) -> int:
    runs = client.get("/api/runs", headers=auth).json()
    seeded = next((r for r in runs if r["run_code"] == "RUN-2026-SPRING-01"), None)
    assert seeded, "seed run missing"
    return seeded["id"]


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

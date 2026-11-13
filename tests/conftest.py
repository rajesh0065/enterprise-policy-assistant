"""
Shared test fixtures.

Each test receives a fresh SQLite database (temp file) via the APP_DB_PATH
env-var, which db.get_conn() reads per call. The app object is shared but
startup events are not triggered by TestClient without 'with'.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set DB path before any app module is imported.
_tmp = tempfile.mkdtemp()
os.environ.setdefault("APP_DB_PATH", os.path.join(_tmp, "test.db"))


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Reset the database to a clean state before every test."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("APP_DB_PATH", db_file)

    from app.db import init_db
    from app.ingestion import ingest_all

    init_db()
    ingest_all()
    yield


@pytest.fixture
def client(fresh_db):
    from app.main import app
    # No 'with' — avoids triggering startup (which also calls init_db/ingest_all)
    return TestClient(app, raise_server_exceptions=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_request(client, user: str = "emp_a1", key: str = "k1",
                 amount: float = 3000.0) -> dict:
    r = client.post(
        "/requests",
        json={"amount": amount, "currency": "INR",
              "category": "equipment", "description": "test item"},
        headers={"X-Demo-User": user, "Idempotency-Key": key},
    )
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, request_id: str, approver: str = "mgr_a1") -> dict:
    r = client.post(
        f"/requests/{request_id}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": approver},
    )
    assert r.status_code == 200, r.text
    return r.json()

"""
Test: Successful-but-timed-out downstream ticket call is reconciled to one ticket.

Scenario (architecture §5.11, §7.3):
1. Request is approved.
2. HR adapter creates the ticket then raises TimeoutError before returning.
3. The ticket row becomes UNKNOWN (intent preserved in the outbox).
4. The reconciler performs a read-back, finds the ticket, and marks it CREATED.
5. No duplicate ticket is created.

The HRAdapter.timeout_mode flag simulates step 2.
The reconcile_ticket() function implements step 4.
"""
from __future__ import annotations

import pytest

from app.integrations import HRAdapter, reconcile_ticket
from tests.conftest import make_request


@pytest.fixture
def hr_timeout(monkeypatch):
    HRAdapter.set_timeout_mode(True)
    yield
    HRAdapter.set_timeout_mode(False)


def _ticket_row(request_id: str):
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM tickets WHERE request_id=?", (request_id,)
        ).fetchone()
    finally:
        conn.close()


def test_timeout_produces_unknown_state(client, hr_timeout):
    req = make_request(client, key="to-01")
    rid = req["id"]

    r = client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "mgr_a1"},
    )
    assert r.status_code == 200
    assert r.json()["ticket_state"] == "UNKNOWN", (
        "Timed-out ticket creation must result in UNKNOWN state"
    )

    row = _ticket_row(rid)
    assert row is not None
    assert row["state"] == "UNKNOWN"


def test_reconciler_resolves_unknown_to_created(client, hr_timeout):
    req = make_request(client, key="to-02")
    rid = req["id"]

    # Approve with timeout — ticket becomes UNKNOWN
    client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "mgr_a1"},
    )
    assert _ticket_row(rid)["state"] == "UNKNOWN"

    # Reconcile — read-back finds the ticket, marks CREATED
    HRAdapter.set_timeout_mode(False)
    state = reconcile_ticket(rid)
    assert state == "CREATED", "Reconciler must resolve UNKNOWN to CREATED via read-back"

    row = _ticket_row(rid)
    assert row["state"] == "CREATED"
    assert row["external_id"] is not None


def test_exactly_one_ticket_after_reconciliation(client, hr_timeout):
    req = make_request(client, key="to-03")
    rid = req["id"]

    client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "mgr_a1"},
    )

    HRAdapter.set_timeout_mode(False)
    reconcile_ticket(rid)
    reconcile_ticket(rid)  # second call must be a no-op

    from app.db import get_conn
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM tickets WHERE request_id=?", (rid,)).fetchone()[0]
    conn.close()
    assert count == 1, "Exactly one ticket row must exist after reconciliation"


def test_no_ticket_created_before_approval(client):
    req = make_request(client, key="to-04")
    rid = req["id"]
    # Request is PENDING_APPROVAL; no ticket should exist yet
    row = _ticket_row(rid)
    assert row is None, "Ticket must not be created before approval"

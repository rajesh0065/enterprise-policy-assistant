"""
Test: After approval or rejection, further decisions cannot create new effects.

- Same approver repeating the same decision → 200, no new audit event, no new ticket.
- Conflicting decision on a finalized request → 409.
- Neither case changes the request status or duplicates a ticket.
"""
from __future__ import annotations

from tests.conftest import make_request


def _audit_count(request_id: str) -> int:
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE request_id=?", (request_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _ticket_count(request_id: str) -> int:
    from app.db import get_conn
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE request_id=?", (request_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_repeated_approve_is_idempotent(client):
    req = make_request(client, key="rep-01")
    rid = req["id"]

    r1 = client.post(f"/requests/{rid}/decision",
                     json={"decision": "APPROVE"}, headers={"X-Demo-User": "mgr_a1"})
    assert r1.status_code == 200

    audit_after_first = _audit_count(rid)
    ticket_after_first = _ticket_count(rid)

    r2 = client.post(f"/requests/{rid}/decision",
                     json={"decision": "APPROVE"}, headers={"X-Demo-User": "mgr_a1"})
    assert r2.status_code == 200
    assert r2.json()["request"]["status"] == "APPROVED"

    # No new audit event or ticket on replay
    assert _audit_count(rid) == audit_after_first, "Replay must not add audit event"
    assert _ticket_count(rid) == ticket_after_first, "Replay must not create a second ticket"


def test_repeated_reject_is_idempotent(client):
    req = make_request(client, key="rep-02")
    rid = req["id"]

    r1 = client.post(f"/requests/{rid}/decision",
                     json={"decision": "REJECT"}, headers={"X-Demo-User": "mgr_a1"})
    assert r1.status_code == 200

    r2 = client.post(f"/requests/{rid}/decision",
                     json={"decision": "REJECT"}, headers={"X-Demo-User": "mgr_a1"})
    assert r2.status_code == 200
    assert r2.json()["request"]["status"] == "REJECTED"


def test_conflicting_decision_on_approved_request_returns_409(client):
    req = make_request(client, key="rep-03")
    rid = req["id"]

    client.post(f"/requests/{rid}/decision",
                json={"decision": "APPROVE"}, headers={"X-Demo-User": "mgr_a1"})

    r = client.post(f"/requests/{rid}/decision",
                    json={"decision": "REJECT"}, headers={"X-Demo-User": "mgr_a1"})
    assert r.status_code == 409, "Conflicting decision on approved request must be 409"


def test_conflicting_decision_on_rejected_request_returns_409(client):
    req = make_request(client, key="rep-04")
    rid = req["id"]

    client.post(f"/requests/{rid}/decision",
                json={"decision": "REJECT"}, headers={"X-Demo-User": "mgr_a1"})

    r = client.post(f"/requests/{rid}/decision",
                    json={"decision": "APPROVE"}, headers={"X-Demo-User": "mgr_a1"})
    assert r.status_code == 409, "Conflicting decision on rejected request must be 409"

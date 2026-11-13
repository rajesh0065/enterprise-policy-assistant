"""
Test: Approval authorization.

- An unrelated employee (no relationship to the request) receives 404 — existence
  is not revealed (architecture §5.9).
- The requester (relationship: owner, not approver) receives 403.
- Only the designated approver may decide.
- Request status must remain PENDING_APPROVAL after unauthorized attempts.
"""
from __future__ import annotations

from tests.conftest import make_request


def test_unrelated_employee_gets_404(client):
    req = make_request(client, user="emp_a1", key="auth-01")
    rid = req["id"]

    # emp_a2 is in tenant A but has no relationship to emp_a1's request
    r = client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "emp_a2"},
    )
    assert r.status_code == 404, "Unrelated employee must get 404 (existence not revealed)"


def test_requester_cannot_approve_own_request(client):
    req = make_request(client, user="emp_a1", key="auth-02")
    rid = req["id"]

    r = client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "emp_a1"},
    )
    assert r.status_code == 403, "Requester must not be able to approve their own request"


def test_cross_tenant_user_gets_404(client):
    req = make_request(client, user="emp_a1", key="auth-03")
    rid = req["id"]

    # emp_b1 is in tenant B — cannot see tenant A requests
    r = client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "emp_b1"},
    )
    assert r.status_code == 404, "Cross-tenant user must get 404"


def test_request_unchanged_after_unauthorized_attempt(client):
    req = make_request(client, user="emp_a1", key="auth-04")
    rid = req["id"]
    assert req["status"] == "PENDING_APPROVAL"

    # Attempt by unrelated user — should fail
    client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "emp_a2"},
    )

    # Request must still be PENDING_APPROVAL; verify by approving as the designated approver
    r = client.post(
        f"/requests/{rid}/decision",
        json={"decision": "APPROVE"},
        headers={"X-Demo-User": "mgr_a1"},
    )
    assert r.status_code == 200
    assert r.json()["request"]["status"] == "APPROVED", (
        "Request must remain PENDING_APPROVAL after unauthorized attempt, then be decidable"
    )


def test_designated_approver_can_decide(client):
    req = make_request(client, user="emp_a1", key="auth-05")
    r = client.post(
        f"/requests/{req['id']}/decision",
        json={"decision": "REJECT", "comment": "over budget"},
        headers={"X-Demo-User": "mgr_a1"},
    )
    assert r.status_code == 200
    assert r.json()["request"]["status"] == "REJECTED"

"""
Test: Old, inactive policy is not used when an active replacement exists.

A-EQP-2025 (version 1, active=False) must never be cited when A-EQP-2026
(version 2, active=True) is available.  The access_and_version_filter enforces
this at the Python layer regardless of what the index returns.
"""
from __future__ import annotations


def test_inactive_policy_not_cited(client):
    r = client.post(
        "/assistant/query",
        json={"question": "What is the equipment reimbursement cap?"},
        headers={"X-Demo-User": "emp_a1"},
    )
    assert r.status_code == 200
    cited_ids = [c["document_id"] for c in r.json()["citations"]]
    assert "A-EQP-2025" not in cited_ids, (
        "Inactive policy A-EQP-2025 must never be cited"
    )


def test_active_version_is_cited(client):
    r = client.post(
        "/assistant/query",
        json={"question": "Can I get reimbursed for equipment?"},
        headers={"X-Demo-User": "emp_a1"},
    )
    assert r.status_code == 200
    cited_ids = [c["document_id"] for c in r.json()["citations"]]
    assert "A-EQP-2026" in cited_ids, (
        "Active policy A-EQP-2026 should be the cited document"
    )


def test_answer_uses_current_cap_not_old_cap(client):
    """
    The 2025 cap was INR 7,000; the 2026 cap is INR 10,000.
    The answer must reference the 2026 cap.
    """
    r = client.post(
        "/assistant/query",
        json={"question": "How much equipment can I claim?"},
        headers={"X-Demo-User": "emp_a1"},
    )
    assert r.status_code == 200
    answer = r.json()["answer"]
    assert "10,000" in answer or "10000" in answer, (
        "Answer must reference the active 2026 cap (INR 10,000), not the superseded 7,000"
    )
    # The 7,000 cap from the inactive policy must not appear
    assert "7,000" not in answer and "7000" not in answer, (
        "Inactive 2025 cap (INR 7,000) must not appear in the answer"
    )

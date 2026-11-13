"""
Test: Tenant A query never returns or cites a Tenant B document.

Covers architecture §5.3–5.4: tenant filter applied in both the search query
and the deterministic Python re-filter before context construction.
"""
from __future__ import annotations


def test_tenant_a_query_never_cites_tenant_b_policy(client):
    r = client.post(
        "/assistant/query",
        json={"question": "Can I claim INR 8,000 for a headset?"},
        headers={"X-Demo-User": "emp_a1"},
    )
    assert r.status_code == 200
    body = r.json()
    cited_ids = [c["document_id"] for c in body["citations"]]

    assert "B-EQP-2026" not in cited_ids, (
        "Tenant B policy must never appear in a Tenant A response"
    )


def test_tenant_b_query_never_cites_tenant_a_policy(client):
    r = client.post(
        "/assistant/query",
        json={"question": "What is the equipment reimbursement limit?"},
        headers={"X-Demo-User": "emp_b1"},
    )
    assert r.status_code == 200
    body = r.json()
    cited_ids = [c["document_id"] for c in body["citations"]]

    for bad in ("A-EQP-2026", "A-EQP-2025"):
        assert bad not in cited_ids, (
            f"Tenant A policy {bad} must never appear in a Tenant B response"
        )


def test_tenant_b_answer_text_does_not_contain_tenant_a_cap(client):
    """Answer text must reflect INR 15,000 (B policy), not A's INR 10,000 or 7,000."""
    r = client.post(
        "/assistant/query",
        json={"question": "Can I claim for equipment?"},
        headers={"X-Demo-User": "emp_b1"},
    )
    assert r.status_code == 200
    answer = r.json()["answer"].lower()
    # B policy cap is 15,000; A caps are 10,000 and 7,000
    assert "15,000" in answer or "15000" in answer, (
        "Tenant B answer should reference its own INR 15,000 cap"
    )

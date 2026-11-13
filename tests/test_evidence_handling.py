"""
Test: A question unsupported by retrieved sources returns insufficient_evidence.

The relevance threshold is deterministic, so this test does not depend on LLM
behaviour. When no candidate exceeds the threshold, the LLM is never called and
the response has evidence_status='insufficient_evidence' with no citations.
"""
from __future__ import annotations

from app import llm


class _TrackingAdapter:
    """Records whether generate() was called; delegates to mock if it is."""
    def __init__(self):
        self.called = False

    def generate(self, question, context_docs):
        self.called = True
        return llm.MockLLMAdapter().generate(question, context_docs)


def test_unsupported_question_returns_insufficient_evidence(client):
    tracker = _TrackingAdapter()
    llm.set_adapter(tracker)
    try:
        r = client.post(
            "/assistant/query",
            json={"question": "What is the weather forecast for tomorrow?"},
            headers={"X-Demo-User": "emp_a1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["evidence_status"] == "insufficient_evidence"
        assert body["citations"] == []
        assert "insufficient" in body["answer"].lower()
        assert not tracker.called, "LLM must not be called when evidence is insufficient"
    finally:
        llm.set_adapter(llm.MockLLMAdapter())


def test_sufficient_evidence_question_does_not_return_insufficient(client):
    """Control: a relevant question must not falsely return insufficient_evidence."""
    r = client.post(
        "/assistant/query",
        json={"question": "What is the equipment reimbursement cap?"},
        headers={"X-Demo-User": "emp_a1"},
    )
    assert r.status_code == 200
    assert r.json()["evidence_status"] == "sufficient"

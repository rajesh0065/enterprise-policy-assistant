"""
Tests: Idempotent request creation.

Same idempotency key + same payload → one record, identical response.
Same idempotency key + different payload → 409.
"""
from __future__ import annotations


def test_same_key_same_payload_returns_one_record(client):
    payload = {
        "amount": 3000.0,
        "currency": "INR",
        "category": "equipment",
        "description": "keyboard",
    }
    headers = {"X-Demo-User": "emp_a1", "Idempotency-Key": "idem-001"}

    r1 = client.post("/requests", json=payload, headers=headers)
    r2 = client.post("/requests", json=payload, headers=headers)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"], "Duplicate create must return the same record"


def test_same_key_different_payload_returns_409(client):
    headers = {"X-Demo-User": "emp_a1", "Idempotency-Key": "idem-002"}

    r1 = client.post(
        "/requests",
        json={"amount": 3000.0, "currency": "INR", "category": "equipment", "description": "keyboard"},
        headers=headers,
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/requests",
        json={"amount": 5000.0, "currency": "INR", "category": "equipment", "description": "keyboard"},
        headers=headers,
    )
    assert r2.status_code == 409, "Different payload with same key must be 409"


def test_different_keys_produce_separate_records(client):
    payload = {
        "amount": 2000.0, "currency": "INR",
        "category": "equipment", "description": "mouse",
    }
    r1 = client.post("/requests", json=payload,
                     headers={"X-Demo-User": "emp_a1", "Idempotency-Key": "key-A"})
    r2 = client.post("/requests", json=payload,
                     headers={"X-Demo-User": "emp_a1", "Idempotency-Key": "key-B"})

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"], "Different keys must produce separate records"

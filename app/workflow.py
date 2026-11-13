"""
Request service and approval state machine.

Design invariants (architecture §5.8–5.10):
- requester_id and tenant_id always come from the Principal, never from the body.
- approver_id is resolved server-side from the directory; callers cannot supply it.
- Idempotency is enforced by a unique DB constraint, not application-level check-then-insert.
- State transitions use a conditional UPDATE (WHERE status='PENDING_APPROVAL') so
  two concurrent decisions cannot both succeed.
- The audit_events table records only accepted changes; denied attempts go to logs.
- The tickets outbox row is inserted in the same transaction as the approval so
  that the intent to create a ticket is never lost.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app.db import get_conn
from app.integrations import attempt_ticket
from app.models import (
    ALLOWED_CATEGORIES,
    CreateRequestBody,
    DecisionType,
    RequestRecord,
    RequestStatus,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_hash(body: CreateRequestBody) -> str:
    payload = json.dumps(
        {
            "amount":      body.amount,
            "currency":    body.currency,
            "category":    body.category,
            "description": body.description,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _row_to_record(row: sqlite3.Row) -> RequestRecord:
    return RequestRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        requester_id=row["requester_id"],
        approver_id=row["approver_id"],
        amount=row["amount"],
        currency=row["currency"],
        category=row["category"],
        description=row["description"],
        status=row["status"],
        requires_manager_approval=bool(row["requires_manager_approval"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


def _active_policy_rules(tenant_id: str, family: str = "equipment") -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT rules FROM policies
            WHERE tenant_id=? AND policy_family=? AND active=1
            ORDER BY version DESC, effective_date DESC LIMIT 1
            """,
            (tenant_id, family),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["rules"]) if row else None


def _resolve_approver(requester_id: str, tenant_id: str) -> str:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT manager_id FROM directory WHERE user_id=? AND tenant_id=?",
            (requester_id, tenant_id),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["manager_id"]:
        raise HTTPException(status_code=422, detail="No approver found for this user")
    return row["manager_id"]


# ── Create ─────────────────────────────────────────────────────────────────────

def create_request(
    body:             CreateRequestBody,
    requester_id:     str,
    tenant_id:        str,
    idempotency_key:  str,
    trace_id:         str,
) -> RequestRecord:
    if body.category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {sorted(ALLOWED_CATEGORIES)}",
        )

    rules = _active_policy_rules(tenant_id)
    if rules:
        cap = rules.get("cap_inr")
        if cap is not None and body.amount > cap:
            raise HTTPException(
                status_code=422,
                detail=f"amount INR {body.amount} exceeds policy cap of INR {cap}",
            )

    threshold = (rules or {}).get("manager_approval_threshold_inr", 0)
    requires_manager_approval = body.amount > threshold

    approver_id = _resolve_approver(requester_id, tenant_id)
    if approver_id == requester_id:
        raise HTTPException(status_code=422, detail="requester cannot be their own approver")

    phash      = _payload_hash(body)
    request_id = str(uuid.uuid4())
    now        = _now()

    conn = get_conn()
    try:
        # Check for existing key first (avoid inserting then rolling back on conflict)
        existing = conn.execute(
            """
            SELECT * FROM requests
            WHERE tenant_id=? AND requester_id=? AND idempotency_key=?
            """,
            (tenant_id, requester_id, idempotency_key),
        ).fetchone()

        if existing:
            if existing["payload_hash"] != phash:
                raise HTTPException(
                    status_code=409,
                    detail="idempotency key reused with different payload",
                )
            return _row_to_record(existing)

        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO requests
                      (id, tenant_id, requester_id, approver_id, amount, currency,
                       category, description, status, requires_manager_approval,
                       idempotency_key, payload_hash, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        request_id, tenant_id, requester_id, approver_id,
                        body.amount, body.currency, body.category, body.description,
                        RequestStatus.PENDING_APPROVAL, int(requires_manager_approval),
                        idempotency_key, phash, now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO audit_events
                      (request_id, actor_id, action, from_status, to_status, ts, trace_id)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (request_id, requester_id, "CREATE", None,
                     RequestStatus.PENDING_APPROVAL, now, trace_id),
                )
        except sqlite3.IntegrityError:
            # Concurrent insert with same idempotency key — fetch winner
            row = conn.execute(
                "SELECT * FROM requests WHERE tenant_id=? AND requester_id=? AND idempotency_key=?",
                (tenant_id, requester_id, idempotency_key),
            ).fetchone()
            if row["payload_hash"] != phash:
                raise HTTPException(
                    status_code=409,
                    detail="idempotency key reused with different payload",
                )
            return _row_to_record(row)

        row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        return _row_to_record(row)
    finally:
        conn.close()


# ── Visibility ─────────────────────────────────────────────────────────────────

def _visible_request(request_id: str, actor_id: str, actor_tenant: str):
    """
    Return (row, is_requester, is_approver).
    Returns 404 for anyone with no relationship to the request (hides existence).
    """
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row or row["tenant_id"] != actor_tenant:
            raise HTTPException(status_code=404, detail="request not found")

        # Fetch actor roles to allow hr_admin visibility
        user_row = conn.execute(
            "SELECT roles FROM directory WHERE user_id=?", (actor_id,)
        ).fetchone()
        roles = json.loads(user_row["roles"]) if user_row else []
    finally:
        conn.close()

    is_requester = row["requester_id"] == actor_id
    is_approver  = row["approver_id"]  == actor_id
    is_hr_admin  = "hr_admin" in roles

    if not (is_requester or is_approver or is_hr_admin):
        raise HTTPException(status_code=404, detail="request not found")

    return row, is_requester, is_approver


# ── Decide ─────────────────────────────────────────────────────────────────────

def decide_request(
    request_id:      str,
    decision:        str,
    comment:         Optional[str],
    actor_id:        str,
    actor_tenant_id: str,
    trace_id:        str,
) -> dict:
    row, is_requester, is_approver = _visible_request(request_id, actor_id, actor_tenant_id)

    # Only the designated approver may decide (architecture §5.9)
    if not is_approver:
        # Requester knows the request exists → 403; unrelated already got 404 above
        raise HTTPException(status_code=403, detail="only the designated approver may decide")

    new_status = RequestStatus.APPROVED if decision == DecisionType.APPROVE else RequestStatus.REJECTED
    current    = row["status"]
    now        = _now()

    # Replay safety: same decision on a finalized request → return existing state unchanged
    if current != RequestStatus.PENDING_APPROVAL:
        if current == new_status:
            return {"request": _row_to_record(row), "ticket_state": None}
        raise HTTPException(status_code=409, detail=f"request is already {current}")

    conn = get_conn()
    try:
        with conn:
            # Conditional UPDATE — zero rows changed means a concurrent decision won
            cur = conn.execute(
                """
                UPDATE requests SET status=?, decided_at=?
                WHERE id=? AND status='PENDING_APPROVAL'
                """,
                (new_status, now, request_id),
            )
            if cur.rowcount == 0:
                updated = conn.execute("SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
                raise HTTPException(status_code=409, detail=f"request is already {updated['status']}")

            conn.execute(
                """
                INSERT INTO audit_events
                  (request_id, actor_id, action, from_status, to_status, ts, trace_id)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    request_id, actor_id, f"DECISION_{decision}",
                    RequestStatus.PENDING_APPROVAL, new_status, now, trace_id,
                ),
            )

            if new_status == RequestStatus.APPROVED:
                # Outbox row in the same transaction — intent is never lost
                conn.execute(
                    "INSERT OR IGNORE INTO tickets (request_id, external_ref, state, attempts) VALUES (?,?,'PENDING',0)",
                    (request_id, request_id),
                )

        updated_row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()

        ticket_state = None
        if new_status == RequestStatus.APPROVED:
            ticket_state = attempt_ticket(request_id)

        return {"request": _row_to_record(updated_row), "ticket_state": ticket_state}
    finally:
        conn.close()

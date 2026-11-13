"""
HR ticket adapter: outbox pattern + read-back reconciliation.

After an approval is committed, a PENDING ticket row already exists (written in
the same transaction as the state change). This module attempts ticket creation
and updates the row. On timeout the row becomes UNKNOWN; the reconciler performs
a read-back to discover what actually happened, then retries within a bounded
attempt limit without duplicating tickets.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.db import get_conn

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


class HRAdapter:
    """
    Mock HR ticket service.
    Set timeout_mode=True in tests to simulate a successful-but-timed-out call.
    """
    timeout_mode: bool = False

    @classmethod
    def set_timeout_mode(cls, enabled: bool) -> None:
        cls.timeout_mode = enabled

    @classmethod
    def create_ticket(cls, external_ref: str, payload: dict) -> dict:
        external_id = f"HR-{external_ref[:8].upper()}"
        if cls.timeout_mode:
            # Ticket was created server-side, but the response was lost.
            raise TimeoutError(f"HR adapter timed out (ticket {external_id} may exist)")
        return {"external_id": external_id}

    @classmethod
    def get_ticket_by_external_ref(cls, external_ref: str) -> Optional[dict]:
        """Read-back: return ticket if it exists, else None."""
        # Mock always finds the ticket (it was created before the timeout).
        external_id = f"HR-{external_ref[:8].upper()}"
        return {"external_id": external_id}


def attempt_ticket(request_id: str) -> str:
    """
    Try to create the HR ticket for an approved request.
    Uses the tickets outbox row (already inserted in the approval transaction).
    Returns the resulting ticket state.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tickets WHERE request_id = ?", (request_id,)
        ).fetchone()

        if not row:
            return "NOT_FOUND"
        if row["state"] in ("CREATED", "FAILED"):
            return row["state"]

        attempts = row["attempts"]
        if attempts >= MAX_ATTEMPTS:
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='FAILED', last_error=? WHERE request_id=?",
                    ("max attempts exceeded", request_id),
                )
            logger.error("ticket FAILED after max attempts request_id=%s", request_id)
            return "FAILED"

        try:
            result = HRAdapter.create_ticket(request_id, {})
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='CREATED', external_id=?, attempts=attempts+1 WHERE request_id=?",
                    (result["external_id"], request_id),
                )
            return "CREATED"

        except TimeoutError as exc:
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='UNKNOWN', attempts=attempts+1, last_error=? WHERE request_id=?",
                    (str(exc), request_id),
                )
            logger.warning("ticket UNKNOWN request_id=%s: %s", request_id, exc)
            return "UNKNOWN"

        except Exception as exc:
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='UNKNOWN', attempts=attempts+1, last_error=? WHERE request_id=?",
                    (str(exc), request_id),
                )
            return "UNKNOWN"

    finally:
        conn.close()


def reconcile_ticket(request_id: str) -> str:
    """
    Reconciler: for UNKNOWN/PENDING tickets, ask HR whether the ticket exists,
    then either record it or retry creation with the same external_ref.
    The external_ref equals the request_id, making repeated creates idempotent
    on the HR side.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tickets WHERE request_id = ?", (request_id,)
        ).fetchone()

        if not row:
            return "NOT_FOUND"
        if row["state"] in ("CREATED", "FAILED"):
            return row["state"]

        attempts = row["attempts"]
        if attempts >= MAX_ATTEMPTS:
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='FAILED', last_error=? WHERE request_id=?",
                    ("max attempts exceeded", request_id),
                )
            logger.error("reconciler: ticket FAILED request_id=%s", request_id)
            return "FAILED"

        # Read-back: does the ticket already exist in HR?
        existing = HRAdapter.get_ticket_by_external_ref(request_id)
        if existing:
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='CREATED', external_id=?, attempts=attempts+1 WHERE request_id=?",
                    (existing["external_id"], request_id),
                )
            logger.info("reconciler: ticket CREATED via read-back request_id=%s", request_id)
            return "CREATED"

        # Not found — retry creation with same external_ref (idempotent on HR side)
        try:
            result = HRAdapter.create_ticket(request_id, {})
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='CREATED', external_id=?, attempts=attempts+1 WHERE request_id=?",
                    (result["external_id"], request_id),
                )
            return "CREATED"
        except TimeoutError as exc:
            with conn:
                conn.execute(
                    "UPDATE tickets SET state='UNKNOWN', attempts=attempts+1, last_error=? WHERE request_id=?",
                    (str(exc), request_id),
                )
            return "UNKNOWN"

    finally:
        conn.close()

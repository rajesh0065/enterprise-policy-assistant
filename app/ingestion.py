"""
Policy ingestion: loads fixture documents into the policies and directory tables.

Fixture text values match the assessment exactly.
Enriched fields (policy_family, effective_date, rules) support version-precedence
logic and deterministic cap enforcement without LLM interpretation.
"""
from __future__ import annotations

import json
from app.db import get_conn

# Assessment-required fixtures, text values preserved verbatim.
POLICIES: list[dict] = [
    {
        "document_id":   "A-EQP-2026",
        "tenant_id":     "A",
        "policy_family": "equipment",
        "version":       2,
        "effective_date": "2026-01-01",
        "active":        True,
        "access_scope":  "employees",
        "title":         "Equipment Reimbursement Policy 2026",
        "text": (
            "Equipment reimbursement is capped at INR 10,000 per year."
            " Requests above INR 5,000 require manager approval."
        ),
        "rules": {"cap_inr": 10000, "manager_approval_threshold_inr": 5000},
    },
    {
        "document_id":   "A-EQP-2025",
        "tenant_id":     "A",
        "policy_family": "equipment",
        "version":       1,
        "effective_date": "2025-01-01",
        "active":        False,
        "access_scope":  "employees",
        "title":         "Equipment Reimbursement Policy 2025",
        "text": "Equipment reimbursement is capped at INR 7,000 per year.",
        "rules": {"cap_inr": 7000, "manager_approval_threshold_inr": 5000},
    },
    {
        "document_id":   "B-EQP-2026",
        "tenant_id":     "B",
        "policy_family": "equipment",
        "version":       1,
        "effective_date": "2026-01-01",
        "active":        True,
        "access_scope":  "employees",
        "title":         "Equipment Reimbursement Policy 2026",
        "text": "Equipment reimbursement is capped at INR 15,000 per year.",
        "rules": {"cap_inr": 15000, "manager_approval_threshold_inr": 5000},
    },
]

# Mirrors auth._DIRECTORY; both must stay in sync.
_DIRECTORY_ROWS: list[dict] = [
    {"user_id": "emp_a1",   "tenant_id": "A", "roles": ["employee"],               "manager_id": "mgr_a1"},
    {"user_id": "mgr_a1",   "tenant_id": "A", "roles": ["employee", "approver"],   "manager_id": None},
    {"user_id": "emp_a2",   "tenant_id": "A", "roles": ["employee"],               "manager_id": "mgr_a1"},
    {"user_id": "emp_b1",   "tenant_id": "B", "roles": ["employee"],               "manager_id": "mgr_b1"},
    {"user_id": "mgr_b1",   "tenant_id": "B", "roles": ["employee", "approver"],   "manager_id": None},
    {"user_id": "hr_admin", "tenant_id": "A", "roles": ["employee", "hr_admin"],   "manager_id": None},
]


def ingest_all() -> None:
    conn = get_conn()
    try:
        with conn:
            for p in POLICIES:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO policies
                      (document_id, tenant_id, policy_family, version, effective_date,
                       active, access_scope, title, text, rules)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        p["document_id"], p["tenant_id"], p["policy_family"],
                        p["version"], p["effective_date"], int(p["active"]),
                        p["access_scope"], p["title"], p["text"],
                        json.dumps(p["rules"]),
                    ),
                )
            for u in _DIRECTORY_ROWS:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO directory (user_id, tenant_id, roles, manager_id)
                    VALUES (?,?,?,?)
                    """,
                    (u["user_id"], u["tenant_id"], json.dumps(u["roles"]), u["manager_id"]),
                )
    finally:
        conn.close()

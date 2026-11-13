from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DecisionType(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


# ── Assistant query ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class CitedDocument(BaseModel):
    document_id: str
    title: str
    version: int


class SuggestedDraft(BaseModel):
    amount: float
    currency: str = "INR"
    category: str
    description: str


class QueryResponse(BaseModel):
    answer: str
    citations: List[CitedDocument]
    evidence_status: str  # "sufficient" | "insufficient_evidence"
    suggested_request_draft: Optional[SuggestedDraft] = None


# ── Expense request ────────────────────────────────────────────────────────────

ALLOWED_CATEGORIES = frozenset({
    "equipment", "travel", "training", "office_supplies", "other"
})


class CreateRequestBody(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = Field(default="INR")
    category: str
    description: str = Field(..., min_length=1, max_length=1000)


class RequestRecord(BaseModel):
    id: str
    tenant_id: str
    requester_id: str
    approver_id: str
    amount: float
    currency: str
    category: str
    description: str
    status: str
    requires_manager_approval: bool
    idempotency_key: str
    created_at: str
    decided_at: Optional[str] = None


# ── Decision ───────────────────────────────────────────────────────────────────

class DecisionBody(BaseModel):
    decision: DecisionType
    comment: Optional[str] = None


class DecisionResponse(BaseModel):
    request: RequestRecord
    ticket_state: Optional[str] = None

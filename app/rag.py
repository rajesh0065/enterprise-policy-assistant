"""
RAG pipeline: retrieve → build context → LLM → validate citations → respond.

Evidence status is determined by a deterministic relevance threshold, not by
asking the model to self-report uncertainty. The LLM is never called when no
candidate exceeds the threshold.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from app import llm, retrieval
from app.db import get_conn
from app.models import CitedDocument, QueryResponse, SuggestedDraft
from app.retrieval import RELEVANCE_THRESHOLD

CONTEXT_BUDGET_CHARS = 6000  # rough proxy for ~1 500 tokens


def _fetch_doc(document_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM policies WHERE document_id = ?", (document_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["rules"] = json.loads(d["rules"])
    return d


def _build_context(
    candidates: List[Tuple[str, float]]
) -> Tuple[List[dict], List[str]]:
    """
    Fill the context budget in rank order.
    Each document is wrapped as quoted evidence so the LLM cannot mistake it
    for instructions (prompt-injection mitigation, architecture §5.5).
    """
    context_docs: list[dict] = []
    in_context:   list[str]  = []
    budget = CONTEXT_BUDGET_CHARS

    for doc_id, _ in candidates:
        doc = _fetch_doc(doc_id)
        if not doc:
            continue
        snippet = (
            f'[EVIDENCE doc_id="{doc_id}" version="{doc["version"]}"]\n'
            f'{doc["text"]}\n'
            f'[END EVIDENCE]'
        )
        if len(snippet) > budget:
            break
        context_docs.append(doc)
        in_context.append(doc_id)
        budget -= len(snippet)

    return context_docs, in_context


def _extract_draft(
    question: str, context_docs: List[dict]
) -> Optional[SuggestedDraft]:
    """
    If the user asked to submit/claim, offer a draft payload.
    The draft is a suggestion only — no request is created here.
    """
    if not context_docs:
        return None
    q = question.lower()
    if not any(w in q for w in ("submit", "request", "claim", "apply", "help me")):
        return None

    # Extract amount from question text
    amount: Optional[float] = None
    for pattern in (r"inr\s*([\d,]+)", r"₹\s*([\d,]+)", r"([\d,]+)\s*inr"):
        m = re.search(pattern, q)
        if m:
            try:
                amount = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    if amount is None:
        return None

    return SuggestedDraft(
        amount=amount,
        currency="INR",
        category="equipment",
        description=(
            "Equipment reimbursement request "
            "(draft — confirm and POST /requests to create)"
        ),
    )


def _insufficient(msg: str = "") -> QueryResponse:
    return QueryResponse(
        answer=(
            "Insufficient evidence: "
            + (msg or "no relevant policy documents found for this question.")
        ),
        citations=[],
        evidence_status="insufficient_evidence",
    )


def answer_query(question: str, tenant_id: str, roles: List[str]) -> QueryResponse:
    candidates = retrieval.retrieve(question, tenant_id, roles)

    passing = [(d, s) for d, s in candidates if s >= RELEVANCE_THRESHOLD]
    if not passing:
        return _insufficient()

    context_docs, in_context = _build_context(passing)
    if not context_docs:
        return _insufficient()

    llm_resp = llm.generate(question, context_docs)

    # Citation validator: drop any ID not in the context list (architecture §5.7)
    valid_cited = [cid for cid in llm_resp.cited_document_ids if cid in in_context]
    if not valid_cited:
        return _insufficient("response could not be grounded in retrieved documents.")

    citations = [
        CitedDocument(
            document_id=doc["document_id"],
            title=doc["title"],
            version=doc["version"],
        )
        for doc in context_docs
        if doc["document_id"] in valid_cited
    ]

    return QueryResponse(
        answer=llm_resp.answer,
        citations=citations,
        evidence_status="sufficient",
        suggested_request_draft=_extract_draft(question, context_docs),
    )

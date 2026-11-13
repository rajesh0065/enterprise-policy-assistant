"""
Hybrid retrieval: keyword search + mocked vector search → RRF fusion →
deterministic tenant/access/version filter.

In production: replace keyword_search and vector_search with Azure AI Search
hybrid query calls, keeping the Python filter as a mandatory second layer.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from app.db import get_conn

# Minimum fused RRF score for a document to pass into the context builder.
# A single-channel hit at rank-0 scores 1/61 ≈ 0.0164; two-channel scores ≈ 0.033.
# Setting 0.025 means purely-vector hits (from bigram mock) don't pass alone —
# a keyword match is required, ensuring unrelated questions return insufficient_evidence.
RELEVANCE_THRESHOLD = 0.025
_RRF_K = 60

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "for", "of", "to", "in",
    "on", "at", "by", "as", "or", "and", "but", "not", "with", "from",
    "that", "this", "it", "its", "i", "my", "we", "our", "you", "your",
    "he", "she", "they", "them", "his", "her", "their", "what", "which",
    "who", "how", "when", "where", "why", "per", "s",
})


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"\w+", text.lower()))
    return tokens - _STOP_WORDS


def keyword_search(query: str, tenant_id: str, k: int = 10) -> List[Tuple[str, float]]:
    """Token-overlap keyword search scoped to active docs in tenant."""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT document_id, text FROM policies WHERE tenant_id = ? AND active = 1",
            (tenant_id,),
        ).fetchall()
    finally:
        conn.close()

    scores: list[tuple[str, float]] = []
    for row in rows:
        overlap = len(q_tokens & _tokenize(row["text"]))
        if overlap:
            scores.append((row["document_id"], overlap / (len(q_tokens) + 1)))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:k]


def vector_search(query: str, tenant_id: str, k: int = 10) -> List[Tuple[str, float]]:
    """Mocked vector search using character bigram similarity (stands in for embeddings)."""
    q = query.lower()
    q_bigrams = set(q[i : i + 2] for i in range(len(q) - 1))
    if not q_bigrams:
        return []

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT document_id, text, title FROM policies WHERE tenant_id = ? AND active = 1",
            (tenant_id,),
        ).fetchall()
    finally:
        conn.close()

    scores: list[tuple[str, float]] = []
    for row in rows:
        combined = (row["text"] + " " + row["title"]).lower()
        d_bigrams = set(combined[i : i + 2] for i in range(len(combined) - 1))
        union = len(q_bigrams | d_bigrams)
        if union:
            scores.append((row["document_id"], len(q_bigrams & d_bigrams) / union))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:k]


def rrf_fusion(
    keyword_results: List[Tuple[str, float]],
    vector_results:  List[Tuple[str, float]],
) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion — merges ranked lists by position, not raw score."""
    fused: dict[str, float] = {}
    for rank, (doc_id, _) in enumerate(keyword_results):
        fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
    for rank, (doc_id, _) in enumerate(vector_results):
        fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


def access_and_version_filter(
    candidates: List[Tuple[str, float]],
    tenant_id:  str,
    roles:      List[str],
) -> List[Tuple[str, float]]:
    """
    Deterministic second-layer filter that runs even when the index already filtered.
    Enforces: correct tenant, access scope, active=True, and highest version per family.
    An index misconfiguration cannot leak cross-tenant data because this layer always runs.
    """
    if not candidates:
        return []

    doc_ids = [c[0] for c in candidates]
    placeholders = ",".join("?" * len(doc_ids))

    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT document_id, tenant_id, policy_family, version, effective_date,
                   active, access_scope
            FROM policies WHERE document_id IN ({placeholders})
            """,
            doc_ids,
        ).fetchall()
    finally:
        conn.close()

    docs = {r["document_id"]: dict(r) for r in rows}
    score_map = dict(candidates)

    # Apply per-document rules
    allowed: list[tuple[str, float, dict]] = []
    for doc_id, score in candidates:
        doc = docs.get(doc_id)
        if not doc:
            continue
        if doc["tenant_id"] != tenant_id:
            continue
        if not doc["active"]:
            continue
        scope = doc["access_scope"]
        if scope == "employees" and "employee" not in roles:
            continue
        if scope == "managers" and "approver" not in roles and "hr_admin" not in roles:
            continue
        allowed.append((doc_id, score, doc))

    # Keep only the highest active version per policy_family
    best: dict[str, tuple[str, float, dict]] = {}
    for doc_id, score, doc in allowed:
        family = doc["policy_family"]
        if family not in best:
            best[family] = (doc_id, score, doc)
        else:
            existing = best[family][2]
            if doc["version"] > existing["version"] or (
                doc["version"] == existing["version"]
                and doc["effective_date"] > existing["effective_date"]
            ):
                best[family] = (doc_id, score, doc)

    result = [(doc_id, score_map[doc_id]) for doc_id, _, _ in best.values()]
    result.sort(key=lambda x: x[1], reverse=True)
    return result


def retrieve(
    query:     str,
    tenant_id: str,
    roles:     List[str],
    top_n:     int = 5,
) -> List[Tuple[str, float]]:
    """Full pipeline: keyword + vector → RRF → access/version filter → top N."""
    kw  = keyword_search(query, tenant_id)
    vec = vector_search(query, tenant_id)
    fused    = rrf_fusion(kw, vec)
    filtered = access_and_version_filter(fused, tenant_id, roles)
    return filtered[:top_n]

"""
LLM adapter: replaceable interface + mock implementation.

In production: swap MockLLMAdapter for AzureOpenAIAdapter (Azure OpenAI via
AI Foundry) without changing any caller. The adapter has no tools and no network
access beyond the model endpoint, so retrieved text cannot trigger actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    answer:              str
    cited_document_ids:  List[str] = field(default_factory=list)


@runtime_checkable
class LLMAdapter(Protocol):
    def generate(self, question: str, context_docs: List[dict]) -> LLMResponse: ...


class MockLLMAdapter:
    """
    Deterministic mock that generates answers from pre-fetched context documents.
    The 'question' text carries no authority; all facts come from context_docs.
    Retrieved text is treated as quoted data — the mock never executes instructions
    embedded in policy text.
    """

    def generate(self, question: str, context_docs: List[dict]) -> LLMResponse:
        if not context_docs:
            return LLMResponse(
                answer="Insufficient evidence: no relevant policy documents were retrieved.",
                cited_document_ids=[],
            )

        cited_ids = [doc["document_id"] for doc in context_docs]
        q_lower   = question.lower()

        is_equipment = any(
            w in q_lower
            for w in ("claim", "reimburse", "equipment", "headset", "laptop", "device")
        )

        if is_equipment:
            doc   = context_docs[0]
            rules = doc.get("rules") or {}
            cap   = rules.get("cap_inr")
            thresh = rules.get("manager_approval_threshold_inr")

            lines = [
                f"Based on policy {doc['document_id']} — {doc['title']}:",
                "",
                doc["text"],
            ]
            if cap:
                lines.append(f"\nThe annual reimbursement cap is INR {cap:,.0f}.")
            if thresh:
                lines.append(
                    f"Requests above INR {thresh:,.0f} require manager approval."
                )
            lines.append(
                "\nNote: Remaining annual allowance is not shown because claim "
                "history is not available in this query."
            )

            return LLMResponse(answer="\n".join(lines), cited_document_ids=cited_ids)

        # Generic answer
        snippets = "\n\n".join(
            f'[{d["document_id"]}]: {d["text"]}' for d in context_docs
        )
        return LLMResponse(
            answer=f"Based on the following policies:\n\n{snippets}",
            cited_document_ids=cited_ids,
        )


# Module-level adapter — replace by calling set_adapter() before startup.
_adapter: LLMAdapter = MockLLMAdapter()


def set_adapter(adapter: LLMAdapter) -> None:
    global _adapter
    _adapter = adapter


def generate(question: str, context_docs: List[dict]) -> LLMResponse:
    return _adapter.generate(question, context_docs)

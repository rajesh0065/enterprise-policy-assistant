# Enterprise Policy Assistant & Request Automation

**Assessment submission — Rajesh Patnam · Hands-on AI / Python Engineering Lead**

---

## What this is

A small but working enterprise assistant that:
- Answers employee policy questions with retrieved, cited evidence (RAG).
- Helps create an approval-controlled expense request.

Three endpoints, two paths, deterministic control plane.

---

## Quick start

### Prerequisites
- Python 3.11+

### Setup

```bash
cd enterprise-policy-assistant
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Run the API

```bash
uvicorn app.main:app --reload
```

API is available at `http://127.0.0.1:8000`.  
Interactive docs: `http://127.0.0.1:8000/docs`

---

## Run the tests

```bash
pytest -v
```

All 7 mandatory acceptance tests plus control cases.

---

## Demo — curl examples

All requests include `X-Demo-User` (the demo token resolved server-side to `tenant_id` and `roles`).

### 1. Query the assistant

```bash
curl -s -X POST http://127.0.0.1:8000/assistant/query \
  -H "Content-Type: application/json" \
  -H "X-Demo-User: emp_a1" \
  -d '{"question": "I work in organization A. Can I claim INR 8000 for a headset? Which policy applies? Please help me submit a request."}' | python -m json.tool
```

Expected: cites `A-EQP-2026` only, states INR 8,000 is within cap but needs manager approval, includes a `suggested_request_draft`.

### 2. Create a request (confirmed by user)

```bash
curl -s -X POST http://127.0.0.1:8000/requests \
  -H "Content-Type: application/json" \
  -H "X-Demo-User: emp_a1" \
  -H "Idempotency-Key: req-headset-001" \
  -d '{"amount": 8000, "currency": "INR", "category": "equipment", "description": "Headset for WFH setup"}' | python -m json.tool
```

Capture the `id` from the response for the next step.

### 3. Approve the request (as designated approver)

```bash
curl -s -X POST http://127.0.0.1:8000/requests/<REQUEST_ID>/decision \
  -H "Content-Type: application/json" \
  -H "X-Demo-User: mgr_a1" \
  -d '{"decision": "APPROVE", "comment": "Approved"}' | python -m json.tool
```

---

## Demo users

| User | Tenant | Roles | Manager |
|---|---|---|---|
| `emp_a1` | A | employee | `mgr_a1` |
| `mgr_a1` | A | employee, approver | — |
| `emp_a2` | A | employee | `mgr_a1` |
| `emp_b1` | B | employee | `mgr_b1` |
| `mgr_b1` | B | employee, approver | — |
| `hr_admin` | A | employee, hr_admin | — |

---

## Policy fixtures

| Document ID | Tenant | Version | Active | Cap |
|---|---|---|---|---|
| `A-EQP-2026` | A | 2 | ✓ | INR 10,000 (manager approval above 5,000) |
| `A-EQP-2025` | A | 1 | ✗ | INR 7,000 (superseded) |
| `B-EQP-2026` | B | 1 | ✓ | INR 15,000 |

---

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full Mermaid diagram, component table, and Azure vs Python decision map.

---

## Part D — Engineering review

### Deterministic Python vs LLM-driven

**Deterministic Python:** identity, tenant and role checks, access and version filters, cap and threshold enforcement, idempotency, state machine transitions, audit recording, ticket creation and reconciliation. These decisions must be repeatable, auditable and testable — model output is none of those.

**LLM-driven:** turning already-filtered, already-validated evidence into a readable natural-language answer, and drafting a request payload the user must explicitly confirm. The model never decides who someone is, what they may see, or whether anything is approved.

### Why use an agent at all?

An agent is justified for multi-step assistance — retrieve several policies, compare them, draft a request, explain the difference — using **read-only** tools whose calls are bound to the caller's Principal. For the linear flows in this assessment (single retrieve → answer, single validate → decide) an agent adds moving parts without benefit.

### Tenant filters and tool permissions

**Azure AI Search:** Store `tenant_id` and `access_scope` as filterable fields. Add a mandatory OData filter built server-side from the Principal: `tenant_id eq '<id>' and active eq true`. Keep the Python re-filter as a second layer — an index misconfiguration cannot leak tenant data because Python always re-checks.

For stronger isolation: separate index per tenant, or security-trimming groups per document.

**Azure AI Foundry / LangGraph:** Expose only read-only tools (`search_policy`, `get_policy_rules`). Bind each tool call to the Principal passed by the service, not supplied by the model. Write actions are plain API calls the client makes after explicit confirmation. Use role-based allowlists per tool and log every invocation.

### Path to production

| Concern | Approach |
|---|---|
| Authentication | Entra ID with OAuth 2.0; validate JWT signature, audience, issuer, tenant and role claims; service identity for Power Automate flows |
| Monitoring | OpenTelemetry traces and metrics; structured logs with `trace_id`, `tenant_id`, `user_id`; alerts on cross-tenant filter drops, ticket UNKNOWN > threshold, insufficient-evidence rate spikes |
| RAG evaluation | Golden question set per tenant with expected citations; measure groundedness, citation accuracy, insufficient-evidence recall, and a tenant-leak canary suite as a release gate |
| CI/CD | Lint + type-check + unit tests on every PR; evaluation job; container build + scan; staged deploy with canary and rollback; infrastructure as code |

---

## Trade-offs, incomplete items and assumptions

- **Cumulative annual cap:** not enforced — claim history is not stored. Requests are checked against the per-request cap only. Stated clearly in the assistant's answer.
- **Approver resolution:** mocked as `manager_id` from the directory fixture. Production would query the HR or identity directory.
- **Approval required for all amounts:** every request starts as `PENDING_APPROVAL`. The INR 5,000 threshold sets `requires_manager_approval` only; it does not bypass the approval requirement.
- **Embeddings and vector rankings:** mocked with bigram similarity. Retrieval quality is validated only against the three given fixtures.
- **Ticket worker:** runs inline on the request thread. Production would use a queue-backed background worker.
- **Not included:** UI, real Azure calls, full RBAC administration, rate limiting, real embeddings with an evaluation set, contract tests for adapters.

# Enterprise Policy Assistant & Request Automation — Complete Project Guide

**Author:** Rajesh Patnam · Hands-on AI / Python Engineering Lead Assessment  
**Version:** 1.0 · November 2026

---

## Table of Contents

1. [What We Built — The Big Picture](#1-what-we-built--the-big-picture)
2. [How the System Works — End to End](#2-how-the-system-works--end-to-end)
3. [Project File Structure](#3-project-file-structure)
4. [Each File Explained](#4-each-file-explained)
5. [Demo Users & Policies](#5-demo-users--policies)
6. [How to Run the Project](#6-how-to-run-the-project)
7. [Demo Test Cases — Step by Step](#7-demo-test-cases--step-by-step)
8. [Security Design Decisions](#8-security-design-decisions)
9. [What is Mock vs Production](#9-what-is-mock-vs-production)

---

## 1. What We Built — The Big Picture

This is a **working enterprise backend API** that solves two real workplace problems:

### Problem 1 — Policy Questions
Employees waste time reading long policy PDFs or waiting for HR to answer simple questions like:
> "Can I claim INR 8,000 for a headset? Do I need approval?"

**Our solution:** An AI assistant that searches the right policy documents, reads the relevant text, and gives a cited, grounded answer — without hallucinating numbers or leaking another company's data.

### Problem 2 — Expense Requests
Once an employee knows they're eligible, they need to submit a request and get it approved. This process must be:
- Safe from duplicate submissions
- Only approvable by the designated manager
- Audited (every action recorded)
- Connected to the HR ticketing system

**Our solution:** A state-machine-backed request workflow that enforces all these rules in code, never trusting the client to tell us who they are or what they're allowed to do.

### Two Paths, One Service

```
Employee asks a question  →  POST /assistant/query  →  AI reads policy → Answer + citation
Employee submits request   →  POST /requests         →  Validated, stored, awaits approval
Manager approves/rejects   →  POST /requests/{id}/decision  →  State change + HR ticket
```

The service runs as a FastAPI Python application. Everything is working and testable today with SQLite and mocked AI components. Every component is designed to swap to Azure (Azure OpenAI, Azure AI Search, Azure SQL) in production without changing the surrounding code.

---

## 2. How the System Works — End to End

### Path A: Answering a Policy Question

```
1. Employee sends question with their username header (X-Demo-User: emp_a1)
2. Server looks up emp_a1 → tenant=A, roles=[employee]
3. Retrieval runs in two channels simultaneously:
   a. Keyword search  — finds policy docs in tenant A with matching words
   b. Vector search   — finds policy docs by semantic similarity (mocked with bigrams)
4. RRF Fusion         — combines both ranked lists by position (not raw score)
5. Python filter      — enforces: correct tenant, active doc, highest version per family
6. Relevance check    — if best score < 0.025, return "insufficient_evidence" immediately
7. Context builder    — wraps document text in [EVIDENCE] tags (prompt injection guard)
8. LLM adapter        — reads evidence, writes grounded answer, returns citation IDs
9. Citation validator — drops any cited ID not in the actual context (hallucination guard)
10. Response returned  — answer + citations + evidence_status + optional draft request
```

### Path B: Creating an Expense Request

```
1. Employee POSTs request with Idempotency-Key header
2. Server reads identity from header → tenant, user ID (never from body)
3. Category validated against allowed list
4. Amount checked against policy cap (server reads rules from DB, never trusts client)
5. Manager approval threshold checked → flag set if amount > INR 5,000
6. Approver resolved server-side from directory (manager_id of requester)
7. Self-approval check — cannot be your own approver
8. DB INSERT with UNIQUE constraint on (tenant_id, requester_id, idempotency_key)
9. Audit event written in same transaction
10. Request returned with status = PENDING_APPROVAL
```

### Path C: Approving or Rejecting

```
1. Manager POSTs decision with their username header
2. Server verifies: request exists, same tenant, actor is designated approver
3. If request is already finalized:
   - Same decision → 200 no-op (replay safe)
   - Different decision → 409 conflict
4. Conditional UPDATE WHERE status='PENDING_APPROVAL' (prevents race conditions)
5. Audit event written in same transaction
6. If APPROVED: outbox ticket row inserted in same transaction (intent never lost)
7. HR adapter called → ticket created in HR system
8. If timeout → state = UNKNOWN → reconciler reads back before retrying
9. Response returned with final request state + ticket state
```

---

## 3. Project File Structure

```
enterprise-policy-assistant/
│
├── app/                        ← All backend application code
│   ├── main.py                 ← FastAPI app, middleware, 3 endpoints
│   ├── auth.py                 ← Identity: who is the caller?
│   ├── models.py               ← All Pydantic data models
│   ├── db.py                   ← SQLite schema and connection
│   ├── ingestion.py            ← Policy and directory fixtures loaded at startup
│   ├── retrieval.py            ← Keyword search + vector search + RRF fusion + filter
│   ├── llm.py                  ← LLM adapter interface + mock implementation
│   ├── rag.py                  ← Full RAG pipeline (retrieve → context → LLM → validate)
│   ├── workflow.py             ← Request creation + approval state machine
│   └── integrations.py        ← HR ticket adapter + outbox + reconciliation
│
├── tests/                      ← 7 acceptance tests + helpers
│   ├── conftest.py             ← Shared fixtures: fresh DB per test, HTTP client, helpers
│   ├── test_tenant_isolation.py
│   ├── test_policy_version.py
│   ├── test_evidence_handling.py
│   ├── test_idempotency.py
│   ├── test_approval_authorization.py
│   ├── test_repeated_decision.py
│   └── test_external_timeout.py
│
├── docs/
│   ├── architecture.md         ← System diagram + component table + security decisions
│   ├── architecture.png        ← Architecture diagram image (extracted from Word doc)
│   └── project-explainer.md   ← This file
│
├── README.md                   ← Quick start + curl examples + demo users
└── requirements.txt            ← fastapi, uvicorn, pydantic, httpx, pytest
```

---

## 4. Each File Explained

---

### `app/main.py` — The FastAPI Application Entry Point

**What it does:**  
This is the front door of the entire service. It wires together all the other modules and exposes the 3 HTTP endpoints.

**On startup (lifespan):**
- Calls `init_db()` → creates all 5 SQLite tables if they don't exist
- Calls `ingest_all()` → loads policy documents and user directory into the database

**Middleware:**
- Attaches a unique `trace_id` (UUID) to every incoming request
- Sends it back in the `X-Trace-Id` response header
- Every log line includes this trace_id — makes debugging easy

**Three Endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/assistant/query` | POST | Ask a policy question, get a cited AI answer |
| `/requests` | POST | Submit an expense request (needs `Idempotency-Key` header) |
| `/requests/{id}/decision` | POST | Approve or reject a pending request |

**Security note:** Every endpoint calls `Depends(get_principal)` — the server resolves who the caller is from the `X-Demo-User` header. The request body is never trusted for identity.

---

### `app/auth.py` — Identity Resolution

**What it does:**  
Answers the question: "Who is making this request, and what are they allowed to do?"

**The directory fixture** (hardcoded for demo, would be Entra ID in production):

| User ID | Tenant | Roles | Manager |
|---|---|---|---|
| `emp_a1` | A | employee | `mgr_a1` |
| `mgr_a1` | A | employee, approver | none |
| `emp_a2` | A | employee | `mgr_a1` |
| `emp_b1` | B | employee | `mgr_b1` |
| `mgr_b1` | B | employee, approver | none |
| `hr_admin` | A | employee, hr_admin | none |

**Principal dataclass:**
```python
Principal(user_id="emp_a1", tenant_id="A", roles=["employee"], manager_id="mgr_a1")
```

**What happens if you pass an unknown user:** HTTP 401 Unauthorized — immediately, before any business logic runs.

**Key design rule:** `tenant_id` and `roles` are ALWAYS read from this server-side directory. No client can claim a different tenant or escalate their own roles.

---

### `app/models.py` — All Data Shapes

**What it does:**  
Defines every Pydantic model — the shapes of requests and responses. Pydantic validates inputs automatically; malformed JSON is rejected before any code runs.

**Key models:**

| Model | Used for |
|---|---|
| `QueryRequest` | Body of POST /assistant/query — just `question: str` |
| `QueryResponse` | Response from /assistant/query — answer, citations, evidence_status, optional draft |
| `CitedDocument` | One citation: document_id, title, version |
| `SuggestedDraft` | Optional pre-filled request form included in query response |
| `CreateRequestBody` | Body of POST /requests — amount, currency, category, description |
| `RequestRecord` | Full request including status, approver_id, timestamps |
| `DecisionBody` | Body of POST .../decision — APPROVE or REJECT + optional comment |
| `DecisionResponse` | Returned after decision — updated request + ticket state |

**Allowed categories:** `equipment`, `travel`, `training`, `office_supplies`, `other`

**Status lifecycle:** `PENDING_APPROVAL` → `APPROVED` or `REJECTED` (one-way, no going back)

---

### `app/db.py` — Database Schema

**What it does:**  
Creates and manages the SQLite database with 5 tables. Uses WAL (Write-Ahead Logging) mode for better concurrent read performance.

**Five tables:**

**`policies`** — Stores all policy documents  
- `document_id` (primary key), `tenant_id`, `policy_family`, `version`, `active`, `access_scope`, `title`, `text`, `rules` (JSON)

**`directory`** — Stores users and their relationships  
- `user_id` (primary key), `tenant_id`, `roles` (JSON array), `manager_id`

**`requests`** — Every submitted expense request  
- `id` (UUID), `tenant_id`, `requester_id`, `approver_id`, `amount`, `status`
- UNIQUE constraint on `(tenant_id, requester_id, idempotency_key)` — this is the idempotency enforcement

**`audit_events`** — Append-only log (never updated, only inserted)  
- Tracks every CREATE and DECISION action with actor, from_status, to_status, timestamp, trace_id

**`tickets`** — HR ticket outbox  
- Inserted when request is approved, tracks ticket creation state: PENDING → CREATED / UNKNOWN / FAILED

---

### `app/ingestion.py` — Policy Fixtures

**What it does:**  
Loads the 3 policy documents and 6 users into the database at startup. This is the data the AI reads when answering questions.

**Three policies loaded:**

| ID | Tenant | Version | Active | Cap | Manager Approval Required Above |
|---|---|---|---|---|---|
| `A-EQP-2026` | A | 2 | Yes | INR 10,000/year | INR 5,000 |
| `A-EQP-2025` | A | 1 | No | INR 7,000/year | INR 5,000 |
| `B-EQP-2026` | B | 1 | Yes | INR 15,000/year | INR 5,000 |

**A-EQP-2025 is intentionally inactive.** It's the old 2025 policy that was superseded by the 2026 version. The system must never use it. This tests that version and active filters work correctly.

**Rules JSON** (machine-readable, used for cap enforcement):
```json
{"cap_inr": 10000, "manager_approval_threshold_inr": 5000}
```

---

### `app/retrieval.py` — Hybrid Search Pipeline

**What it does:**  
Given a question, finds the most relevant policy documents for that user's tenant. This is the "R" in RAG (Retrieval-Augmented Generation).

**Four stages:**

**1. Keyword Search**  
Tokenizes the question (removes stop words like "the", "is", "a"). Counts how many tokens overlap with each policy document. Returns documents ranked by overlap score. Only searches active documents in the user's tenant.

**2. Vector Search (mocked)**  
In production this would call Azure AI Search with an embedding. Here it uses character bigram similarity (overlapping 2-character pairs) as a proxy. Same idea — returns documents ranked by similarity.

**3. RRF Fusion (Reciprocal Rank Fusion)**  
Merges the two ranked lists into one. Formula: `score = 1/(60 + rank)` for each document in each list. Documents appearing in both lists score higher than those in only one. This is a proven industry technique used by Azure AI Search.

**4. Access & Version Filter (Python second layer)**  
This runs even if the index already filtered. For every candidate document:
- Must be in the correct tenant (re-checked in Python)
- Must be active (active=True)
- Access scope must match roles
- If two documents are from the same policy family, only keep the highest version

**Relevance threshold: 0.025**  
If no document scores above this, the query returns `insufficient_evidence` immediately — no LLM is called. This is deterministic and testable.

---

### `app/llm.py` — LLM Adapter

**What it does:**  
Defines the interface that all LLM implementations must follow, and provides a mock for testing.

**The Protocol (interface):**
```python
class LLMAdapter(Protocol):
    def generate(self, question: str, context_docs: List[dict]) -> LLMResponse:
```

**MockLLMAdapter (what runs in demo):**  
Reads the `rules` JSON from the retrieved policy doc and constructs a factually correct answer. Includes the cap amount, approval threshold, and a note that remaining annual allowance is not shown (because claim history isn't tracked).

**LLMResponse:**
```python
LLMResponse(answer="Based on policy A-EQP-2026...", cited_document_ids=["A-EQP-2026"])
```

**To swap to Azure OpenAI in production:**
```python
from app.llm import set_adapter
set_adapter(AzureOpenAIAdapter(endpoint="...", api_key="..."))
```
No other file changes needed.

---

### `app/rag.py` — RAG Orchestration

**What it does:**  
Orchestrates the full Retrieval-Augmented Generation pipeline. Calls retrieval, builds context, calls LLM, validates citations, returns response.

**Context builder:**  
Takes the top retrieved documents and formats them with prompt-injection guards:
```
[EVIDENCE doc_id="A-EQP-2026" version="2"]
Equipment reimbursement is capped at INR 10,000 per year...
[END EVIDENCE]
```
Budget: 6,000 characters (~1,500 tokens). Documents are added in rank order until budget is full.

**Citation validator:**  
After the LLM responds, checks every cited document ID against the list of what was actually in the context. If the LLM cited something it wasn't given (hallucination), that citation is dropped.

**Draft extraction:**  
If the user's question contains words like "submit", "claim", "request", and includes an INR amount, the response includes a `suggested_request_draft` — a pre-filled form the user can confirm and POST to `/requests`. The draft is a suggestion only. No request is created here.

---

### `app/workflow.py` — Request State Machine

**What it does:**  
The most critical file for business correctness. Enforces all the rules around creating and deciding on expense requests.

**`create_request()` — What it checks:**
1. Category must be in allowed list → 422 if not
2. Amount must not exceed policy cap (read from DB, not client) → 422 if over cap
3. `requires_manager_approval` flag set if amount > threshold
4. Approver resolved from directory (server-side) → 422 if no manager found
5. Self-approval check → 422 if approver = requester
6. Check for existing idempotency key (same key + same payload → return existing record)
7. Same key + different payload → 409 Conflict
8. INSERT request + audit event in one atomic transaction

**`decide_request()` — What it checks:**
1. Request must exist and belong to same tenant → 404 if not
2. Only the designated approver may decide:
   - Requester trying to decide their own request → 403
   - Unrelated user → 404 (hides that the request exists)
3. Replay safety:
   - Already APPROVED, same decision → 200 no-op
   - Already APPROVED, trying to REJECT → 409
4. Conditional UPDATE: `WHERE status='PENDING_APPROVAL'` — if two managers somehow click simultaneously, only one wins
5. Audit event + outbox ticket row in same transaction

---

### `app/integrations.py` — HR Ticket Adapter

**What it does:**  
After a request is approved, an HR ticket must be created. This handles that — including failure recovery.

**The outbox pattern:**  
When a request is approved, a ticket row (state=PENDING) is inserted in the same database transaction as the approval. This means: even if the server crashes immediately after, the ticket intent is recorded and can be retried.

**`attempt_ticket()`:**
- Looks up the outbox row
- Calls `HRAdapter.create_ticket()` 
- If success → state = CREATED
- If timeout → state = UNKNOWN (ticket may have been created server-side)
- Max 5 attempts before state = FAILED

**`reconcile_ticket()`:**  
Called when state is UNKNOWN. Instead of blindly retrying (which might duplicate the ticket), it first asks HR: "Did you already create this ticket?" (read-back via `external_ref`). If yes → mark CREATED. If no → retry creation.

**Testing timeout mode:**  
`HRAdapter.set_timeout_mode(True)` forces a timeout in tests to verify the UNKNOWN → CREATED reconciliation path.

---

### `tests/conftest.py` — Test Infrastructure

**What it does:**  
Sets up shared test helpers so each test file is clean and focused.

**`fresh_db` fixture:**  
Creates a brand-new temp database for every test. Patches `APP_DB_PATH` so the app uses the temp file. Runs `init_db()` and `ingest_all()`. After the test, it's deleted. No test can pollute another.

**`client` fixture:**  
Returns a `TestClient` (httpx) pointed at the FastAPI app. Tests call endpoints directly in-process — no network needed.

**`make_request()` helper:**  
Sends a POST /requests call with standard employee credentials and an idempotency key. Returns the full request record. Used across multiple tests.

**`approve()` helper:**  
Sends a POST .../decision with APPROVE as the manager. Returns the decision response.

---

### Test Files — What Each One Tests

| File | What it proves |
|---|---|
| `test_tenant_isolation.py` | Tenant A employee can never see or cite Tenant B's policy |
| `test_policy_version.py` | Old inactive policy (A-EQP-2025) is never used even if it matches the query |
| `test_evidence_handling.py` | An unrelated question returns `insufficient_evidence` with no LLM call |
| `test_idempotency.py` | Same idempotency key → one record; same key + different amount → 409 |
| `test_approval_authorization.py` | Non-approver gets 404; requester trying to approve own request gets 403 |
| `test_repeated_decision.py` | Approving an already-approved request is a 200 no-op; trying to reject it → 409 |
| `test_external_timeout.py` | Timeout during ticket creation → UNKNOWN → reconciler finds ticket → CREATED (no duplicate) |

---

## 5. Demo Users & Policies

### Demo Users

| Username (send as X-Demo-User header) | Tenant | Roles | Manager |
|---|---|---|---|
| `emp_a1` | A | employee | `mgr_a1` |
| `emp_a2` | A | employee | `mgr_a1` |
| `mgr_a1` | A | employee, approver | none |
| `emp_b1` | B | employee | `mgr_b1` |
| `mgr_b1` | B | employee, approver | none |
| `hr_admin` | A | employee, hr_admin | none |

### Policy Documents

| Document ID | Tenant | Version | Active | Annual Cap | Approval Required Above |
|---|---|---|---|---|---|
| `A-EQP-2026` | A | 2 | ✅ Current | INR 10,000 | INR 5,000 |
| `A-EQP-2025` | A | 1 | ❌ Superseded | INR 7,000 | INR 5,000 |
| `B-EQP-2026` | B | 1 | ✅ Current | INR 15,000 | INR 5,000 |

---

## 6. How to Run the Project

### Prerequisites

- Python 3.11 or newer
- Terminal / PowerShell in the project folder

### Step 1 — Create Virtual Environment

```powershell
cd C:\Users\rajesh.patnam\enterprise-policy-assistant
python -m venv .venv
.venv\Scripts\activate
```

You should see `(.venv)` at the start of your prompt.

### Step 2 — Install Dependencies

```powershell
pip install -r requirements.txt
```

Installs: `fastapi`, `uvicorn`, `pydantic`, `httpx`, `pytest`

### Step 3 — Start the Server

```powershell
uvicorn app.main:app --reload
```

You will see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

The `--reload` flag restarts the server automatically if you edit any file.

### Step 4 — Open the API Explorer

Go to: **http://127.0.0.1:8000/docs**

This is Swagger UI — a visual interface where you can test every endpoint. Click an endpoint → "Try it out" → fill in `x-demo-user` field + request body → "Execute".

### Step 5 — Run the Tests

Open a **second terminal** (keep the server running in the first), activate the venv:

```powershell
.venv\Scripts\activate
pytest -v
```

Expected output: 7 tests, all passing.

---

## 7. Demo Test Cases — Step by Step

> All examples use Windows PowerShell syntax. Replace `<REQUEST_ID>` with the UUID from the previous step's response.

---

### Test Case 1 — Basic Policy Query (Happy Path)

**Scenario:** Employee asks a question about equipment reimbursement.

**What to demonstrate:** AI returns a cited answer. Only the active 2026 policy is cited. Amount is confirmed within cap. Manager approval required flagged. A draft request is offered.

```powershell
# Ask the assistant
Invoke-RestMethod -Uri "http://127.0.0.1:8000/assistant/query" `
  -Method POST `
  -Headers @{ "X-Demo-User" = "emp_a1"; "Content-Type" = "application/json" } `
  -Body '{"question": "Can I claim INR 8000 for a headset? Help me submit a request."}'
```

**Expected response highlights:**
- `evidence_status`: `"sufficient"`
- `citations`: contains `A-EQP-2026` (never `A-EQP-2025`, never `B-EQP-2026`)
- `answer`: mentions INR 10,000 cap and INR 5,000 approval threshold
- `suggested_request_draft`: `{"amount": 8000, "currency": "INR", "category": "equipment", ...}`

---

### Test Case 2 — Insufficient Evidence (Unrelated Question)

**Scenario:** Employee asks something that has no matching policy.

**What to demonstrate:** System returns `insufficient_evidence` without calling the LLM. Deterministic — not a model guess.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/assistant/query" `
  -Method POST `
  -Headers @{ "X-Demo-User" = "emp_a1"; "Content-Type" = "application/json" } `
  -Body '{"question": "What is the company policy on rocket ship maintenance?"}'
```

**Expected:**
- `evidence_status`: `"insufficient_evidence"`
- `citations`: empty list `[]`
- `answer`: starts with "Insufficient evidence: ..."

---

### Test Case 3 — Tenant Isolation

**Scenario:** Tenant B employee asks the same question. Must never see Tenant A policies.

**What to demonstrate:** Complete data isolation. Even if both tenants have an equipment policy, each sees only their own.

```powershell
# As Tenant B employee
Invoke-RestMethod -Uri "http://127.0.0.1:8000/assistant/query" `
  -Method POST `
  -Headers @{ "X-Demo-User" = "emp_b1"; "Content-Type" = "application/json" } `
  -Body '{"question": "What is my equipment reimbursement cap?"}'
```

**Expected:**
- Citation: `B-EQP-2026` only (cap: INR 15,000)
- Never mentions INR 10,000 (Tenant A's cap)
- Never cites `A-EQP-2026` or `A-EQP-2025`

---

### Test Case 4 — Full Approval Workflow (Happy Path)

**Scenario:** Employee submits a request, manager approves it, HR ticket is created.

**Step 1 — Employee creates request:**

```powershell
$response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{
    "X-Demo-User"     = "emp_a1"
    "Content-Type"    = "application/json"
    "Idempotency-Key" = "demo-request-001"
  } `
  -Body '{"amount": 8000, "currency": "INR", "category": "equipment", "description": "Headset for WFH setup"}'

$requestId = $response.id
Write-Host "Request ID: $requestId"
Write-Host "Status: $($response.status)"
Write-Host "Approver: $($response.approver_id)"
```

**Expected:** status=`PENDING_APPROVAL`, approver_id=`mgr_a1`

**Step 2 — Manager approves:**

```powershell
$decision = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$requestId/decision" `
  -Method POST `
  -Headers @{ "X-Demo-User" = "mgr_a1"; "Content-Type" = "application/json" } `
  -Body '{"decision": "APPROVE", "comment": "Approved for WFH setup"}'

Write-Host "Status: $($decision.request.status)"
Write-Host "Ticket State: $($decision.ticket_state)"
```

**Expected:** status=`APPROVED`, ticket_state=`CREATED`

---

### Test Case 5 — Idempotency (Safe Retry)

**Scenario:** Network glitch makes the client retry the same request twice. Must not create a duplicate.

**What to demonstrate:** Same idempotency key returns the same record. No duplicate.

```powershell
# First call
$r1 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="retry-test-001" } `
  -Body '{"amount": 3000, "currency": "INR", "category": "equipment", "description": "Keyboard"}'

# Second call — identical
$r2 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="retry-test-001" } `
  -Body '{"amount": 3000, "currency": "INR", "category": "equipment", "description": "Keyboard"}'

Write-Host "Same ID? $($r1.id -eq $r2.id)"
```

**Expected:** `True` — both calls return the exact same record. Only one row in the database.

---

### Test Case 6 — Idempotency Conflict (Different Payload, Same Key)

**Scenario:** Client sends same key but changes the amount — this is a client error.

```powershell
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
    -Method POST `
    -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="conflict-test-001" } `
    -Body '{"amount": 3000, "currency": "INR", "category": "equipment", "description": "Keyboard"}'

  Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
    -Method POST `
    -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="conflict-test-001" } `
    -Body '{"amount": 5000, "currency": "INR", "category": "equipment", "description": "Keyboard"}'
} catch {
  Write-Host "Status: $($_.Exception.Response.StatusCode)"
}
```

**Expected:** HTTP 409 Conflict on the second call

---

### Test Case 7 — Authorization: Wrong Approver

**Scenario:** A different employee (not the designated manager) tries to approve.

**What to demonstrate:** System returns 404 — it doesn't even reveal the request exists. This prevents enumeration attacks.

```powershell
# First create a request as emp_a1
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="authz-test-001" } `
  -Body '{"amount": 3000, "currency": "INR", "category": "equipment", "description": "Mouse"}'

# Tenant B manager tries to approve (wrong tenant, wrong person)
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
    -Method POST `
    -Headers @{ "X-Demo-User"="mgr_b1"; "Content-Type"="application/json" } `
    -Body '{"decision": "APPROVE"}'
} catch {
  Write-Host "Status: $($_.Exception.Response.StatusCode)"
}
```

**Expected:** HTTP 404 — request not found (from mgr_b1's perspective, it doesn't exist)

---

### Test Case 8 — Authorization: Requester Cannot Approve Own Request

**Scenario:** Employee tries to approve their own request.

```powershell
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="self-approve-test-001" } `
  -Body '{"amount": 3000, "currency": "INR", "category": "equipment", "description": "Mouse"}'

try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
    -Method POST `
    -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json" } `
    -Body '{"decision": "APPROVE"}'
} catch {
  Write-Host "Status: $($_.Exception.Response.StatusCode)"
}
```

**Expected:** HTTP 403 Forbidden — only the designated approver may decide

---

### Test Case 9 — Duplicate Decision (Replay Safety)

**Scenario:** Approval is submitted twice (network retry). Must not create two audit entries or two HR tickets.

```powershell
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a2"; "Content-Type"="application/json"; "Idempotency-Key"="replay-test-001" } `
  -Body '{"amount": 3000, "currency": "INR", "category": "equipment", "description": "Webcam"}'

# First approval
$d1 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
  -Method POST `
  -Headers @{ "X-Demo-User"="mgr_a1"; "Content-Type"="application/json" } `
  -Body '{"decision": "APPROVE"}'

# Second approval (retry)
$d2 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
  -Method POST `
  -Headers @{ "X-Demo-User"="mgr_a1"; "Content-Type"="application/json" } `
  -Body '{"decision": "APPROVE"}'

Write-Host "First status:  $($d1.request.status)"
Write-Host "Second status: $($d2.request.status)"
```

**Expected:** Both return `APPROVED` — no error, no duplicate. The second call is a no-op.

---

### Test Case 10 — Policy Cap Enforcement

**Scenario:** Employee tries to request more than the policy allows.

```powershell
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
    -Method POST `
    -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="cap-test-001" } `
    -Body '{"amount": 15000, "currency": "INR", "category": "equipment", "description": "High-end laptop"}'
} catch {
  $body = $_.ErrorDetails.Message | ConvertFrom-Json
  Write-Host "Error: $($body.detail)"
}
```

**Expected:** HTTP 422 — "amount INR 15000.0 exceeds policy cap of INR 10000"  
*(Tenant A's cap is 10,000; Tenant B's cap is 15,000 — this is for emp_a1 in Tenant A)*

---

### Test Case 11 — Reject a Request

**Scenario:** Manager reviews and decides to reject.

```powershell
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a1"; "Content-Type"="application/json"; "Idempotency-Key"="reject-test-001" } `
  -Body '{"amount": 4000, "currency": "INR", "category": "equipment", "description": "Second monitor"}'

$d = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
  -Method POST `
  -Headers @{ "X-Demo-User"="mgr_a1"; "Content-Type"="application/json" } `
  -Body '{"decision": "REJECT", "comment": "Budget freeze this quarter"}'

Write-Host "Status: $($d.request.status)"
Write-Host "Ticket: $($d.ticket_state)"
```

**Expected:** status=`REJECTED`, ticket_state=`null` — no HR ticket is created for rejected requests

---

### Test Case 12 — Conflict After Decision

**Scenario:** Request is already approved, someone tries to reject it after.

```powershell
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests" `
  -Method POST `
  -Headers @{ "X-Demo-User"="emp_a2"; "Content-Type"="application/json"; "Idempotency-Key"="conflict-decision-001" } `
  -Body '{"amount": 3000, "currency": "INR", "category": "training", "description": "Python course"}'

# Approve it
Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
  -Method POST `
  -Headers @{ "X-Demo-User"="mgr_a1"; "Content-Type"="application/json" } `
  -Body '{"decision": "APPROVE"}' | Out-Null

# Try to reject it now
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/requests/$($r.id)/decision" `
    -Method POST `
    -Headers @{ "X-Demo-User"="mgr_a1"; "Content-Type"="application/json" } `
    -Body '{"decision": "REJECT"}'
} catch {
  Write-Host "Status: $($_.Exception.Response.StatusCode)"
}
```

**Expected:** HTTP 409 Conflict — "request is already APPROVED"

---

## 8. Security Design Decisions

### Why identity never comes from the request body

If the client could say "I am from tenant A" in the POST body, any client could impersonate any tenant. The server resolves identity from the `X-Demo-User` header using its own directory. In production this is a JWT token validated by Entra ID — the server verifies the signature, not just the value.

### Why there are two tenant filters

The keyword and vector search already filter by tenant. But if there's ever an index misconfiguration (wrong filter, caching bug), the Python layer re-checks every candidate document. Cross-tenant data cannot leak because both layers must pass.

### Why inactive policies are still stored

Deleting old policies would lose audit history. A-EQP-2025 is kept in the database with `active=False`. It will never appear in search results (both the query filter and the Python filter exclude inactive docs) but it exists as a record.

### Why the LLM never sees unfiltered text

Policy text is wrapped in `[EVIDENCE]...[END EVIDENCE]` tags before being sent to the LLM. This tells the model clearly: "this is data to read, not instructions to follow." The model has no tools and no internet access, so even a malicious policy document cannot trigger actions.

### Why 404 instead of 403 for wrong-tenant requests

If a user from Tenant B sends a request ID that belongs to Tenant A, they get 404 — not "you don't have access." This prevents an attacker from confirming that a request with a given ID exists in the system. The same applies to unrelated users within the same tenant.

---

## 9. What is Mock vs Production

| Component | In This Demo | In Production |
|---|---|---|
| Authentication | X-Demo-User header → directory lookup | Entra ID JWT, validated signature + claims |
| Keyword search | In-memory token overlap | Azure AI Search keyword index |
| Vector search | Character bigram similarity | Azure AI Search vector index (embeddings) |
| Retrieval filter | Same mandatory tenant filter | Azure AI Search OData filter + Python second layer |
| LLM | MockLLMAdapter reads rules JSON | Azure OpenAI via AI Foundry |
| Database | SQLite (single file) | Azure SQL or PostgreSQL |
| HR integration | In-memory mock, timeout simulation | Real HR system API or Power Automate |
| Monitoring | Structured JSON logs with trace_id | OpenTelemetry → Application Insights |
| Ticket worker | Inline on request thread | Queue-backed background worker |

**Everything around the mocks is production-grade:**
- Idempotency, state machine, audit log — identical behavior in production
- Tenant isolation, version filter, citation validator — deterministic Python, unchanged in production
- Outbox pattern, reconciliation — identical; only the HR adapter endpoint changes

Swapping any mock for the real Azure service is a one-line change in one file. No other code changes required.

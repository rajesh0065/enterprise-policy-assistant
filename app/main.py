"""
FastAPI application — three required endpoints:
  POST /assistant/query
  POST /requests
  POST /requests/{request_id}/decision

Identity for every call comes from the Principal (server-side demo token).
No tenant or user identifiers are accepted from request bodies.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from app import rag, workflow
from app.auth import Principal, get_principal
from app.db import init_db
from app.ingestion import ingest_all
from app.models import (
    CreateRequestBody,
    DecisionBody,
    DecisionResponse,
    QueryRequest,
    QueryResponse,
    RequestRecord,
)

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ingest_all()
    logger.info('"database ready"')
    yield


app = FastAPI(title="Enterprise Policy Assistant", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):
    request.state.trace_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


# ── POST /assistant/query ──────────────────────────────────────────────────────

@app.post("/assistant/query", response_model=QueryResponse)
async def query_assistant(
    body:      QueryRequest,
    request:   Request,
    principal: Principal = Depends(get_principal),
) -> QueryResponse:
    logger.info(
        '"endpoint":"query","user":"%s","tenant":"%s","trace":"%s"',
        principal.user_id, principal.tenant_id, request.state.trace_id,
    )
    return rag.answer_query(body.question, principal.tenant_id, principal.roles)


# ── POST /requests ─────────────────────────────────────────────────────────────

@app.post("/requests", response_model=RequestRecord, status_code=201)
async def create_request(
    body:             CreateRequestBody,
    request:          Request,
    idempotency_key:  str = Header(..., alias="Idempotency-Key"),
    principal:        Principal = Depends(get_principal),
) -> RequestRecord:
    logger.info(
        '"endpoint":"create_request","user":"%s","tenant":"%s","trace":"%s"',
        principal.user_id, principal.tenant_id, request.state.trace_id,
    )
    return workflow.create_request(
        body=body,
        requester_id=principal.user_id,
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
        trace_id=request.state.trace_id,
    )


# ── POST /requests/{request_id}/decision ──────────────────────────────────────

@app.post("/requests/{request_id}/decision", response_model=DecisionResponse)
async def decide_request(
    request_id: str,
    body:       DecisionBody,
    request:    Request,
    principal:  Principal = Depends(get_principal),
) -> DecisionResponse:
    logger.info(
        '"endpoint":"decision","user":"%s","tenant":"%s","decision":"%s","trace":"%s"',
        principal.user_id, principal.tenant_id, body.decision, request.state.trace_id,
    )
    result = workflow.decide_request(
        request_id=request_id,
        decision=body.decision.value,
        comment=body.comment,
        actor_id=principal.user_id,
        actor_tenant_id=principal.tenant_id,
        trace_id=request.state.trace_id,
    )
    return DecisionResponse(
        request=result["request"],
        ticket_state=result["ticket_state"],
    )

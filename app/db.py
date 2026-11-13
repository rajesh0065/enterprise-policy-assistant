import os
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
    document_id  TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    policy_family TEXT NOT NULL,
    version      INTEGER NOT NULL,
    effective_date TEXT NOT NULL,
    active       INTEGER NOT NULL,
    access_scope TEXT NOT NULL,
    title        TEXT NOT NULL,
    text         TEXT NOT NULL,
    rules        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directory (
    user_id    TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    roles      TEXT NOT NULL,
    manager_id TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    id                        TEXT PRIMARY KEY,
    tenant_id                 TEXT NOT NULL,
    requester_id              TEXT NOT NULL,
    approver_id               TEXT NOT NULL,
    amount                    REAL NOT NULL,
    currency                  TEXT NOT NULL DEFAULT 'INR',
    category                  TEXT NOT NULL,
    description               TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
    requires_manager_approval INTEGER NOT NULL DEFAULT 0,
    idempotency_key           TEXT NOT NULL,
    payload_hash              TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    decided_at                TEXT,
    UNIQUE(tenant_id, requester_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    ts          TEXT NOT NULL,
    trace_id    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   TEXT NOT NULL UNIQUE,
    external_ref TEXT NOT NULL UNIQUE,
    external_id  TEXT,
    state        TEXT NOT NULL DEFAULT 'PENDING',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);
"""


def _db_path() -> str:
    return os.environ.get("APP_DB_PATH", "data.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()

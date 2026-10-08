"""SQLite storage. Plain SQL on purpose: the schema *is* the documentation."""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    email   TEXT NOT NULL UNIQUE,
    role    TEXT NOT NULL CHECK (role IN ('requester', 'agent', 'admin')),
    queue   TEXT                     -- the queue an agent works
);

CREATE TABLE IF NOT EXISTS tickets (
    id                INTEGER PRIMARY KEY,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    requester_id      INTEGER NOT NULL REFERENCES users(id),
    assignee_id       INTEGER REFERENCES users(id),
    queue             TEXT NOT NULL,
    category          TEXT NOT NULL,
    impact            TEXT NOT NULL,
    urgency           TEXT NOT NULL,
    priority          TEXT NOT NULL,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    first_response_at TEXT,          -- first public reply from an agent
    resolved_at       TEXT,
    closed_at         TEXT,
    paused_since      TEXT,          -- set while status = pending
    paused_seconds    INTEGER NOT NULL DEFAULT 0,
    resolution        TEXT,
    routing_reason    TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
    author_id  INTEGER NOT NULL REFERENCES users(id),
    body       TEXT NOT NULL,
    internal   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Append-only audit trail: every change to a ticket lands here.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
    actor_id   INTEGER REFERENCES users(id),   -- NULL = system
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tickets_status   ON tickets(status);
CREATE INDEX IF NOT EXISTS ix_tickets_queue    ON tickets(queue);
CREATE INDEX IF NOT EXISTS ix_comments_ticket  ON comments(ticket_id);
CREATE INDEX IF NOT EXISTS ix_events_ticket    ON events(ticket_id);
"""

SEED_USERS = [
    ("Alice Chen", "alice@example.com", "requester", None),
    ("Bob Martinez", "bob@example.com", "requester", None),
    ("Dana Reyes", "dana@example.com", "agent", "Service Desk"),
    ("Sam Patel", "sam@example.com", "agent", "Network Ops"),
    ("Priya Nair", "priya@example.com", "agent", "Desktop Support"),
    ("Lee Okafor", "lee@example.com", "agent", "Identity & Access"),
    ("Jordan Kim", "jordan@example.com", "agent", "Applications"),
    ("Morgan Blake", "morgan@example.com", "admin", None),
]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, seed_users: bool = True) -> None:
    conn.executescript(SCHEMA)
    if seed_users and conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        with conn:
            conn.executemany(
                "INSERT INTO users (name, email, role, queue) VALUES (?, ?, ?, ?)",
                SEED_USERS,
            )

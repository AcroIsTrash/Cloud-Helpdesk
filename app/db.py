"""PostgreSQL storage, declared with SQLAlchemy Core (ADR-0007).

The tables below are the single source of truth for the schema: Alembic
generates migrations from them, and CI fails if the two drift apart. The app
never creates tables itself; `alembic upgrade head` does.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
)

# Name constraints predictably so migrations can refer to them.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _timestamp(name: str, nullable: bool = False) -> Column[datetime]:
    # Timezone-aware: Postgres stores UTC and hands back aware datetimes.
    return Column(name, DateTime(timezone=True), nullable=nullable)


users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False),
    Column("email", Text, nullable=False, unique=True),
    Column("role", Text, nullable=False),
    Column("queue", Text),  # the queue an agent works
    CheckConstraint("role IN ('requester', 'agent', 'admin')", name="role"),
)

tickets = Table(
    "tickets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("requester_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("assignee_id", Integer, ForeignKey("users.id")),
    Column("queue", Text, nullable=False, index=True),
    Column("category", Text, nullable=False),
    Column("impact", Text, nullable=False),
    Column("urgency", Text, nullable=False),
    Column("priority", Text, nullable=False),
    Column("status", Text, nullable=False, index=True),
    _timestamp("created_at"),
    _timestamp("updated_at"),
    _timestamp("first_response_at", nullable=True),  # first public reply from an agent
    _timestamp("resolved_at", nullable=True),
    _timestamp("closed_at", nullable=True),
    _timestamp("paused_since", nullable=True),  # set while status = pending
    Column("paused_seconds", Integer, nullable=False, server_default="0"),
    Column("resolution", Text),
    Column("routing_reason", Text),
)

comments = Table(
    "comments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ticket_id", Integer, ForeignKey("tickets.id"), nullable=False, index=True),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("internal", Boolean, nullable=False, server_default="false"),
    _timestamp("created_at"),
)

# Append-only audit trail: every change to a ticket lands here.
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ticket_id", Integer, ForeignKey("tickets.id"), nullable=False, index=True),
    Column("actor_id", Integer, ForeignKey("users.id")),  # NULL = system
    Column("kind", Text, nullable=False),
    Column("detail", Text, nullable=False),
    _timestamp("created_at"),
)

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

DEFAULT_URL = "postgresql+psycopg://helpdesk:helpdesk@localhost:5432/helpdesk"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def make_engine(url: str | None = None) -> Engine:
    # Pin the session time zone so timestamps come back in UTC whatever the
    # server's default is; the SLA arithmetic and the API output rely on it.
    return create_engine(
        url or database_url(),
        connect_args={"options": "-c timezone=UTC"},
        pool_pre_ping=True,
    )


def seed_users(conn: Connection) -> None:
    """Create the demo users on an empty database. Schema comes from Alembic."""
    if conn.scalar(select(func.count()).select_from(users)):
        return
    conn.execute(
        insert(users),
        [dict(name=n, email=e, role=r, queue=q) for n, e, r, q in SEED_USERS],
    )
    conn.commit()

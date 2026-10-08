"""Domain vocabulary: statuses, the priority matrix, SLA targets, and API schemas.

Everything that encodes *policy* (which transitions are legal, how priority is
derived, how long each priority gets) lives here so it can be read in one place.
"""
from __future__ import annotations

from datetime import timedelta
from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    NEW = "new"                  # submitted, not yet triaged
    OPEN = "open"                # triaged / assigned, waiting for work
    IN_PROGRESS = "in_progress"  # an agent is actively working it
    PENDING = "pending"          # waiting on the requester; SLA clock paused
    RESOLVED = "resolved"        # fix applied; requester can confirm or reopen
    CLOSED = "closed"            # terminal


# The ticket lifecycle as a state machine. Anything not listed is illegal.
ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.NEW: {Status.OPEN, Status.CLOSED},  # CLOSED here = duplicate / spam
    Status.OPEN: {Status.IN_PROGRESS, Status.PENDING, Status.RESOLVED},
    Status.IN_PROGRESS: {Status.OPEN, Status.PENDING, Status.RESOLVED},
    Status.PENDING: {Status.IN_PROGRESS, Status.RESOLVED},
    Status.RESOLVED: {Status.OPEN, Status.CLOSED},  # OPEN = reopen
    Status.CLOSED: set(),
}


class Level(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


# ITIL-style priority matrix: impact (how many people / how much business)
# crossed with urgency (how fast it hurts). Users describe the problem;
# the system derives priority, so "everything is urgent" can't inflate it.
PRIORITY_MATRIX: dict[tuple[Level, Level], Priority] = {
    (Level.HIGH, Level.HIGH): Priority.P1,
    (Level.HIGH, Level.MEDIUM): Priority.P2,
    (Level.HIGH, Level.LOW): Priority.P3,
    (Level.MEDIUM, Level.HIGH): Priority.P2,
    (Level.MEDIUM, Level.MEDIUM): Priority.P3,
    (Level.MEDIUM, Level.LOW): Priority.P4,
    (Level.LOW, Level.HIGH): Priority.P3,
    (Level.LOW, Level.MEDIUM): Priority.P4,
    (Level.LOW, Level.LOW): Priority.P4,
}


def compute_priority(impact: Level, urgency: Level) -> Priority:
    return PRIORITY_MATRIX[(impact, urgency)]


# (time to first response, time to resolution) — calendar time for simplicity.
SLA_TARGETS: dict[Priority, tuple[timedelta, timedelta]] = {
    Priority.P1: (timedelta(minutes=15), timedelta(hours=4)),
    Priority.P2: (timedelta(hours=1), timedelta(hours=8)),
    Priority.P3: (timedelta(hours=4), timedelta(hours=24)),
    Priority.P4: (timedelta(hours=8), timedelta(hours=72)),
}


class Category(str, Enum):
    NETWORK = "network"
    ACCESS = "access"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    OTHER = "other"


TRIAGE_QUEUE = "Service Desk"

QUEUE_FOR_CATEGORY: dict[Category, str] = {
    Category.NETWORK: "Network Ops",
    Category.ACCESS: "Identity & Access",
    Category.HARDWARE: "Desktop Support",
    Category.SOFTWARE: "Applications",
    Category.OTHER: TRIAGE_QUEUE,
}

ALL_QUEUES = sorted(set(QUEUE_FOR_CATEGORY.values()))


# ---- API request schemas -------------------------------------------------

class TicketCreate(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=5000)
    requester_id: int
    impact: Level = Level.MEDIUM
    urgency: Level = Level.MEDIUM
    category: Category | None = None  # None = let the router decide


class CommentCreate(BaseModel):
    author_id: int
    body: str = Field(min_length=1, max_length=5000)
    internal: bool = False  # internal notes are hidden from the requester


class TransitionRequest(BaseModel):
    actor_id: int
    to_status: Status
    note: str | None = None


class AssignRequest(BaseModel):
    actor_id: int
    assignee_id: int | None  # None = unassign

"""Business rules. The web layer calls these; these never know about HTTP.

Every mutation runs in a transaction and writes an audit event, so the
timeline on a ticket is a complete record of who did what and when.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Select, func, insert, select, update

from .db import comments, events, tickets, users
from .models import (
    ALLOWED_TRANSITIONS,
    QUEUE_FOR_CATEGORY,
    SLA_TARGETS,
    TRIAGE_QUEUE,
    Category,
    CommentCreate,
    Priority,
    Status,
    TicketCreate,
    compute_priority,
)
from .routing import Router, RoutingDecision

log = logging.getLogger(__name__)

# A database row as a plain dict: column name -> value.
Row = dict[str, Any]

AGENT_ROLES = {"agent", "admin"}
# Requesters may only confirm or reject a fix on their own ticket.
REQUESTER_TRANSITIONS = {(Status.RESOLVED, Status.OPEN), (Status.RESOLVED, Status.CLOSED)}
AT_RISK_THRESHOLD = 0.75


class TicketError(Exception):
    """A business-rule violation (maps to HTTP 400)."""


class NotFound(TicketError):
    """Maps to HTTP 404."""


class PermissionDenied(TicketError):
    """Maps to HTTP 403."""


# ---- helpers -------------------------------------------------------------


def utcnow() -> datetime:
    # Whole seconds: Postgres would keep microseconds and change the API output.
    return datetime.now(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@contextmanager
def _atomic(conn: Connection) -> Iterator[None]:
    """Run a command as one transaction: commit it all, or roll it all back.

    A command's checks and writes share the transaction, so an Event never
    commits without the change it records, and a failed check releases the
    ticket's row lock straight away.
    """
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def is_agent(user: Row) -> bool:
    return user["role"] in AGENT_ROLES


def get_user(conn: Connection, user_id: int) -> Row:
    row = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
    if row is None:
        raise NotFound(f"user {user_id} not found")
    return dict(row)


def list_users(conn: Connection, roles: set[str] | None = None) -> list[Row]:
    query = select(users).order_by(users.c.role, users.c.name)
    rows = [dict(r) for r in conn.execute(query).mappings()]
    return [r for r in rows if roles is None or r["role"] in roles]


def _ticket(conn: Connection, ticket_id: int) -> Row:
    """Read a ticket for a command, locking its row until the command commits.

    Without the lock, two app tasks changing the same ticket at once could both
    act on the same old state (bank the same paused time twice, say).
    """
    query = select(tickets).where(tickets.c.id == ticket_id).with_for_update()
    row = conn.execute(query).mappings().first()
    if row is None:
        raise NotFound(f"ticket {ticket_id} not found")
    return dict(row)


def _log(
    conn: Connection,
    ticket_id: int,
    actor_id: int | None,
    kind: str,
    detail: str,
    now: datetime,
) -> None:
    conn.execute(
        insert(events).values(
            ticket_id=ticket_id, actor_id=actor_id, kind=kind, detail=detail, created_at=now
        )
    )


def _update(conn: Connection, ticket_id: int, now: datetime, **fields: Any) -> None:
    conn.execute(update(tickets).where(tickets.c.id == ticket_id).values(updated_at=now, **fields))


def _unpause(t: Row, now: datetime) -> Row:
    """Fields that stop the SLA pause clock and bank the paused time."""
    paused = int((now - t["paused_since"]).total_seconds())
    return {"paused_seconds": t["paused_seconds"] + paused, "paused_since": None}


def _triage(reason: str) -> RoutingDecision:
    return RoutingDecision(Category.OTHER, TRIAGE_QUEUE, reason, 0.0)


def _safe_route(router: Router, title: str, description: str) -> tuple[RoutingDecision, bool]:
    """Ask the router, but never let routing lose a ticket.

    Returns (decision, fell_back). If the router raises (an AI router timing
    out, say) or returns a decision that breaks the contract, the ticket goes
    to the triage queue and the reason is recorded for later review.
    """
    try:
        d = router.route(title, description)
    except Exception as exc:  # any router failure must degrade, not crash
        log.warning(
            "router %s failed; sending ticket to triage", type(router).__name__, exc_info=True
        )
        return _triage(
            f"router failed ({type(exc).__name__}: {str(exc)[:200]}); sent to triage"
        ), True

    if not isinstance(d, RoutingDecision) or not isinstance(d.category, Category):
        return _triage(f"router returned an invalid decision ({d!r:.200}); sent to triage"), True
    if d.queue != QUEUE_FOR_CATEGORY[d.category]:
        return _triage(
            f"router sent category {d.category.value} to queue {d.queue!r}, "
            f"expected {QUEUE_FOR_CATEGORY[d.category]!r}; sent to triage"
        ), True
    if not 0.0 <= d.confidence <= 1.0:
        return _triage(f"router confidence {d.confidence!r} outside 0–1; sent to triage"), True
    return d, False


# ---- commands ------------------------------------------------------------


def create_ticket(
    conn: Connection, data: TicketCreate, router: Router, now: datetime | None = None
) -> int:
    now = now or utcnow()
    priority = compute_priority(data.impact, data.urgency)

    fell_back = False
    if data.category is not None:
        decision = RoutingDecision(
            data.category, QUEUE_FOR_CATEGORY[data.category], "category chosen by requester", 1.0
        )
    else:
        decision, fell_back = _safe_route(router, data.title, data.description)

    with _atomic(conn):
        get_user(conn, data.requester_id)
        tid: int = conn.execute(
            insert(tickets)
            .values(
                title=data.title.strip(),
                description=data.description.strip(),
                requester_id=data.requester_id,
                queue=decision.queue,
                category=decision.category.value,
                impact=data.impact.value,
                urgency=data.urgency.value,
                priority=priority.value,
                status=Status.NEW.value,
                created_at=now,
                updated_at=now,
                routing_reason=decision.reason,
            )
            .returning(tickets.c.id)
        ).scalar_one()
        _log(
            conn,
            tid,
            data.requester_id,
            "created",
            f"Ticket created as {priority.value} (impact {data.impact.value}, "
            f"urgency {data.urgency.value})",
            now,
        )
        # A distinct event kind makes the fallback rate easy to measure.
        _log(
            conn,
            tid,
            None,
            "routing_fallback" if fell_back else "routed",
            f"Routed to {decision.queue} as {decision.category.value}: "
            f"{decision.reason} (confidence {decision.confidence:.0%})",
            now,
        )
    return tid


def transition(
    conn: Connection,
    ticket_id: int,
    actor_id: int,
    to_status: Status,
    note: str | None = None,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    with _atomic(conn):
        t = _ticket(conn, ticket_id)
        actor = get_user(conn, actor_id)
        current = Status(t["status"])
        note = (note or "").strip() or None

        if to_status == current:
            raise TicketError(f"ticket is already {current.value}")
        if to_status not in ALLOWED_TRANSITIONS[current]:
            raise TicketError(f"cannot move a ticket from {current.value} to {to_status.value}")
        if not is_agent(actor) and (
            t["requester_id"] != actor_id or (current, to_status) not in REQUESTER_TRANSITIONS
        ):
            raise PermissionDenied("requesters can only reopen or close their own resolved tickets")
        if to_status == Status.RESOLVED and not note:
            raise TicketError("a resolution note is required to resolve a ticket")
        if current == Status.NEW and to_status == Status.CLOSED and not note:
            raise TicketError("a reason is required to close an untriaged ticket")

        fields: dict[str, Any] = {"status": to_status.value}
        if current == Status.PENDING:
            fields.update(_unpause(t, now))
        if to_status == Status.PENDING:
            fields["paused_since"] = now
        if to_status == Status.RESOLVED:
            fields.update(resolved_at=now, resolution=note)
        if current == Status.RESOLVED and to_status == Status.OPEN:
            fields.update(resolved_at=None, resolution=None)  # reopen: clock keeps running
        if to_status == Status.CLOSED:
            fields["closed_at"] = now

        auto_assign = (
            to_status == Status.IN_PROGRESS and t["assignee_id"] is None and is_agent(actor)
        )
        if auto_assign:
            fields["assignee_id"] = actor_id

        _update(conn, ticket_id, now, **fields)
        _log(
            conn,
            ticket_id,
            actor_id,
            "status",
            f"{current.value} → {to_status.value}" + (f": {note}" if note else ""),
            now,
        )
        if auto_assign:
            _log(
                conn,
                ticket_id,
                actor_id,
                "assigned",
                f"Auto-assigned to {actor['name']} on starting work",
                now,
            )


def assign(
    conn: Connection,
    ticket_id: int,
    actor_id: int,
    assignee_id: int | None,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    with _atomic(conn):
        t = _ticket(conn, ticket_id)
        actor = get_user(conn, actor_id)
        if not is_agent(actor):
            raise PermissionDenied("only agents can assign tickets")
        if t["status"] == Status.CLOSED.value:
            raise TicketError("closed tickets cannot be reassigned")

        fields: dict[str, Any] = {}
        logs: list[tuple[str, str]] = []
        if assignee_id is None:
            if t["assignee_id"] is None:
                raise TicketError("ticket is already unassigned")
            fields["assignee_id"] = None
            logs.append(("assigned", "Unassigned"))
        else:
            assignee = get_user(conn, assignee_id)
            if not is_agent(assignee):
                raise TicketError(f"{assignee['name']} is not an agent")
            if t["assignee_id"] == assignee_id:
                raise TicketError(f"already assigned to {assignee['name']}")
            fields["assignee_id"] = assignee_id
            logs.append(("assigned", f"Assigned to {assignee['name']}"))
            if assignee["queue"] and assignee["queue"] != t["queue"]:
                fields["queue"] = assignee["queue"]
                logs.append(("queue", f"Queue changed {t['queue']} → {assignee['queue']}"))
            if t["status"] == Status.NEW.value:
                fields["status"] = Status.OPEN.value
                logs.append(("status", "new → open: triaged by assignment"))

        _update(conn, ticket_id, now, **fields)
        for kind, detail in logs:
            _log(conn, ticket_id, actor_id, kind, detail, now)


def add_comment(
    conn: Connection, ticket_id: int, data: CommentCreate, now: datetime | None = None
) -> int:
    now = now or utcnow()
    with _atomic(conn):
        t = _ticket(conn, ticket_id)
        author = get_user(conn, data.author_id)
        agent = is_agent(author)

        if t["status"] == Status.CLOSED.value:
            raise TicketError("closed tickets cannot receive comments; open a new ticket")
        if data.internal and not agent:
            raise PermissionDenied("only agents can post internal notes")
        if not agent and author["id"] != t["requester_id"]:
            raise PermissionDenied("requesters can only comment on their own tickets")

        comment_id: int = conn.execute(
            insert(comments)
            .values(
                ticket_id=ticket_id,
                author_id=author["id"],
                body=data.body.strip(),
                internal=data.internal,
                created_at=now,
            )
            .returning(comments.c.id)
        ).scalar_one()
        if agent and not data.internal and t["first_response_at"] is None:
            # Internal notes don't count: the SLA measures the *customer's* wait.
            _update(conn, ticket_id, now, first_response_at=now)
            _log(
                conn,
                ticket_id,
                author["id"],
                "first_response",
                f"First response by {author['name']}",
                now,
            )
        elif not agent and t["status"] == Status.PENDING.value:
            _update(conn, ticket_id, now, status=Status.IN_PROGRESS.value, **_unpause(t, now))
            _log(
                conn,
                ticket_id,
                author["id"],
                "status",
                "pending → in_progress: requester replied, SLA clock resumed",
                now,
            )
        else:
            _update(conn, ticket_id, now)
        return comment_id


# ---- queries -------------------------------------------------------------

_requester = users.alias("r")
_assignee = users.alias("a")


def _ticket_select() -> Select[Any]:
    """Tickets with the requester's and assignee's names joined on."""
    return select(
        tickets,
        _requester.c.name.label("requester_name"),
        _assignee.c.name.label("assignee_name"),
    ).select_from(
        tickets.join(_requester, _requester.c.id == tickets.c.requester_id).outerjoin(
            _assignee, _assignee.c.id == tickets.c.assignee_id
        )
    )


def _own_tickets_only(viewer: Row | None) -> int | None:
    """The requester id to filter on: requesters see only their own tickets."""
    return viewer["id"] if viewer is not None and not is_agent(viewer) else None


def list_tickets(
    conn: Connection,
    status: Status | None = None,
    include_closed: bool = False,
    queue: str | None = None,
    assignee_id: int | None = None,
    viewer: Row | None = None,
) -> list[Row]:
    """Tickets matching the filters that `viewer` may see (None = everything)."""
    query = _ticket_select()
    if status is not None:
        query = query.where(tickets.c.status == status.value)
    elif not include_closed:
        query = query.where(tickets.c.status != Status.CLOSED.value)
    for col, val in (
        (tickets.c.queue, queue),
        (tickets.c.assignee_id, assignee_id),
        (tickets.c.requester_id, _own_tickets_only(viewer)),
    ):
        if val is not None:
            query = query.where(col == val)
    # "P1" < "P2" < ... as text, so priority order is P1 first.
    query = query.order_by(tickets.c.priority, tickets.c.created_at)
    return [dict(r) for r in conn.execute(query).mappings()]


def status_counts(conn: Connection, viewer: Row | None = None) -> dict[str, int]:
    counts = {s.value: 0 for s in Status}
    query = select(tickets.c.status, func.count()).group_by(tickets.c.status)
    if (requester_id := _own_tickets_only(viewer)) is not None:
        query = query.where(tickets.c.requester_id == requester_id)
    for status, n in conn.execute(query):
        counts[status] = n
    return counts


def ticket_detail(conn: Connection, ticket_id: int, viewer: Row | None = None) -> Row:
    row = conn.execute(_ticket_select().where(tickets.c.id == ticket_id)).mappings().first()
    if row is None:
        raise NotFound(f"ticket {ticket_id} not found")
    ticket = dict(row)
    if viewer is not None and not is_agent(viewer) and ticket["requester_id"] != viewer["id"]:
        raise PermissionDenied("you can only view your own tickets")

    show_internal = viewer is None or is_agent(viewer)
    comment_query = (
        select(comments, users.c.name.label("author_name"))
        .join(users, users.c.id == comments.c.author_id)
        .where(comments.c.ticket_id == ticket_id)
        .order_by(comments.c.id)
    )
    if not show_internal:
        comment_query = comment_query.where(comments.c.internal.is_(False))
    comment_rows = [dict(r) | {"type": "comment"} for r in conn.execute(comment_query).mappings()]
    event_query = (
        select(events, users.c.name.label("actor_name"))
        .outerjoin(users, users.c.id == events.c.actor_id)
        .where(events.c.ticket_id == ticket_id)
        .order_by(events.c.id)
    )
    event_rows = [dict(r) | {"type": "event"} for r in conn.execute(event_query).mappings()]
    timeline = sorted(
        comment_rows + event_rows, key=lambda x: (x["created_at"], x["type"] == "comment")
    )
    return {
        "ticket": ticket,
        "comments": comment_rows,
        "events": event_rows,
        "timeline": timeline,
        "sla": sla_status(ticket),
    }


def next_statuses(ticket: Row, viewer: Row) -> list[Status]:
    current = Status(ticket["status"])
    options = sorted(ALLOWED_TRANSITIONS[current], key=list(Status).index)
    if is_agent(viewer):
        return options
    if ticket["requester_id"] != viewer["id"]:
        return []
    return [s for s in options if (current, s) in REQUESTER_TRANSITIONS]


# ---- SLA -----------------------------------------------------------------


def _clock(elapsed: timedelta, target: timedelta, done: bool) -> Row:
    ratio = elapsed / target
    if done:
        state = "met" if elapsed <= target else "breached"
    elif ratio >= 1:
        state = "breached"
    elif ratio >= AT_RISK_THRESHOLD:
        state = "at_risk"
    else:
        state = "on_track"
    return {
        "state": state,
        "target_minutes": round(target.total_seconds() / 60),
        "elapsed_minutes": round(elapsed.total_seconds() / 60),
        "remaining_minutes": round((target - elapsed).total_seconds() / 60),
        "percent": min(round(ratio * 100), 999),
    }


def sla_status(t: Row, now: datetime | None = None) -> Row:
    """Two clocks: time to first response, and time to resolution (minus pauses).

    A ticket closed without ever being resolved (duplicate, spam) was never real
    work, so its clocks report `cancelled` instead of counting as a breach.
    """
    now = now or utcnow()
    response_target, resolution_target = SLA_TARGETS[Priority(t["priority"])]
    created: datetime = t["created_at"]
    resolved: datetime | None = t["resolved_at"]
    closed: datetime | None = t["closed_at"]
    responded: datetime | None = t["first_response_at"]
    cancelled = closed is not None and resolved is None

    if cancelled and responded is None:
        response: Row = {"state": "cancelled"}
    else:
        # Resolving or closing a ticket is itself an answer to the requester.
        answered = responded or resolved or closed
        response = _clock((answered or now) - created, response_target, done=answered is not None)

    if cancelled:
        resolution: Row = {"state": "cancelled"}
    else:
        paused = timedelta(seconds=t["paused_seconds"])
        if t["paused_since"]:
            paused += now - t["paused_since"]
        resolution = _clock(
            (resolved or now) - created - paused, resolution_target, done=resolved is not None
        )
        if t["paused_since"] and resolution["state"] != "breached":
            # Pausing stops the clock, but it can't un-breach a missed target.
            resolution["state"] = "paused"
        resolution["paused_minutes"] = round(paused.total_seconds() / 60)
        if not resolved:
            resolution["due_at"] = _iso(created + resolution_target + paused)
    return {"response": response, "resolution": resolution}

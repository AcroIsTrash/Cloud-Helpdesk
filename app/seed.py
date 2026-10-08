"""Demo data so the app looks alive on first launch."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import services as svc
from .models import CommentCreate, Level, Status, TicketCreate
from .routing import Router


def seed_demo(conn: sqlite3.Connection, router: Router) -> None:
    if conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]:
        return
    u = {r["email"].split("@")[0]: r["id"] for r in svc.list_users(conn)}
    now = svc.utcnow()

    def ago(**kw: float) -> datetime:
        return now - timedelta(**kw)

    # 1. Network issue, waiting on the requester (SLA paused)
    t = svc.create_ticket(
        conn,
        TicketCreate(
            title="VPN drops every 10 minutes",
            description="Working from home and the VPN disconnects constantly. "
            "Wifi itself seems fine.",
            requester_id=u["alice"],
            impact=Level.MEDIUM,
            urgency=Level.HIGH,
        ),
        router,
        ago(hours=3),
    )
    svc.assign(conn, t, u["sam"], u["sam"], ago(hours=2, minutes=50))
    svc.transition(conn, t, u["sam"], Status.IN_PROGRESS, now=ago(hours=2, minutes=48))
    svc.add_comment(
        conn,
        t,
        CommentCreate(
            author_id=u["sam"],
            internal=True,
            body="Seeing similar reports from two other remote users; "
            "checking the VPN concentrator.",
        ),
        ago(hours=2, minutes=46),
    )
    svc.add_comment(
        conn,
        t,
        CommentCreate(
            author_id=u["sam"],
            body="Thanks Alice. Can you send the client version from Help > About, and whether it "
            "happens on a wired connection too?",
        ),
        ago(hours=2, minutes=45),
    )
    svc.transition(
        conn,
        t,
        u["sam"],
        Status.PENDING,
        "Waiting on client version from requester",
        ago(hours=2, minutes=44),
    )

    # 2. P1 access issue nobody has answered yet: response SLA breached
    svc.create_ticket(
        conn,
        TicketCreate(
            title="Locked out after password reset",
            description="Reset my password this morning and now I can't log in anywhere. "
            "MFA prompt never arrives.",
            requester_id=u["bob"],
            impact=Level.HIGH,
            urgency=Level.HIGH,
        ),
        router,
        ago(minutes=40),
    )

    # 3. Hardware issue, resolved, awaiting requester confirmation
    t = svc.create_ticket(
        conn,
        TicketCreate(
            title="Laptop battery won't hold a charge",
            description="Battery goes from 100% to dead in about 40 minutes, even when idle.",
            requester_id=u["alice"],
            impact=Level.LOW,
            urgency=Level.LOW,
        ),
        router,
        ago(days=2),
    )
    svc.assign(conn, t, u["priya"], u["priya"], ago(days=1, hours=23))
    svc.add_comment(
        conn,
        t,
        CommentCreate(
            author_id=u["priya"],
            body="Replacement battery ordered. I'll swap it at your desk tomorrow morning.",
        ),
        ago(days=1, hours=22),
    )
    svc.transition(conn, t, u["priya"], Status.IN_PROGRESS, now=ago(days=1, hours=2))
    svc.transition(
        conn,
        t,
        u["priya"],
        Status.RESOLVED,
        "Replaced battery; ran diagnostics, health 100%.",
        ago(days=1),
    )

    # 4. Software issue, routed but unassigned
    svc.create_ticket(
        conn,
        TicketCreate(
            title="Excel crashes opening the shared budget workbook",
            description="Excel crashes every time I open the finance budget workbook "
            "from the shared drive. Other workbooks open fine.",
            requester_id=u["bob"],
            impact=Level.MEDIUM,
            urgency=Level.MEDIUM,
        ),
        router,
        ago(hours=5),
    )

    # 5. Doesn't match any rule: lands in triage
    svc.create_ticket(
        conn,
        TicketCreate(
            title="Where do I request a desk phone?",
            description="Starting in the new office next week and I'll need a desk phone set up.",
            requester_id=u["alice"],
            impact=Level.LOW,
            urgency=Level.MEDIUM,
        ),
        router,
        ago(minutes=10),
    )

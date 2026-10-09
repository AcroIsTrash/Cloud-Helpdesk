import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, update

from app import services as svc
from app.db import users
from app.models import (
    Category,
    CommentCreate,
    Level,
    Priority,
    Status,
    TicketCreate,
    compute_priority,
)
from app.routing import KeywordRouter, RoutingDecision

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
router = KeywordRouter()


@pytest.fixture
def ids(conn):
    return {u["email"].split("@")[0]: u["id"] for u in svc.list_users(conn)}


def make(conn, ids, **kw):
    data = (
        dict(
            title="VPN keeps dropping",
            description="The VPN disconnects every few minutes.",
            requester_id=ids["alice"],
            impact=Level.MEDIUM,
            urgency=Level.MEDIUM,
        )
        | kw
    )
    return svc.create_ticket(conn, TicketCreate(**data), router, T0)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


# ---- priority & routing ----------------------------------------------------


def test_priority_matrix():
    assert compute_priority(Level.HIGH, Level.HIGH) == Priority.P1
    assert compute_priority(Level.MEDIUM, Level.HIGH) == Priority.P2
    assert compute_priority(Level.LOW, Level.LOW) == Priority.P4


@pytest.mark.parametrize(
    "title,desc,queue",
    [
        ("VPN keeps dropping", "disconnects from wifi", "Network Ops"),
        ("Locked out", "password reset broke my login", "Identity & Access"),
        ("Monitor flickers", "second screen on the dock flickers", "Desktop Support"),
        ("Outlook crashes", "error on startup", "Applications"),
        ("Question", "where is the kitchen", "Service Desk"),
    ],
)
def test_keyword_routing(title, desc, queue):
    assert router.route(title, desc).queue == queue


def test_word_boundaries_prevent_false_matches():
    # "happy" contains "app"; "accounting" contains "account"
    assert router.route("Happy hour", "accounting team social").queue == "Service Desk"


def test_explicit_category_overrides_router(conn, ids):
    tid = make(conn, ids, category="hardware")
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert t["queue"] == "Desktop Support"
    assert t["routing_reason"] == "category chosen by requester"


class _CrashingRouter:
    def route(self, title, description):
        raise TimeoutError("model did not answer in 10s")


class _StaticRouter:
    def __init__(self, decision):
        self.decision = decision

    def route(self, title, description):
        return self.decision


def _create_with(conn, ids, a_router):
    data = TicketCreate(
        title="VPN keeps dropping",
        description="The VPN disconnects every few minutes.",
        requester_id=ids["alice"],
    )
    return svc.create_ticket(conn, data, a_router, T0)


def test_router_crash_still_creates_ticket_in_triage(conn, ids):
    tid = _create_with(conn, ids, _CrashingRouter())
    d = svc.ticket_detail(conn, tid)
    assert (d["ticket"]["queue"], d["ticket"]["category"]) == ("Service Desk", "other")
    assert "TimeoutError" in d["ticket"]["routing_reason"]
    assert [e["kind"] for e in d["events"]] == ["created", "routing_fallback"]


@pytest.mark.parametrize(
    "decision",
    [
        RoutingDecision(Category.NETWORK, "Applications", "wrong queue for category", 0.9),
        RoutingDecision(Category.NETWORK, "Network Ops", "overconfident", 1.7),
        "network",  # not a RoutingDecision at all
    ],
)
def test_router_contract_violations_fall_back_to_triage(conn, ids, decision):
    tid = _create_with(conn, ids, _StaticRouter(decision))
    d = svc.ticket_detail(conn, tid)
    assert d["ticket"]["queue"] == "Service Desk"
    assert d["events"][-1]["kind"] == "routing_fallback"


def test_valid_router_decision_is_used_as_is(conn, ids):
    ok = RoutingDecision(Category.NETWORK, "Network Ops", "stub", 0.8)
    d = svc.ticket_detail(conn, _create_with(conn, ids, _StaticRouter(ok)))
    assert d["ticket"]["queue"] == "Network Ops"
    assert d["events"][-1]["kind"] == "routed"


# ---- lifecycle ---------------------------------------------------------------


def test_new_ticket_is_routed_and_audited(conn, ids):
    d = svc.ticket_detail(conn, make(conn, ids))
    assert d["ticket"]["status"] == "new"
    assert d["ticket"]["queue"] == "Network Ops"
    assert [e["kind"] for e in d["events"]] == ["created", "routed"]


def test_assignment_triages_and_moves_queue(conn, ids):
    tid = make(conn, ids)
    svc.assign(conn, tid, ids["dana"], ids["priya"], at(1))
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert t["status"] == "open"
    assert t["queue"] == "Desktop Support"


def test_illegal_transition_rejected(conn, ids):
    tid = make(conn, ids)
    with pytest.raises(svc.TicketError, match="cannot move"):
        svc.transition(conn, tid, ids["dana"], Status.RESOLVED, "done", at(1))


def test_resolve_requires_note(conn, ids):
    tid = make(conn, ids)
    svc.assign(conn, tid, ids["sam"], ids["sam"], at(1))
    with pytest.raises(svc.TicketError, match="resolution note"):
        svc.transition(conn, tid, ids["sam"], Status.RESOLVED, "  ", at(2))


def test_starting_work_auto_assigns(conn, ids):
    tid = make(conn, ids)
    svc.transition(conn, tid, ids["dana"], Status.OPEN, now=at(1))
    svc.transition(conn, tid, ids["sam"], Status.IN_PROGRESS, now=at(2))
    assert svc.ticket_detail(conn, tid)["ticket"]["assignee_id"] == ids["sam"]


def test_requester_permissions(conn, ids):
    tid = make(conn, ids)
    with pytest.raises(svc.PermissionDenied):
        svc.transition(conn, tid, ids["alice"], Status.OPEN, now=at(1))
    with pytest.raises(svc.PermissionDenied):
        svc.add_comment(
            conn, tid, CommentCreate(author_id=ids["alice"], body="x", internal=True), at(1)
        )
    with pytest.raises(svc.PermissionDenied):
        svc.add_comment(conn, tid, CommentCreate(author_id=ids["bob"], body="not mine"), at(1))
    with pytest.raises(svc.PermissionDenied):
        svc.ticket_detail(conn, tid, viewer=svc.get_user(conn, ids["bob"]))


def test_requester_can_reopen_then_close(conn, ids):
    tid = make(conn, ids)
    svc.assign(conn, tid, ids["sam"], ids["sam"], at(1))
    svc.transition(conn, tid, ids["sam"], Status.RESOLVED, "Updated client", at(5))
    svc.transition(conn, tid, ids["alice"], Status.OPEN, now=at(10))
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert t["resolved_at"] is None and t["resolution"] is None
    svc.transition(conn, tid, ids["sam"], Status.RESOLVED, "Replaced router", at(20))
    svc.transition(conn, tid, ids["alice"], Status.CLOSED, now=at(25))
    with pytest.raises(svc.TicketError, match="closed tickets"):
        svc.add_comment(
            conn, tid, CommentCreate(author_id=ids["alice"], body="one more thing"), at(30)
        )


def test_internal_notes_hidden_from_requester(conn, ids):
    tid = make(conn, ids)
    svc.add_comment(
        conn, tid, CommentCreate(author_id=ids["sam"], body="secret", internal=True), at(1)
    )
    svc.add_comment(conn, tid, CommentCreate(author_id=ids["sam"], body="hello"), at(2))
    alice_view = svc.ticket_detail(conn, tid, viewer=svc.get_user(conn, ids["alice"]))
    assert [c["body"] for c in alice_view["comments"]] == ["hello"]


# ---- SLA -----------------------------------------------------------------------


def test_first_response_ignores_internal_notes(conn, ids):
    tid = make(conn, ids)
    svc.add_comment(
        conn, tid, CommentCreate(author_id=ids["sam"], body="note", internal=True), at(5)
    )
    assert svc.ticket_detail(conn, tid)["ticket"]["first_response_at"] is None
    svc.add_comment(conn, tid, CommentCreate(author_id=ids["sam"], body="On it"), at(30))
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert svc.sla_status(t, at(60))["response"]["state"] == "met"  # P3: 4h target


def test_p1_response_breach(conn, ids):
    tid = make(conn, ids, impact=Level.HIGH, urgency=Level.HIGH)
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert svc.sla_status(t, at(10))["response"]["state"] == "on_track"
    assert svc.sla_status(t, at(12))["response"]["state"] == "at_risk"
    assert svc.sla_status(t, at(16))["response"]["state"] == "breached"


def test_pending_pauses_resolution_clock(conn, ids):
    tid = make(conn, ids, impact=Level.HIGH, urgency=Level.HIGH)  # P1: 4h to resolve
    svc.assign(conn, tid, ids["sam"], ids["sam"], at(1))
    svc.transition(conn, tid, ids["sam"], Status.PENDING, "need logs", at(60))
    t = svc.ticket_detail(conn, tid)["ticket"]
    s = svc.sla_status(t, at(60 + 600))  # 10h later, still paused
    assert s["resolution"]["state"] == "paused"
    assert s["resolution"]["elapsed_minutes"] == 60

    # requester replying resumes work and banks the paused time
    svc.add_comment(conn, tid, CommentCreate(author_id=ids["alice"], body="logs attached"), at(660))
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert t["status"] == "in_progress"
    assert t["paused_seconds"] == 600 * 60
    svc.transition(conn, tid, ids["sam"], Status.RESOLVED, "fixed", at(720))
    t = svc.ticket_detail(conn, tid)["ticket"]
    res = svc.sla_status(t, at(9999))["resolution"]
    assert res["state"] == "met" and res["elapsed_minutes"] == 120


def test_pause_cannot_hide_a_breach(conn, ids):
    """Moving a ticket to pending stops the clock; it can't un-miss the target."""
    tid = make(conn, ids, impact=Level.HIGH, urgency=Level.HIGH)  # P1: 4h to resolve
    svc.assign(conn, tid, ids["sam"], ids["sam"], at(1))
    svc.transition(conn, tid, ids["sam"], Status.IN_PROGRESS, now=at(2))
    t = svc.ticket_detail(conn, tid)["ticket"]
    assert svc.sla_status(t, at(300))["resolution"]["state"] == "breached"

    svc.transition(conn, tid, ids["sam"], Status.PENDING, "need logs", at(301))
    res = svc.sla_status(svc.ticket_detail(conn, tid)["ticket"], at(400))["resolution"]
    assert res["state"] == "breached"  # not masked as "paused"
    assert res["elapsed_minutes"] == 301  # but the clock did stop

    # A ticket paused before it breaches still reports as paused.
    tid2 = make(conn, ids, impact=Level.HIGH, urgency=Level.HIGH)
    svc.assign(conn, tid2, ids["sam"], ids["sam"], at(1))
    svc.transition(conn, tid2, ids["sam"], Status.PENDING, "need logs", at(10))
    t2 = svc.ticket_detail(conn, tid2)["ticket"]
    assert svc.sla_status(t2, at(400))["resolution"]["state"] == "paused"


def test_close_untriaged_cancels_both_clocks(conn, ids):
    tid = make(conn, ids)
    with pytest.raises(svc.TicketError, match="reason"):
        svc.transition(conn, tid, ids["dana"], Status.CLOSED, now=at(1))
    svc.transition(conn, tid, ids["dana"], Status.CLOSED, "duplicate of #1", at(1))
    # Spam and duplicates are not work: they must not count against either SLA.
    sla = svc.sla_status(svc.ticket_detail(conn, tid)["ticket"], at(2))
    assert sla["resolution"]["state"] == "cancelled"
    assert sla["response"]["state"] == "cancelled"


def test_answered_then_cancelled_keeps_the_response_clock(conn, ids):
    """An agent who did reply before closing the ticket still gets measured."""
    tid = make(conn, ids, impact=Level.HIGH, urgency=Level.HIGH)  # P1: 15m to respond
    svc.add_comment(conn, tid, CommentCreate(author_id=ids["dana"], body="Looking now"), at(5))
    svc.transition(conn, tid, ids["dana"], Status.CLOSED, "duplicate of #1", at(30))
    sla = svc.sla_status(svc.ticket_detail(conn, tid)["ticket"], at(60))
    assert sla["response"]["state"] == "met"
    assert sla["resolution"]["state"] == "cancelled"


def test_ticket_list_puts_p1_first(conn, ids):
    p4 = make(conn, ids, impact=Level.LOW, urgency=Level.LOW)
    p1 = make(conn, ids, impact=Level.HIGH, urgency=Level.HIGH)
    p2 = make(conn, ids, impact=Level.HIGH, urgency=Level.MEDIUM)
    assert [t["id"] for t in svc.list_tickets(conn)] == [p1, p2, p4]


def test_requesters_list_and_count_only_their_own_tickets(conn, ids):
    alices = make(conn, ids)
    bobs = make(conn, ids, requester_id=ids["bob"])
    alice, dana = svc.get_user(conn, ids["alice"]), svc.get_user(conn, ids["dana"])

    assert [t["id"] for t in svc.list_tickets(conn, viewer=alice)] == [alices]
    assert svc.status_counts(conn, viewer=alice)["new"] == 1
    assert {t["id"] for t in svc.list_tickets(conn, viewer=dana)} == {alices, bobs}
    assert svc.status_counts(conn, viewer=dana)["new"] == 2


def test_rejected_command_leaves_ticket_free_for_others(conn, engine, ids):
    """A failed check rolls back at once, so it can't hold the ticket's lock."""
    tid = make(conn, ids)
    with pytest.raises(svc.PermissionDenied):
        svc.transition(conn, tid, ids["bob"], Status.OPEN, now=at(1))

    with engine.connect() as other:
        other.exec_driver_sql("SET lock_timeout = '1s'")
        svc.transition(other, tid, ids["dana"], Status.OPEN, now=at(2))
    assert svc.ticket_detail(conn, tid)["ticket"]["status"] == "open"


# ---- Deactivation ----------------------------------------------------------------


def test_deactivation_unassigns_open_tickets_and_records_why(conn, ids):
    open_ticket = make(conn, ids)
    svc.assign(conn, open_ticket, ids["dana"], ids["sam"], at(1))
    done = make(conn, ids)
    svc.assign(conn, done, ids["dana"], ids["sam"], at(1))
    svc.transition(conn, done, ids["sam"], Status.RESOLVED, "Fixed the tunnel", at(2))

    svc.deactivate(conn, ids["sam"], ids["morgan"], "Left the company", at(3))

    assert svc.get_user(conn, ids["sam"])["active"] is False
    still_open = svc.ticket_detail(conn, open_ticket)
    assert still_open["ticket"]["assignee_id"] is None
    assert still_open["ticket"]["queue"] == "Network Ops"  # stays in its Queue
    assert still_open["events"][-1]["kind"] == "unassigned"
    assert svc.ticket_detail(conn, done)["ticket"]["assignee_id"] == ids["sam"]
    [event] = svc.access_events(conn, ids["sam"])
    assert (event["kind"], event["actor_id"]) == ("user_deactivated", ids["morgan"])
    assert "Left the company" in event["detail"]


@pytest.mark.parametrize(
    "actor,target,error",
    [
        ("dana", "sam", svc.PermissionDenied),  # an Agent is not an Admin
        ("alice", "sam", svc.PermissionDenied),  # nor is a Requester
        ("morgan", "morgan", svc.PermissionDenied),  # never themselves
    ],
)
def test_only_an_admin_deactivates_and_never_themselves(conn, ids, actor, target, error):
    with pytest.raises(error):
        svc.deactivate(conn, ids[target], ids[actor], "No longer here", at(1))
    assert svc.get_user(conn, ids[target])["active"] is True
    assert svc.access_events(conn, ids[target]) == []


def test_deactivation_needs_a_reason(conn, ids):
    with pytest.raises(svc.TicketError, match="reason"):
        svc.deactivate(conn, ids["sam"], ids["morgan"], "   ", at(1))
    assert svc.get_user(conn, ids["sam"])["active"] is True


def test_assigning_to_a_deactivated_person_is_refused(conn, ids):
    tid = make(conn, ids)
    svc.deactivate(conn, ids["sam"], ids["morgan"], "On leave", at(1))
    with pytest.raises(svc.TicketError, match="deactivated"):
        svc.assign(conn, tid, ids["dana"], ids["sam"], at(2))
    assert svc.ticket_detail(conn, tid)["ticket"]["assignee_id"] is None


def test_reactivation_is_recorded_and_restores_assignment(conn, ids):
    tid = make(conn, ids)
    svc.deactivate(conn, ids["sam"], ids["morgan"], "On leave", at(1))
    with pytest.raises(svc.PermissionDenied):
        svc.reactivate(conn, ids["sam"], ids["dana"], at(2))

    svc.reactivate(conn, ids["sam"], ids["morgan"], at(3))

    assert svc.get_user(conn, ids["sam"])["active"] is True
    assert [e["kind"] for e in svc.access_events(conn, ids["sam"])] == [
        "user_deactivated",
        "user_reactivated",
    ]
    svc.assign(conn, tid, ids["dana"], ids["sam"], at(4))
    assert svc.ticket_detail(conn, tid)["ticket"]["assignee_id"] == ids["sam"]


def test_assignment_waits_for_a_deactivation_in_flight(conn, engine, ids):
    """Another task is mid-Deactivation of Sam when an Agent assigns to him.

    The assignment must wait for it, then see Sam deactivated and refuse;
    otherwise the Ticket lands on Sam after Deactivation swept his Tickets.
    """
    tid = make(conn, ids)
    with engine.connect() as deactivating:
        deactivating.execute(select(users).where(users.c.id == ids["sam"]).with_for_update())
        deactivating.execute(update(users).where(users.c.id == ids["sam"]).values(active=False))

        with ThreadPoolExecutor(1) as pool:
            assigning = pool.submit(svc.assign, conn, tid, ids["dana"], ids["sam"], at(1))
            _wait_until_blocked(engine)
            deactivating.commit()
            with pytest.raises(svc.TicketError, match="deactivated"):
                assigning.result(timeout=10)
    assert svc.ticket_detail(conn, tid)["ticket"]["assignee_id"] is None


def _wait_until_blocked(engine, timeout=5.0):
    """Wait until some session is waiting on a row lock."""
    deadline = time.monotonic() + timeout
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            waiting = probe.exec_driver_sql(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
            ).scalar_one()
            probe.rollback()
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError("nothing ever waited on the lock")


def test_a_deactivated_admin_cannot_deactivate_anyone(conn, ids):
    """Two Admins switching each other off must not leave the desk with none."""
    other = conn.execute(
        insert(users)
        .values(name="Robin Admin", email="robin@example.com", role="admin")
        .returning(users.c.id)
    ).scalar_one()
    conn.commit()
    svc.deactivate(conn, ids["morgan"], other, "Handover", at(1))
    with pytest.raises(svc.PermissionDenied, match="deactivated"):
        svc.deactivate(conn, other, ids["morgan"], "Revenge", at(2))
    assert svc.get_user(conn, other)["active"] is True

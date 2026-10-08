"""FastAPI app: a JSON API under /api and a server-rendered UI.

Run:  uv run python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import services as svc
from .db import connect, init_db
from .models import (
    ALL_QUEUES,
    PRIORITY_MATRIX,
    AssignRequest,
    Category,
    CommentCreate,
    Level,
    Status,
    TicketCreate,
    TransitionRequest,
)
from .routing import KeywordRouter
from .seed import seed_demo

# Swap this for an AI router later; nothing else needs to change.
ROUTER = KeywordRouter()


def db_path() -> str:
    return os.environ.get("HELPDESK_DB", "helpdesk.db")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    conn = connect(db_path())
    init_db(conn)
    if os.environ.get("HELPDESK_DEMO", "1") == "1":
        seed_demo(conn, ROUTER)
    conn.close()
    yield


app = FastAPI(title="Help Desk", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect(db_path())
    try:
        yield conn
    finally:
        conn.close()


# ---- template filters ------------------------------------------------------


def _ago(value: str | None) -> str:
    if not value:
        return ""
    s = int((svc.utcnow() - datetime.fromisoformat(value)).total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _duration(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    sign, m = ("-" if minutes < 0 else ""), abs(int(minutes))
    d, rem = divmod(m, 1440)
    h, mm = divmod(rem, 60)
    parts = [f"{d}d"] if d else []
    if h:
        parts.append(f"{h}h")
    if mm or not parts:
        parts.append(f"{mm}m")
    return sign + " ".join(parts[:2])


templates.env.filters["ago"] = _ago
templates.env.filters["duration"] = _duration
templates.env.filters["label"] = lambda v: str(v).replace("_", " ")


# ---- JSON API ----------------------------------------------------------------


@app.exception_handler(svc.TicketError)
async def ticket_error_handler(_: Request, exc: svc.TicketError) -> JSONResponse:
    code = (
        404
        if isinstance(exc, svc.NotFound)
        else 403
        if isinstance(exc, svc.PermissionDenied)
        else 400
    )
    return JSONResponse({"detail": str(exc)}, status_code=code)


@app.get("/api/users", tags=["api"], response_model=None)
def api_users(conn: sqlite3.Connection = Depends(get_conn)) -> list[svc.Row]:
    return svc.list_users(conn)


@app.get("/api/tickets", tags=["api"], response_model=None)
def api_list(
    status: Status | None = None,
    include_closed: bool = False,
    queue: str | None = None,
    assignee_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[svc.Row]:
    rows = svc.list_tickets(
        conn, status=status, include_closed=include_closed, queue=queue, assignee_id=assignee_id
    )
    return [r | {"sla": svc.sla_status(r)} for r in rows]


@app.post("/api/tickets", status_code=201, tags=["api"], response_model=None)
def api_create(body: TicketCreate, conn: sqlite3.Connection = Depends(get_conn)) -> svc.Row:
    tid = svc.create_ticket(conn, body, ROUTER)
    return svc.ticket_detail(conn, tid)


@app.get("/api/tickets/{ticket_id}", tags=["api"], response_model=None)
def api_detail(ticket_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> svc.Row:
    return svc.ticket_detail(conn, ticket_id)


@app.post("/api/tickets/{ticket_id}/transition", tags=["api"], response_model=None)
def api_transition(
    ticket_id: int, body: TransitionRequest, conn: sqlite3.Connection = Depends(get_conn)
) -> svc.Row:
    svc.transition(conn, ticket_id, body.actor_id, body.to_status, body.note)
    return svc.ticket_detail(conn, ticket_id)


@app.post("/api/tickets/{ticket_id}/assign", tags=["api"], response_model=None)
def api_assign(
    ticket_id: int, body: AssignRequest, conn: sqlite3.Connection = Depends(get_conn)
) -> svc.Row:
    svc.assign(conn, ticket_id, body.actor_id, body.assignee_id)
    return svc.ticket_detail(conn, ticket_id)


@app.post("/api/tickets/{ticket_id}/comments", status_code=201, tags=["api"], response_model=None)
def api_comment(
    ticket_id: int, body: CommentCreate, conn: sqlite3.Connection = Depends(get_conn)
) -> svc.Row:
    svc.add_comment(conn, ticket_id, body)
    return svc.ticket_detail(conn, ticket_id)


# ---- HTML UI -----------------------------------------------------------------
# No real auth: an "acting as" switcher stores a user id in a cookie so you can
# demo the requester and agent views side by side.


def current_user(request: Request, conn: sqlite3.Connection) -> svc.Row:
    try:
        return svc.get_user(conn, int(request.cookies.get("acting_as", "")))
    except (ValueError, svc.NotFound):
        return svc.list_users(conn, roles={"agent"})[0]


def render(request: Request, conn: sqlite3.Connection, name: str, **ctx: Any) -> HTMLResponse:
    user = current_user(request, conn)
    ctx.update(
        users=svc.list_users(conn),
        current_user=user,
        is_agent=svc.is_agent(user),
        error=request.query_params.get("error"),
    )
    return templates.TemplateResponse(request, name, ctx)


def back(path: str, error: Exception | str | None = None) -> RedirectResponse:
    url = f"{path}?{urlencode({'error': str(error)})}" if error else path
    return RedirectResponse(url, status_code=303)


@app.post("/act-as", include_in_schema=False)
def act_as(user_id: int = Form(...), next: str = Form("/")) -> RedirectResponse:
    # Only same-site paths: "//host" and "/\host" are protocol-relative URLs.
    if not next.startswith("/") or next[1:2] in {"/", "\\"}:
        next = "/"
    resp = RedirectResponse(next, status_code=303)
    resp.set_cookie("acting_as", str(user_id), httponly=True, samesite="lax")
    return resp


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(
    request: Request,
    status: str = "",
    queue: str = "",
    mine: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
) -> HTMLResponse:
    user = current_user(request, conn)
    agent = svc.is_agent(user)
    status_enum = Status(status) if status in {s.value for s in Status} else None
    rows = svc.list_tickets(
        conn,
        status=status_enum,
        include_closed=(status == "all"),
        queue=queue or None,
        assignee_id=user["id"] if (mine and agent) else None,
        requester_id=None if agent else user["id"],
    )
    for r in rows:
        r["sla"] = svc.sla_status(r)
    return render(
        request,
        conn,
        "list.html",
        tickets=rows,
        counts=svc.status_counts(conn, None if agent else user["id"]),
        statuses=[s.value for s in Status],
        queues=ALL_QUEUES,
        f_status=status,
        f_queue=queue,
        mine=mine,
    )


@app.get("/tickets/new", response_class=HTMLResponse, include_in_schema=False)
def new_ticket_form(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> HTMLResponse:
    return render(
        request,
        conn,
        "new.html",
        levels=[lv.value for lv in Level],
        categories=[c.value for c in Category],
        matrix=PRIORITY_MATRIX,
        Level=Level,
    )


@app.post("/tickets", include_in_schema=False)
def create_ticket_form(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    impact: str = Form("medium"),
    urgency: str = Form("medium"),
    category: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
) -> RedirectResponse:
    user = current_user(request, conn)
    try:
        data = TicketCreate.model_validate(
            {
                "title": title,
                "description": description,
                "requester_id": user["id"],
                "impact": impact,
                "urgency": urgency,
                "category": category or None,
            }
        )
    except ValidationError as e:
        first = e.errors()[0]
        return back("/tickets/new", f"{first['loc'][-1]}: {first['msg']}")
    tid = svc.create_ticket(conn, data, ROUTER)
    return back(f"/tickets/{tid}")


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse, include_in_schema=False)
def ticket_page(
    request: Request, ticket_id: int, conn: sqlite3.Connection = Depends(get_conn)
) -> Response:
    user = current_user(request, conn)
    try:
        detail = svc.ticket_detail(conn, ticket_id, viewer=user)
    except svc.TicketError as e:
        return back("/", e)
    return render(
        request,
        conn,
        "detail.html",
        **detail,
        next_statuses=[s.value for s in svc.next_statuses(detail["ticket"], user)],
        agents=svc.list_users(conn, roles={"agent", "admin"}),
    )


@app.post("/tickets/{ticket_id}/comment", include_in_schema=False)
def comment_form(
    request: Request,
    ticket_id: int,
    body: str = Form(""),
    internal: bool = Form(False),
    conn: sqlite3.Connection = Depends(get_conn),
) -> RedirectResponse:
    user = current_user(request, conn)
    try:
        svc.add_comment(
            conn, ticket_id, CommentCreate(author_id=user["id"], body=body, internal=internal)
        )
    except (svc.TicketError, ValidationError) as e:
        return back(
            f"/tickets/{ticket_id}",
            e if isinstance(e, svc.TicketError) else "comment cannot be empty",
        )
    return back(f"/tickets/{ticket_id}")


@app.post("/tickets/{ticket_id}/transition", include_in_schema=False)
def transition_form(
    request: Request,
    ticket_id: int,
    to_status: str = Form(...),
    note: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
) -> RedirectResponse:
    user = current_user(request, conn)
    try:
        svc.transition(conn, ticket_id, user["id"], Status(to_status), note)
    except (svc.TicketError, ValueError) as e:
        return back(f"/tickets/{ticket_id}", e)
    return back(f"/tickets/{ticket_id}")


@app.post("/tickets/{ticket_id}/assign", include_in_schema=False)
def assign_form(
    request: Request,
    ticket_id: int,
    assignee_id: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
) -> RedirectResponse:
    user = current_user(request, conn)
    try:
        svc.assign(conn, ticket_id, user["id"], int(assignee_id) if assignee_id else None)
    except svc.TicketError as e:
        return back(f"/tickets/{ticket_id}", e)
    return back(f"/tickets/{ticket_id}")

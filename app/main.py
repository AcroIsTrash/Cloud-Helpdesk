"""FastAPI app: a JSON API under /api and a server-rendered UI.

Run:  uv run python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from . import services as svc
from .auth import DevLoginPicker, IdentitySource, NoLogin
from .config import Settings
from .db import make_engine
from .models import (
    ALL_QUEUES,
    PRIORITY_MATRIX,
    AssignRequest,
    Category,
    CommentCreate,
    CommentRequest,
    Level,
    Status,
    TicketCreate,
    TicketRequest,
    TransitionRequest,
)
from .routing import KeywordRouter

log = logging.getLogger(__name__)

# Swap this for an AI router later; nothing else needs to change.
ROUTER = KeywordRouter()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_env()  # raises, so the app won't start, on unsafe config
    identity: IdentitySource = (
        DevLoginPicker(settings.session_secret) if settings.dev_login else NoLogin()
    )
    engine = make_engine()
    try:
        with engine.connect() as conn:
            # The schema belongs to Alembic (ADR-0008); the app never creates tables.
            if not inspect(conn).has_table("tickets"):
                raise RuntimeError(
                    "database has no schema; run `uv run alembic upgrade head` first"
                )
        app.state.engine = engine
        app.state.identity = identity
        yield
    finally:
        engine.dispose()


app = FastAPI(title="Help Desk", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_conn(request: Request) -> Iterator[Connection]:
    # Closing the connection rolls back anything a failed request left uncommitted.
    with request.app.state.engine.connect() as conn:
        yield conn


class NotLoggedIn(Exception):
    """No identity source recognised the request, or the person no longer exists."""


def current_user(request: Request, conn: Connection = Depends(get_conn)) -> svc.Row:
    """The person making this request. Every route but /healthz and the login page needs it."""
    user_id = request.app.state.identity.identify(request)
    if user_id is None:
        raise NotLoggedIn
    try:
        return svc.get_user(conn, user_id)
    except svc.NotFound:
        raise NotLoggedIn from None


@app.exception_handler(NotLoggedIn)
async def not_logged_in_handler(request: Request, _: NotLoggedIn) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "not logged in"}, status_code=401)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?{urlencode({'next': target})}", status_code=303)


# ---- template filters ------------------------------------------------------


def _ago(value: datetime | None) -> str:
    if not value:
        return ""
    s = int((svc.utcnow() - value).total_seconds())
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


# ---- health --------------------------------------------------------------------


@app.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> JSONResponse:
    """For the load balancer: can this task reach the database? No login needed."""
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.warning("health check failed: database unreachable", exc_info=True)
        return JSONResponse({"status": "database unreachable"}, status_code=503)
    return JSONResponse({"status": "ok"})


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
def api_users(
    _: svc.Row = Depends(current_user), conn: Connection = Depends(get_conn)
) -> list[svc.Row]:
    return svc.list_users(conn)


@app.get("/api/tickets", tags=["api"], response_model=None)
def api_list(
    status: Status | None = None,
    include_closed: bool = False,
    queue: str | None = None,
    assignee_id: int | None = None,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> list[svc.Row]:
    rows = svc.list_tickets(
        conn,
        status=status,
        include_closed=include_closed,
        queue=queue,
        assignee_id=assignee_id,
        viewer=user,
    )
    return [r | {"sla": svc.sla_status(r)} for r in rows]


@app.post("/api/tickets", status_code=201, tags=["api"], response_model=None)
def api_create(
    body: TicketRequest,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> svc.Row:
    data = TicketCreate(**body.model_dump(), requester_id=user["id"])
    tid = svc.create_ticket(conn, data, ROUTER)
    return svc.ticket_detail(conn, tid, viewer=user)


@app.get("/api/tickets/{ticket_id}", tags=["api"], response_model=None)
def api_detail(
    ticket_id: int, user: svc.Row = Depends(current_user), conn: Connection = Depends(get_conn)
) -> svc.Row:
    return svc.ticket_detail(conn, ticket_id, viewer=user)


@app.post("/api/tickets/{ticket_id}/transition", tags=["api"], response_model=None)
def api_transition(
    ticket_id: int,
    body: TransitionRequest,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> svc.Row:
    svc.transition(conn, ticket_id, user["id"], body.to_status, body.note)
    return svc.ticket_detail(conn, ticket_id, viewer=user)


@app.post("/api/tickets/{ticket_id}/assign", tags=["api"], response_model=None)
def api_assign(
    ticket_id: int,
    body: AssignRequest,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> svc.Row:
    svc.assign(conn, ticket_id, user["id"], body.assignee_id)
    return svc.ticket_detail(conn, ticket_id, viewer=user)


@app.post("/api/tickets/{ticket_id}/comments", status_code=201, tags=["api"], response_model=None)
def api_comment(
    ticket_id: int,
    body: CommentRequest,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> svc.Row:
    svc.add_comment(conn, ticket_id, CommentCreate(**body.model_dump(), author_id=user["id"]))
    return svc.ticket_detail(conn, ticket_id, viewer=user)


# ---- HTML UI -----------------------------------------------------------------


def render(
    request: Request, name: str, user: svc.Row | None, status_code: int = 200, **ctx: Any
) -> HTMLResponse:
    ctx.update(
        current_user=user,
        is_agent=user is not None and svc.is_agent(user),
        error=request.query_params.get("error"),
    )
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def back(path: str, error: Exception | str | None = None) -> RedirectResponse:
    url = f"{path}?{urlencode({'error': str(error)})}" if error else path
    return RedirectResponse(url, status_code=303)


def _same_site(path: str) -> str:
    # Only same-site paths: "//host" and "/\host" are protocol-relative URLs.
    return path if path.startswith("/") and path[1:2] not in {"/", "\\"} else "/"


# ---- dev login picker ----------------------------------------------------------
# The picker's own pages. With another identity source they have nothing to do.


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(
    request: Request, next: str = "/", conn: Connection = Depends(get_conn)
) -> HTMLResponse:
    picker = isinstance(request.app.state.identity, DevLoginPicker)
    return render(
        request,
        "login.html",
        None,
        people=svc.list_users(conn) if picker else [],
        picker=picker,
        next=_same_site(next),
    )


@app.post("/login", include_in_schema=False)
def login(
    request: Request,
    user_id: int = Form(...),
    next: str = Form("/"),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    picker = request.app.state.identity
    if not isinstance(picker, DevLoginPicker):
        raise HTTPException(404)
    try:
        svc.get_user(conn, user_id)
    except svc.NotFound as e:
        return back("/login", e)
    resp = RedirectResponse(_same_site(next), status_code=303)
    picker.log_in(resp, user_id)
    return resp


@app.post("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    if isinstance(picker := request.app.state.identity, DevLoginPicker):
        picker.log_out(resp)
    return resp


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(
    request: Request,
    status: str = "",
    queue: str = "",
    mine: bool = False,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> HTMLResponse:
    agent = svc.is_agent(user)
    status_enum = Status(status) if status in {s.value for s in Status} else None
    rows = svc.list_tickets(
        conn,
        status=status_enum,
        include_closed=(status == "all"),
        queue=queue or None,
        assignee_id=user["id"] if (mine and agent) else None,
        viewer=user,
    )
    for r in rows:
        r["sla"] = svc.sla_status(r)
    return render(
        request,
        "list.html",
        user,
        tickets=rows,
        counts=svc.status_counts(conn, viewer=user),
        statuses=[s.value for s in Status],
        queues=ALL_QUEUES,
        f_status=status,
        f_queue=queue,
        mine=mine,
    )


@app.get("/tickets/new", response_class=HTMLResponse, include_in_schema=False)
def new_ticket_form(request: Request, user: svc.Row = Depends(current_user)) -> HTMLResponse:
    return render(
        request,
        "new.html",
        user,
        levels=[lv.value for lv in Level],
        categories=[c.value for c in Category],
        matrix=PRIORITY_MATRIX,
        Level=Level,
    )


@app.post("/tickets", include_in_schema=False)
def create_ticket_form(
    title: str = Form(""),
    description: str = Form(""),
    impact: str = Form("medium"),
    urgency: str = Form("medium"),
    category: str = Form(""),
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
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
    request: Request,
    ticket_id: int,
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> Response:
    try:
        detail = svc.ticket_detail(conn, ticket_id, viewer=user)
    except svc.PermissionDenied as e:
        # Refused outright, even for a guessed ticket number.
        return render(request, "error.html", user, status_code=403, message=str(e))
    except svc.TicketError as e:
        return back("/", e)
    return render(
        request,
        "detail.html",
        user,
        **detail,
        next_statuses=[s.value for s in svc.next_statuses(detail["ticket"], user)],
        agents=svc.list_users(conn, roles={"agent", "admin"}),
    )


@app.post("/tickets/{ticket_id}/comment", include_in_schema=False)
def comment_form(
    ticket_id: int,
    body: str = Form(""),
    internal: bool = Form(False),
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
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
    ticket_id: int,
    to_status: str = Form(...),
    note: str = Form(""),
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        svc.transition(conn, ticket_id, user["id"], Status(to_status), note)
    except (svc.TicketError, ValueError) as e:
        return back(f"/tickets/{ticket_id}", e)
    return back(f"/tickets/{ticket_id}")


@app.post("/tickets/{ticket_id}/assign", include_in_schema=False)
def assign_form(
    ticket_id: int,
    assignee_id: str = Form(""),
    user: svc.Row = Depends(current_user),
    conn: Connection = Depends(get_conn),
) -> RedirectResponse:
    try:
        svc.assign(conn, ticket_id, user["id"], int(assignee_id) if assignee_id else None)
    except svc.TicketError as e:
        return back(f"/tickets/{ticket_id}", e)
    return back(f"/tickets/{ticket_id}")

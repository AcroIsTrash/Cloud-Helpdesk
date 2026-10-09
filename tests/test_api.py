"""HTTP tests: only what the web layer adds. Rules are tested in test_services.py.

Every test logs in through the dev login picker, the way a person would.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import services as svc
from app.routing import KeywordRouter
from app.seed import seed


@pytest.fixture
def client(engine, empty_db, monkeypatch) -> Iterator[TestClient]:
    with engine.connect() as c:
        seed(c, KeywordRouter(), demo=True)
    monkeypatch.setenv("DATABASE_URL", empty_db)
    monkeypatch.setenv("HELPDESK_DEV_LOGIN", "1")
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def ids(client, engine) -> dict[str, int]:
    with engine.connect() as c:
        return {u["email"].split("@")[0]: u["id"] for u in svc.list_users(c)}


def login(client: TestClient, user_id: int) -> None:
    r = client.post("/login", data={"user_id": user_id}, follow_redirects=False)
    assert r.status_code == 303


def test_not_logged_in_pages_redirect_to_login_and_api_says_401(client):
    for path in ("/", "/tickets/new", "/tickets/1"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"].startswith("/login"), path
    for path in ("/api/tickets", "/api/tickets/1", "/api/users"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/tickets", json={}).status_code == 401
    assert client.get("/login").status_code == 200


def test_requester_cannot_open_someone_elses_ticket(client, ids):
    login(client, ids["alice"])
    mine = client.post(
        "/api/tickets",
        json={"title": "Monitor is dead", "description": "No power light at all."},
    ).json()["ticket"]
    login(client, ids["bob"])
    assert client.get(f"/api/tickets/{mine['id']}").status_code == 403
    assert client.get(f"/tickets/{mine['id']}").status_code == 403
    assert mine["id"] not in [t["id"] for t in client.get("/api/tickets").json()]


@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/api/tickets",
            {
                "title": "Printer jammed",
                "description": "Paper jam, won't clear.",
                "requester_id": 1,
            },
        ),
        ("/api/tickets/1/transition", {"to_status": "open", "actor_id": 1}),
        ("/api/tickets/1/assign", {"assignee_id": None, "actor_id": 1}),
        ("/api/tickets/1/comments", {"body": "hello", "author_id": 1}),
    ],
)
def test_bodies_naming_who_is_acting_are_rejected(client, ids, path, body):
    login(client, ids["dana"])
    r = client.post(path, json=body)
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "extra_forbidden"


def test_every_page_renders_for_each_role(client, ids):
    for name in ("alice", "dana", "morgan"):  # Requester, Agent, Admin
        login(client, ids[name])
        for path in ("/", "/?status=all", "/?mine=true", "/tickets/new", "/tickets/1"):
            assert client.get(path).status_code == 200, (name, path)


def test_api_status_codes(client, ids):
    login(client, ids["bob"])
    r = client.post(
        "/api/tickets",
        json={
            "title": "Printer jammed on floor 3",
            "description": "Paper jam, won't clear.",
            "impact": "low",
            "urgency": "high",
        },
    )
    assert r.status_code == 201
    t = r.json()["ticket"]
    assert (t["priority"], t["queue"], t["requester_id"]) == ("P3", "Desktop Support", ids["bob"])

    denied = client.post(f"/api/tickets/{t['id']}/assign", json={"assignee_id": ids["priya"]})
    assert denied.status_code == 403

    login(client, ids["priya"])
    bad = client.post(
        f"/api/tickets/{t['id']}/transition", json={"to_status": "resolved", "note": "x"}
    )
    assert bad.status_code == 400
    assert client.get("/api/tickets/9999").status_code == 404
    assert len(client.get("/api/tickets").json()) >= 6  # demo data + ours


def test_html_forms_act_as_the_logged_in_person(client, ids):
    login(client, ids["alice"])
    r = client.post(
        "/tickets",
        data={
            "title": "Monitor is dead",
            "description": "No power light at all.",
            "impact": "low",
            "urgency": "medium",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/tickets/")
    ticket = client.get("/api/tickets/" + r.headers["location"].rsplit("/", 1)[1]).json()
    assert ticket["ticket"]["requester_id"] == ids["alice"]


def test_a_tampered_session_cookie_is_not_logged_in(client, ids):
    login(client, ids["alice"])
    signature = client.cookies["session"].split(".", 1)[1]
    client.cookies.set("session", f"{ids['morgan']}.{signature}")
    assert client.get("/api/tickets").status_code == 401


def test_logging_out_ends_the_session(client, ids):
    login(client, ids["alice"])
    assert client.post("/logout", follow_redirects=False).status_code == 303
    assert client.get("/api/tickets").status_code == 401


def test_login_redirect_stays_on_this_site(client, ids):
    for target in ("//evil.example", "/\\evil.example", "https://evil.example", "/tickets/1"):
        r = client.post(
            "/login", data={"user_id": ids["dana"], "next": target}, follow_redirects=False
        )
        expected = target if target == "/tickets/1" else "/"
        assert r.headers["location"] == expected, target


def test_healthz_needs_no_login_and_reports_the_database(client):
    from app.db import make_engine

    assert client.get("/healthz").status_code == 200

    # The database goes away: nothing listens on port 1.
    up = client.app.state.engine
    client.app.state.engine = make_engine("postgresql+psycopg://helpdesk@127.0.0.1:1/helpdesk")
    try:
        assert client.get("/healthz").status_code == 503
    finally:
        client.app.state.engine.dispose()
        client.app.state.engine = up


def test_a_deactivated_person_is_turned_away_on_their_next_request(client, ids):
    login(client, ids["sam"])
    assert client.get("/api/tickets").status_code == 200

    session = client.cookies["session"]
    login(client, ids["morgan"])
    r = client.post(f"/api/users/{ids['sam']}/deactivate", json={"reason": "Left the company"})
    assert r.status_code == 200 and r.json()["active"] is False

    client.cookies.set("session", session)  # Sam's session is still validly signed
    assert client.get("/api/tickets").status_code == 403
    assert client.get("/", follow_redirects=False).status_code == 403


def test_deactivated_people_are_left_out_of_the_picker_and_assignee_choices(client, ids):
    login(client, ids["morgan"])
    client.post(f"/api/users/{ids['sam']}/deactivate", json={"reason": "On leave"})

    assert "Sam Patel" not in client.get("/login").text
    assert "Sam Patel" not in client.get("/tickets/2").text  # one he never touched
    r = client.post("/login", data={"user_id": ids["sam"]}, follow_redirects=False)
    assert r.headers["location"].startswith("/login?error=")

    assert client.post(f"/api/users/{ids['sam']}/reactivate").json()["active"] is True
    assert "Sam Patel" in client.get("/login").text


def test_an_admin_manages_access_from_the_people_page(client, ids):
    login(client, ids["dana"])
    assert client.get("/people").status_code == 403
    assert client.post(f"/api/users/{ids['sam']}/reactivate").status_code == 403

    login(client, ids["morgan"])
    assert "Sam Patel" in client.get("/people").text
    r = client.post(
        f"/people/{ids['sam']}/deactivate",
        data={"reason": "Contract ended"},
        follow_redirects=False,
    )
    assert (r.status_code, r.headers["location"]) == (303, "/people")
    sam = next(u for u in client.get("/api/users").json() if u["id"] == ids["sam"])
    assert sam["active"] is False

    r = client.post(f"/people/{ids['sam']}/deactivate", data={"reason": "again"})
    assert "already deactivated" in r.text
    client.post(f"/people/{ids['sam']}/reactivate")
    sam = next(u for u in client.get("/api/users").json() if u["id"] == ids["sam"])
    assert sam["active"] is True

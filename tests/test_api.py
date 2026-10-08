from fastapi.testclient import TestClient


def test_api_and_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("HELPDESK_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("HELPDESK_DEMO", "1")
    from app.main import app

    with TestClient(app) as client:
        users = {u["email"].split("@")[0]: u["id"] for u in client.get("/api/users").json()}

        r = client.post(
            "/api/tickets",
            json={
                "title": "Printer jammed on floor 3",
                "description": "Paper jam, won't clear.",
                "requester_id": users["bob"],
                "impact": "low",
                "urgency": "high",
            },
        )
        assert r.status_code == 201
        t = r.json()["ticket"]
        assert (t["priority"], t["queue"]) == ("P3", "Desktop Support")

        bad = client.post(
            f"/api/tickets/{t['id']}/transition",
            json={"actor_id": users["priya"], "to_status": "resolved", "note": "x"},
        )
        assert bad.status_code == 400

        denied = client.post(
            f"/api/tickets/{t['id']}/assign",
            json={"actor_id": users["bob"], "assignee_id": users["priya"]},
        )
        assert denied.status_code == 403

        assert client.get("/api/tickets/9999").status_code == 404
        assert len(client.get("/api/tickets").json()) >= 5  # demo data + ours

        # every page renders, for both an agent and a requester
        for uid in (users["dana"], users["alice"]):
            client.cookies.set("acting_as", str(uid))
            for path in ("/", "/?status=all", "/tickets/new", "/tickets/1"):
                assert client.get(path).status_code == 200, path

        client.cookies.set("acting_as", str(users["alice"]))
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

        # the "acting as" switcher must not be usable as an open redirect
        for target in ("//evil.example", "/\\evil.example", "https://evil.example", "/tickets/1"):
            r = client.post(
                "/act-as", data={"user_id": users["dana"], "next": target}, follow_redirects=False
            )
            expected = target if target == "/tickets/1" else "/"
            assert r.headers["location"] == expected, target

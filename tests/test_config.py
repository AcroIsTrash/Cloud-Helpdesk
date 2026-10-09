import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError


def test_app_refuses_to_start_with_the_dev_login_picker_in_aws(empty_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", empty_db)
    monkeypatch.setenv("HELPDESK_ENV", "aws")
    monkeypatch.setenv("HELPDESK_DEV_LOGIN", "1")
    from app.main import app

    with pytest.raises(ConfigError, match="dev login picker"), TestClient(app):
        pass


def test_without_the_picker_nobody_can_log_in(empty_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", empty_db)
    monkeypatch.delenv("HELPDESK_DEV_LOGIN", raising=False)
    from app.main import app

    with TestClient(app) as client:
        assert client.post("/login", data={"user_id": 1}).status_code == 404
        assert "HELPDESK_DEV_LOGIN" in client.get("/login").text

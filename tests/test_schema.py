import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import make_engine


@pytest.fixture
def unmigrated_db(engine, database_url):
    """A brand-new, empty database next to the migrated test database."""
    name = "helpdesk_unmigrated"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        c.execute(text(f"CREATE DATABASE {name}"))
    yield engine.url.set(database=name).render_as_string(hide_password=False)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))


def test_startup_on_empty_database_does_not_create_tables(unmigrated_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", unmigrated_db)
    from app.main import app

    with pytest.raises(RuntimeError, match="alembic upgrade head"), TestClient(app):
        pass

    probe = make_engine(unmigrated_db)
    assert inspect(probe).get_table_names() == []
    probe.dispose()

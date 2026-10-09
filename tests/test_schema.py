import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

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


def test_deactivation_migration_upgrades_a_database_with_data(unmigrated_db):
    """People who exist before the upgrade stay active; their ticket Events still fit."""
    from tests.conftest import migrate

    eng = make_engine(unmigrated_db)
    migrate(eng, "297bc5ea20dc")  # the schema before Deactivation
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO users (id, name, email, role) VALUES (1, 'Ann', 'ann@x', 'agent');"
                "INSERT INTO tickets (id, title, description, requester_id, queue, category,"
                " impact, urgency, priority, status, created_at, updated_at)"
                " VALUES (1, 't', 'd', 1, 'q', 'other', 'low', 'low', 'P4', 'new', now(), now());"
                "INSERT INTO events (ticket_id, actor_id, kind, detail, created_at)"
                " VALUES (1, 1, 'created', 'x', now())"
            )
        )

    migrate(eng)

    with eng.connect() as c:
        assert c.execute(text("SELECT active FROM users")).scalar_one() is True
        assert c.execute(text("SELECT count(*) FROM events")).scalar_one() == 1
        # An Event must be about a ticket, a person, or both.
        with pytest.raises(IntegrityError):
            c.execute(
                text("INSERT INTO events (kind, detail, created_at) VALUES ('x', 'y', now())")
            )
    eng.dispose()

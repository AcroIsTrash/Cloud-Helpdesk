"""Tests run against a real Postgres, migrated with the real Alembic migrations.

One container per test session (testcontainers). Each test starts from empty
tables: they are truncated before every test, which isolates the API test too,
whose requests commit on connections of their own.

Set TEST_DATABASE_URL to use an existing, disposable database instead of a
container (it must be empty or already migrated by these tests).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text

from app.db import make_engine, metadata, seed_users

POSTGRES_IMAGE = "pgvector/pgvector:pg16"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as pg:
        yield pg.get_connection_url()


def migrate(engine: Engine, revision: str = "head") -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["configure_logger"] = False
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    eng = make_engine(database_url)
    migrate(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def empty_db(engine: Engine, database_url: str) -> str:
    """Migrated database with no rows; returns its URL."""
    names = ", ".join(t.name for t in metadata.sorted_tables)
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    return database_url


@pytest.fixture
def conn(engine: Engine, empty_db: str) -> Iterator[Connection]:
    with engine.connect() as c:
        seed_users(c)
        yield c

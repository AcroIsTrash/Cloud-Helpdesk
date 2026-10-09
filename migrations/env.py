"""Alembic environment: migrations target the tables declared in app/db.py."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from app.db import make_engine, metadata

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = metadata


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=True,  # so the CI drift check catches default changes too
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline (--sql) mode is not supported; run against a database")

# Tests hand in an open connection; everything else connects via DATABASE_URL.
given: Connection | None = config.attributes.get("connection")
if given is not None:
    run_migrations(given)
else:
    with make_engine().connect() as conn:
        run_migrations(conn)

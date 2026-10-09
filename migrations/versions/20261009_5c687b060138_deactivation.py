"""Deactivation: users.active, and Events about a person's access.

An Event can now be about a ticket, a person, or both, so `ticket_id` becomes
optional, `user_id` names the person, and a check keeps at least one of them.
Existing people stay active; existing Events all name a ticket, so they pass.

Revision ID: 5c687b060138
Revises: 297bc5ea20dc
Create Date: 2026-10-09 07:27:37.457276

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c687b060138"
down_revision: str | Sequence[str] | None = "297bc5ea20dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("active", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("events", sa.Column("user_id", sa.Integer(), nullable=True))
    op.alter_column("events", "ticket_id", existing_type=sa.INTEGER(), nullable=True)
    op.create_index(op.f("ix_events_user_id"), "events", ["user_id"], unique=False)
    op.create_foreign_key(op.f("fk_events_user_id_users"), "events", "users", ["user_id"], ["id"])
    op.create_check_constraint(
        op.f("ck_events_subject"), "events", "ticket_id IS NOT NULL OR user_id IS NOT NULL"
    )


def downgrade() -> None:
    # Events about access alone have no ticket to keep them; they go with the column.
    op.execute("DELETE FROM events WHERE ticket_id IS NULL")
    op.drop_constraint(op.f("ck_events_subject"), "events", type_="check")
    op.drop_constraint(op.f("fk_events_user_id_users"), "events", type_="foreignkey")
    op.drop_index(op.f("ix_events_user_id"), table_name="events")
    op.alter_column("events", "ticket_id", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("events", "user_id")
    op.drop_column("users", "active")

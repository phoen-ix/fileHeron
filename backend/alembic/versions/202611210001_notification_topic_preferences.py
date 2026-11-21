"""per-alert-type notification preferences.

`user_notification_topic_preferences`: a finer choice inside one notification
category, keyed (user, category, topic). Only `ops_alert` has topics - its
`reason` - so an admin can choose, per alert type, whether it is mailed, shown
in the bell, both or neither.

Additive: no existing row changes. An admin's stored `ops_alert` choice in
`user_notification_preferences` keeps applying to every alert type until they
set one of them.

Downgrade drops the table; alerts fall back to the per-category choice.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202611210001
Revises: 202610110001
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_table

revision = "202611210001"
down_revision = "202610110001"
branch_labels = None
depends_on = None

_TABLE = "user_notification_topic_preferences"


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        op.create_table(
            _TABLE,
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("category", sa.String(length=40), primary_key=True),
            sa.Column("topic", sa.String(length=40), primary_key=True),
            sa.Column("channel", sa.String(length=10), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        op.drop_table(_TABLE)

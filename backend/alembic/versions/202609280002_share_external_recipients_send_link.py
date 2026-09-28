"""share_external_recipients.send_link - the sender decides whether an address is mailed.

v2.21.0 mailed every address without an account the share's public link
whenever the sender left "Notify recipient(s) by email" ticked - a choice about
ACCOUNT recipients deciding something else. The sender now chooses per share
whether these addresses get the link by email; a row with `send_link` false is
recorded (the share page shows it) and never mailed.

Existing rows default to true, which is what v2.21.0 did for them.

Revision ID: 202609280002
Revises: 202609280001
Create Date: 2026-09-28
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202609280002"
down_revision = "202609280001"
branch_labels = None
depends_on = None

_TABLE = "share_external_recipients"


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, _TABLE, "send_link"):
        op.add_column(
            _TABLE,
            sa.Column("send_link", sa.Boolean(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, _TABLE, "send_link"):
        op.drop_column(_TABLE, "send_link")

"""folder uploads - the folder a file was uploaded from.

`files.relative_dir`: the '/'-separated folder inside the share, e.g.
`Photos/2026`; NULL means the top level, which is every existing row. Additive
and inert: nothing reads it as anything but a display and archive path, and
`original_filename` stays the bare leaf it always was.

Downgrade drops the column: files uploaded with a folder then simply sit at the
top level again, which is how every release before this one shows them.

Each op is guarded SEPARATELY - see `tests/test_migration_reruns.py`.

Revision ID: 202610100001
Revises: 202610030001
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202610100001"
down_revision = "202610030001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "files", "relative_dir"):
        op.add_column("files", sa.Column("relative_dir", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "files", "relative_dir"):
        op.drop_column("files", "relative_dir")

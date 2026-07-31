"""Performance: composite index on files(uploaded_by_id, state) (v1.10.1).

Covers the per-user storage sum (admin user list + quota_reconcile + the
per-user files section), which filters on (uploaded_by_id, state). Guarded by
`_has_index` so it's re-runnable. No data change.

Revision ID: 202606051400
Revises: 202606051300
Create Date: 2026-06-05
"""
from __future__ import annotations

from alembic import op
from app.db_guards import _has_index

revision = "202606051400"
down_revision = "202606051300"
branch_labels = None
depends_on = None

_NAME = "ix_files_uploader_state"


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind, "files", _NAME):
        op.create_index(_NAME, "files", ["uploaded_by_id", "state"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "files", _NAME):
        op.drop_index(_NAME, table_name="files")

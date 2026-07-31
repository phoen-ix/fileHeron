"""files.av_unscanned - flag files served without a real AV verdict (v2.4.0).

clamd clamps MaxFileSize to INT_MAX (~2 GiB) no matter what clamd.conf says, so
`MaxFileSize 30G` really means 2147483645 bytes. Anything larger is answered
"clean" WITHOUT being read. fileHeron supports uploads far larger than that, and
the product decision is to keep serving them - but to record and surface the
scan gap instead of storing a `clean` verdict clamd never produced.

Existing rows default to 0 (not flagged). That is deliberate: this migration
cannot know which historical files were oversize at the time they were scanned,
and back-filling from size_bytes would flag files that WERE genuinely scanned
under whatever limit was configured then. The flag is forward-looking.

Revision ID: 202607300001
Revises: 202607040001
Create Date: 2026-07-30
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column, _has_index

revision = "202607300001"
down_revision = "202607040001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "files", "av_unscanned"):
        op.add_column(
            "files",
            sa.Column(
                "av_unscanned",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )
    if not _has_index(bind, "files", "ix_files_av_unscanned"):
        op.create_index("ix_files_av_unscanned", "files", ["av_unscanned"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "files", "ix_files_av_unscanned"):
        op.drop_index("ix_files_av_unscanned", table_name="files")
    if _has_column(bind, "files", "av_unscanned"):
        op.drop_column("files", "av_unscanned")

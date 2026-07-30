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

revision = "202607300001"
down_revision = "202607040001"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return rows is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _has_index(bind, table: str, index: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND index_name = :i LIMIT 1"
            ),
            {"t": table, "i": index},
        ).fetchone()
        return rows is not None
    rows = bind.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=:i AND tbl_name=:t"
        ),
        {"i": index, "t": table},
    ).fetchone()
    return rows is not None


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

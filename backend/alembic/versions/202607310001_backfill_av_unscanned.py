"""Backfill files.av_unscanned for rows clamd provably never read (v2.7.2).

The migration that added the column (202607300001) declined to backfill, and
said why:

    this migration cannot know which historical files were oversize at the time
    they were scanned, and back-filling from size_bytes would flag files that
    WERE genuinely scanned under whatever limit was configured then.

That is correct for the band between an operator's configured
`AV_MAX_SCAN_BYTES` and clamd's own ceiling: files in there really were handed
to clamd, and flagging them retroactively would be a lie in the other
direction.

It is NOT correct above clamd's ceiling. `CLAMD_MAX_FILE_SIZE` (2147483645) is
a property of the scanner, not of any past configuration - clamd clamps its own
MaxFileSize to INT_MAX whatever clamd.conf asks for, and past that point it
stops reading and answers "OK". So a row that is `clean`, unflagged, and larger
than that carries a verdict clamd produced without opening the file, on every
version this product has ever shipped. No configuration could have made it
otherwise, which is exactly what the original objection assumed it could not
know.

This matters because `.env.example` shipped `AV_MAX_SCAN_BYTES=32212254720`
(30 GiB) for four releases and `install.sh` copies it onto every fresh install,
so on those deployments the whole 2 GiB - 30 GB band was stored `clean` and
unflagged. v2.7.1 clamped the setting so it stops happening; this repairs what
already happened.

Idempotent by construction: the WHERE clause excludes rows it has already
flagged, so a re-run after a crash mid-DDL updates nothing.

Revision ID: 202607310001
Revises: 202607300001
Create Date: 2026-07-31
"""
from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op
from app.db_guards import _has_column

revision = "202607310001"
down_revision = "202607300001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.backfill_av_unscanned")

# Deliberately a literal, not an import of app.config.CLAMD_MAX_FILE_SIZE: a
# migration must keep meaning what it meant on the day it ran, even if the
# constant is later changed. clamd's INT_MAX clamp is not going to move, but
# the principle is why this is inlined.
_CLAMD_MAX_FILE_SIZE = 2147483645


def upgrade() -> None:
    bind = op.get_bind()
    # The column arrives in 202607300001; if that revision was skipped or is
    # being retried, there is nothing to backfill yet.
    if not _has_column(bind, "files", "av_unscanned"):
        return

    result = bind.execute(
        sa.text(
            "UPDATE files SET av_unscanned = 1 "
            "WHERE state = 'clean' AND av_unscanned = 0 "
            "AND size_bytes > :ceiling"
        ),
        {"ceiling": _CLAMD_MAX_FILE_SIZE},
    )
    count = result.rowcount or 0
    if count:
        # Loud on purpose: this is the operator's only notification that files
        # they were told were scanned, were not. They are still served; the API,
        # the UI badge and any future audit now say so.
        logger.warning(
            "backfill_av_unscanned: flagged %d file(s) larger than %d bytes as "
            "av_unscanned - clamd never read them, whatever the verdict said",
            count,
            _CLAMD_MAX_FILE_SIZE,
        )
    else:
        logger.info("backfill_av_unscanned: no affected rows")


def downgrade() -> None:
    # Deliberately NOT reversed. Clearing the flag would re-assert that these
    # files were scanned, which was never true - a downgrade of the schema must
    # not restore a false claim about what the antivirus did. The column itself
    # is dropped by 202607300001's downgrade if the operator goes back that far.
    pass

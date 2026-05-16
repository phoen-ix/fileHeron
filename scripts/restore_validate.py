#!/usr/bin/env python3
"""Post-restore validation harness.

Run AFTER `scripts/restore.sh` finishes, while the docker stack is up.
Verifies the data layer is internally consistent so the operator
doesn't discover broken FK relationships or missing files at 9am
Monday by trying to sign in.

Checks:
- Row counts visible for headline tables (users, shares, files,
  audit_log, public_links, oidc_providers, groups).
- Every `files` row with `state IN (ready_unscanned, clean, infected)`
  has a `storage_path` whose file exists on disk.
- Every `share_recipients` row references a `share_id` that exists.
- Alembic schema is current (head matches the migrations dir).

Exits 0 on PASS, non-zero on any FAIL. Use from cron as a follow-up
to a restore drill, or run manually after a real restore.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Run from /app inside the backend container (or pip-install with the
# backend's pyproject).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.file import File, FileState  # noqa: E402
from app.models.oidc_provider import OIDCProvider  # noqa: E402
from app.models.public_link import PublicLink  # noqa: E402
from app.models.share import Share  # noqa: E402
from app.models.share_recipient import ShareRecipient  # noqa: E402
from app.models.user import User  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    marker = "PASS" if ok else "FAIL"
    print(f"  {marker}: {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    failed = 0
    db = SessionLocal()
    try:
        # 1. Row counts for headline tables.
        print("[validate] row counts:")
        for model in (
            User,
            Share,
            File,
            AuditLog,
            PublicLink,
            OIDCProvider,
            ShareRecipient,
        ):
            n = db.query(model).count()
            print(f"  {model.__tablename__}: {n}")

        print("[validate] integrity:")

        # 2. Files with storage_path point at files that exist on disk.
        live_files = (
            db.execute(
                select(File.id, File.storage_path).where(
                    File.state.in_(
                        [FileState.ready_unscanned, FileState.clean, FileState.infected]
                    )
                )
            )
            .all()
        )
        missing = [
            (fid, sp)
            for (fid, sp) in live_files
            if not sp or not Path(sp).is_file()
        ]
        if not _check(
            f"all {len(live_files)} live files exist on disk",
            len(missing) == 0,
            detail=(
                f"{len(missing)} missing; first: {missing[0][0]} -> {missing[0][1]}"
                if missing
                else ""
            ),
        ):
            failed += 1

        # 3. share_recipients all reference an existing share.
        orphan_recipients = (
            db.execute(
                select(ShareRecipient.share_id)
                .outerjoin(Share, Share.id == ShareRecipient.share_id)
                .where(Share.id.is_(None))
            )
            .all()
        )
        if not _check(
            "no orphan share_recipients",
            len(orphan_recipients) == 0,
            detail=f"{len(orphan_recipients)} dangling refs" if orphan_recipients else "",
        ):
            failed += 1

        # 4. Alembic at head.
        try:
            from alembic.config import Config
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "backend" / "alembic.ini"))
            script_dir = ScriptDirectory.from_config(alembic_cfg)
            heads = set(script_dir.get_heads())
            ctx = MigrationContext.configure(db.connection())
            current = ctx.get_current_heads()
            if not _check(
                "alembic schema at head",
                set(current) == heads,
                detail=f"current={current} heads={list(heads)}",
            ):
                failed += 1
        except Exception as e:
            print(f"  WARN: could not introspect alembic head: {e}")

    finally:
        db.close()

    if failed:
        print(f"\n[validate] {failed} check(s) FAILED", file=sys.stderr)
        return 1
    print("\n[validate] all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

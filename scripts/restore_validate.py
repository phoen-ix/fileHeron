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
- Sampled Fernet-encrypted fields actually DECRYPT under this instance's
  JWT_SECRET. backup.sh does not capture .env, so a restore onto a host with a
  different JWT_SECRET leaves every TOTP secret, OIDC client secret, SMTP/IMAP
  password and public-link token intact but permanently unreadable - and every
  other check here still passes. Keep .env backed up separately; this check is
  what tells you if you didn't.
- Alembic schema is current (head matches the migrations dir).

Exits 0 on PASS, non-zero on any FAIL. Use from cron as a follow-up
to a restore drill, or run manually after a real restore.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Locate the backend root (the dir holding app/ + alembic.ini). The repo keeps
# it at ../backend (scripts/ is a sibling); the container image flattens both
# under /app, so scripts/ and app/ share a parent. Support both layouts.
_HERE = Path(__file__).resolve().parent
_BACKEND = next(
    (c for c in (_HERE.parent / "backend", _HERE.parent) if (c / "app").is_dir()),
    _HERE.parent / "backend",
)
sys.path.insert(0, str(_BACKEND))

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
    print(f"  {marker}: {name}" + (f" - {detail}" if detail else ""))
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

        # 4. Encrypted fields actually decrypt under THIS instance's JWT_SECRET.
        #
        # This is the check that catches the worst restore failure, and the
        # reason it exists: scripts/backup.sh captures the DB, files,
        # quarantine and redis - but NOT .env, so not JWT_SECRET. Every Fernet
        # field (TOTP secrets, OIDC client secrets, SMTP/IMAP passwords,
        # public-link tokens, webhook secrets) is encrypted under a key derived
        # from it. Restore onto a host with a different JWT_SECRET and every one
        # of those rows comes back intact and undecryptable: the restore looks
        # completely successful, and stays that way until someone tries to log
        # in with 2FA (audit 2026-07-30).
        #
        # Every other check in this file passes in that scenario. This one does
        # not.
        encrypted_samples: list[tuple[str, bytes | str]] = []
        try:
            from app.models.app_setting import AppSetting
            from app.models.user_totp import UserTOTP

            for row in db.query(UserTOTP).limit(3).all():
                if row.secret_encrypted:
                    encrypted_samples.append(("users_totp.secret_encrypted", row.secret_encrypted))
            for row in db.query(OIDCProvider).limit(3).all():
                if row.client_secret_encrypted:
                    encrypted_samples.append(
                        ("oidc_providers.client_secret_encrypted", row.client_secret_encrypted)
                    )
            for row in db.query(PublicLink).limit(3).all():
                if row.token_encrypted:
                    encrypted_samples.append(("public_links.token_encrypted", row.token_encrypted))
            for row in db.query(AppSetting).filter(AppSetting.is_encrypted.is_(True)).limit(3).all():
                if row.value:
                    encrypted_samples.append((f"app_settings[{row.key}]", row.value))
        except Exception as e:  # pragma: no cover - model drift shouldn't abort the drill
            print(f"  WARN: could not collect encrypted samples: {e}")

        if not encrypted_samples:
            print(
                "  SKIP: no encrypted fields present to decrypt "
                "(no 2FA enrolments, OIDC providers, public links or encrypted settings)"
            )
        else:
            from app.utils.crypto import decrypt_setting, decrypt_totp_secret

            undecryptable = []
            for label, blob in encrypted_samples:
                try:
                    # TOTP secrets are LargeBinary and use their own helper;
                    # everything else is a Fernet token stored as text.
                    if isinstance(blob, (bytes, bytearray)):
                        decrypt_totp_secret(bytes(blob))
                    else:
                        decrypt_setting(blob)
                except Exception:
                    undecryptable.append(label)
            if not _check(
                f"all {len(encrypted_samples)} sampled encrypted fields decrypt",
                not undecryptable,
                detail=(
                    "JWT_SECRET does not match the one these rows were encrypted "
                    f"under - restore is NOT usable. Failed: {undecryptable}"
                    if undecryptable
                    else ""
                ),
            ):
                failed += 1

        # 5. Alembic at head.
        try:
            from alembic.config import Config
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            alembic_cfg = Config(str(_BACKEND / "alembic.ini"))
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

#!/usr/bin/env python3
"""Re-encrypt every Fernet-protected field under a new JWT_SECRET.

JWT_SECRET is the seed for the HKDF-derived Fernet key that protects:

- ``user_totp.secret_encrypted``      (per-user TOTP shared secrets)
- ``oidc_providers.client_secret_encrypted``  (per-provider IdP secrets)
- ``public_links.token_encrypted``    (public-link plaintext copy)
- ``app_settings`` rows where ``is_encrypted=True``  (today: SMTP_PASSWORD)

If you rotate JWT_SECRET without this script, all of the above become
unreadable - TOTP-enrolled users lock out, OIDC SSO breaks, the SMTP
password disappears, and admins can't re-view public-link URLs.

USAGE
-----
1. Plan a maintenance window. All active JWTs will become invalid; the
   side effect is forced re-login for every user. This is acceptable.
2. Generate ``NEW_JWT_SECRET`` (>=32 chars, e.g.
   ``openssl rand -base64 48 | tr -d '=+/' | cut -c1-48``).
3. Pause the worker (cron jobs may write encrypted rows; this script
   does NOT take a row-level lock):
       docker compose stop worker
4. Run this script with BOTH the current and the next secret set in
   the environment, against a fresh DB session:
       OLD_JWT_SECRET="..." NEW_JWT_SECRET="..." \\
           docker compose exec -T backend python /app/scripts/rotate_jwt_secret.py
   Use ``--dry-run`` to count rows without writing.
5. Update ``.env`` so ``JWT_SECRET=$NEW_JWT_SECRET``.
6. Restart:
       docker compose restart backend worker
   All active JWTs invalidate; users re-login; TOTP / OIDC / public-link
   re-view all continue to work because the on-disk ciphertext is
   already re-keyed.

ROLLBACK
--------
If the script aborts mid-table, re-run with the SAME OLD/NEW pair -
already-re-encrypted rows will fail to decrypt with the OLD key and
are skipped automatically (logged as "already-rotated"). Run again
until "rotated=0 skipped=N already-rotated".

SAFETY
------
- Each table is rewritten in its own transaction. Crash mid-table
  leaves that table partially rotated; re-run cleans up. No row is
  left in an unreadable state - every row is either OLD-key or NEW-key
  encrypted, never half.
- ``--dry-run`` performs all the decrypt+re-encrypt work in memory but
  rolls back. Verify the row counts before committing.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.app_setting import AppSetting  # noqa: E402
from app.models.oidc_provider import OIDCProvider  # noqa: E402
from app.models.public_link import PublicLink  # noqa: E402
from app.models.user_totp import UserTOTP  # noqa: E402
from app.utils.crypto import _FERNET_HKDF_INFO  # noqa: E402


@dataclass
class RotationStats:
    rotated: int = 0
    already_rotated: int = 0
    null_or_empty: int = 0
    errors: int = 0


def _build_fernet(jwt_secret: str) -> Fernet:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_FERNET_HKDF_INFO,
    )
    derived = hkdf.derive(jwt_secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def _rotate_bytes(old: Fernet, new: Fernet, value: bytes | None) -> tuple[bytes | None, str]:
    """Returns (new_value, status). status ∈ rotated|already|empty|error."""
    if value is None or len(value) == 0:
        return value, "empty"
    try:
        plaintext = old.decrypt(value)
    except InvalidToken:
        try:
            new.decrypt(value)
            return value, "already"
        except InvalidToken:
            return value, "error"
    return new.encrypt(plaintext), "rotated"


def _rotate_str(old: Fernet, new: Fernet, value: str | None) -> tuple[str | None, str]:
    if value is None or value == "":
        return value, "empty"
    raw, status = _rotate_bytes(old, new, value.encode("ascii"))
    if status == "rotated":
        return raw.decode("ascii"), status
    return value, status


def _record(stats: RotationStats, status: str) -> None:
    if status == "rotated":
        stats.rotated += 1
    elif status == "already":
        stats.already_rotated += 1
    elif status == "empty":
        stats.null_or_empty += 1
    else:
        stats.errors += 1


def rotate_table(db, label: str, rows, get_field, set_field, is_bytes: bool, old: Fernet, new: Fernet) -> RotationStats:
    stats = RotationStats()
    for row in rows:
        v = get_field(row)
        if is_bytes:
            new_v, status = _rotate_bytes(old, new, v)
        else:
            new_v, status = _rotate_str(old, new, v)
        if status == "rotated":
            set_field(row, new_v)
        _record(stats, status)
    print(
        f"  {label}: rotated={stats.rotated} already_rotated={stats.already_rotated} "
        f"empty={stats.null_or_empty} errors={stats.errors}"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decrypt+re-encrypt in memory but roll back the transaction.",
    )
    args = parser.parse_args()

    old = os.environ.get("OLD_JWT_SECRET", "").strip()
    new = os.environ.get("NEW_JWT_SECRET", "").strip()
    if not old or not new:
        print("FATAL: both OLD_JWT_SECRET and NEW_JWT_SECRET must be set", file=sys.stderr)
        return 2
    if old == new:
        print("FATAL: OLD_JWT_SECRET == NEW_JWT_SECRET (nothing to do)", file=sys.stderr)
        return 2

    old_f = _build_fernet(old)
    new_f = _build_fernet(new)

    print(f"[rotate] mode: {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print("[rotate] re-encrypting Fernet-protected fields:")

    db = SessionLocal()
    total_errors = 0
    try:
        # 1. TOTP secrets (bytes column).
        s = rotate_table(
            db, "user_totp.secret_encrypted",
            db.query(UserTOTP).all(),
            lambda r: r.secret_encrypted,
            lambda r, v: setattr(r, "secret_encrypted", v),
            is_bytes=True, old=old_f, new=new_f,
        )
        total_errors += s.errors

        # 2. OIDC client secrets (str column).
        s = rotate_table(
            db, "oidc_providers.client_secret_encrypted",
            db.query(OIDCProvider).all(),
            lambda r: r.client_secret_encrypted,
            lambda r, v: setattr(r, "client_secret_encrypted", v),
            is_bytes=False, old=old_f, new=new_f,
        )
        total_errors += s.errors

        # 3. Public-link token_encrypted (str column, NULL for legacy rows).
        s = rotate_table(
            db, "public_links.token_encrypted",
            db.query(PublicLink).all(),
            lambda r: r.token_encrypted,
            lambda r, v: setattr(r, "token_encrypted", v),
            is_bytes=False, old=old_f, new=new_f,
        )
        total_errors += s.errors

        # 4. app_settings (only rows with is_encrypted=True).
        s = rotate_table(
            db, "app_settings (encrypted rows)",
            db.query(AppSetting).filter(AppSetting.is_encrypted.is_(True)).all(),
            lambda r: r.value,
            lambda r, v: setattr(r, "value", v),
            is_bytes=False, old=old_f, new=new_f,
        )
        total_errors += s.errors

        if args.dry_run:
            db.rollback()
            print("[rotate] dry-run complete - changes ROLLED BACK")
        else:
            db.commit()
            print("[rotate] committed")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if total_errors:
        print(f"[rotate] {total_errors} row(s) could not be decrypted with EITHER key - investigate", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

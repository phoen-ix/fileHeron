"""Decrypt every file stored encrypted at rest - the way back out.

Usage (from the container):
    docker compose exec backend python scripts/decrypt_files_at_rest.py [--turn-off] [--purge-now] [--dry-run]

Run it before rolling back to a release from before encryption at rest - which
cannot read encrypted files, so Rollback refuses while any exist - or to leave
encryption at rest for good.

Encryption must be OFF first, or new uploads are encrypted again behind the
script: turn it off on Admin > Security & audit > Encryption at rest, or pass
`--turn-off`. The ciphertext each file leaves behind is deleted an hour later by
the `encrypt_existing_files` task, so a download that is streaming it still
finishes. A release from before encryption at rest never deletes them, so before
such a rollback either wait for that hour or pass `--purge-now`.

Needs this instance's JWT_SECRET (the container has it) and free space for one
file at a time. Safe to re-run: it picks up where it stopped.
Exit 0 when nothing encrypted is left, 1 when something is, 2 on bad usage,
3 when the backfill is running (try again in a minute).
"""
from __future__ import annotations

# Run either way - `python scripts/<name>.py` puts scripts/ on sys.path but not
# the package root (see promote_user.py).
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import argparse  # noqa: E402
import sys  # noqa: E402
from datetime import timedelta  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.audit_log import AuditEventType  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.inbound_attachment import InboundAttachment  # noqa: E402
from app.models.storage_purge import PURGE_ENC_PLAINTEXT, StoragePurge  # noqa: E402
from app.services import encryption_lanes, file_encryption  # noqa: E402
from app.services import settings as settings_svc  # noqa: E402
from app.services.audit import record_audit_event  # noqa: E402
from app.utils.timeutil import utc_now  # noqa: E402


def _encrypted_ids(db) -> list[tuple[str, object]]:
    files = db.query(File.id).filter(File.enc_version.isnot(None), File.storage_path.isnot(None))
    atts = db.query(InboundAttachment.id).filter(
        InboundAttachment.enc_version.isnot(None), InboundAttachment.storage_key.isnot(None)
    )
    return [(file_encryption.KIND_FILE, i) for (i,) in files.order_by(File.id)] + [
        (file_encryption.KIND_ATTACHMENT, i) for (i,) in atts.order_by(InboundAttachment.id)
    ]


def _target(db, kind: str, row_id) -> file_encryption.Target | None:
    db.expire_all()
    if kind == file_encryption.KIND_FILE:
        f = db.get(File, row_id)
        if f is None or f.enc_version is None or not f.storage_path:
            return None
        return file_encryption.target_for_file(f)
    a = db.get(InboundAttachment, row_id)
    if a is None or a.enc_version is None or not a.storage_key:
        return None
    return file_encryption.target_for_attachment(a)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="decrypt_files_at_rest.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--turn-off", action="store_true", help="switch encryption at rest off first")
    parser.add_argument("--purge-now", action="store_true",
                        help="delete the replaced ciphertext at once instead of after an hour")
    parser.add_argument("--dry-run", action="store_true", help="only count what would be decrypted")
    args = parser.parse_args(argv[1:])

    db = SessionLocal()
    try:
        todo = _encrypted_ids(db)
        print(f"{len(todo)} stored object(s) are encrypted at rest.")
        if args.dry_run:
            return 0

        if file_encryption.is_enabled(db):
            if not args.turn_off:
                print("Encryption at rest is ON - new uploads would be encrypted again behind this "
                      "script. Turn it off on the admin page, or pass --turn-off.", file=sys.stderr)
                return 2
            settings_svc.set_value(db, key=settings_svc.Keys.STORAGE_ENCRYPT_AT_REST, value="false",
                                   actor=None)
            record_audit_event(db, event_type=AuditEventType.encryption_at_rest_changed,
                               target_type="settings", target_id="encryption",
                               metadata={"enabled": False, "via": "decrypt_script"})
            db.commit()
            print("Encryption at rest is now off.")

        # The backfill re-reads the switch per file, but one may be mid-file.
        lease = encryption_lanes.RedisLease(encryption_lanes._BACKFILL_LOCK)
        if not lease.acquire():
            print("The encrypt_existing_files task is running; try again in a minute.", file=sys.stderr)
            return 3
        grace = timedelta(0) if args.purge_now else file_encryption.PLAINTEXT_PURGE_GRACE
        done = failed = 0
        try:
            for kind, row_id in todo:
                target = _target(db, kind, row_id)
                if target is None:
                    continue
                try:
                    if file_encryption.rewrite_stored(db, target, encrypt=False, purge_after=grace):
                        done += 1
                except file_encryption.InsufficientSpaceError as exc:
                    db.rollback()
                    print(f"Stopped: not enough free space ({exc}).", file=sys.stderr)
                    break
                except Exception as exc:
                    db.rollback()
                    failed += 1
                    print(f"  {kind} {row_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
                if (done + failed) % 100 == 0 and done + failed:
                    print(f"  ... {done} decrypted, {failed} failed")
        finally:
            lease.release()

        if args.purge_now:
            # Every copy a swap replaced - this run's and earlier backfills'
            # plaintext ones alike. Never a live write's lease.
            db.query(StoragePurge).filter(StoragePurge.reason == PURGE_ENC_PLAINTEXT).update(
                {"not_before": utc_now()}, synchronize_session=False
            )
            db.commit()
        file_encryption.sweep_purges(db, limit=100_000)
        left = file_encryption.count_encrypted(db)
        waiting = file_encryption.status(db)["pending_purges"]
        print(f"Decrypted {done}; failed {failed}; still encrypted {left}.")
        if waiting:
            print(f"{waiting} replaced copy(ies) wait to be deleted by the encrypt_existing_files task. "
                  "A release from before encryption at rest never deletes them: wait an hour before "
                  "rolling back to one, or re-run with --purge-now.")
        return 0 if left == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

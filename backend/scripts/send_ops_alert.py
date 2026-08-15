"""Manual CLI: send one ops alert to the configured alert recipients.

Exists for systemd's `OnFailure=` hook: a failed backup or restore drill is
otherwise only a `failed` unit and a journald line, and nothing polls either.

Usage (from the host, via scripts/ops/notify_failure.sh):
    docker compose exec -T backend python scripts/send_ops_alert.py \
        --code BACKUP_FAILED --unit fileheron-backup.service < detail.txt

The body is read from stdin so journal excerpts need no shell quoting.

Recipients and SMTP both come from the app, deliberately: on a deploy that
configured mail through the admin UI the host's .env has no SMTP_HOST at all,
and smtp.password is Fernet-encrypted under a key derived from JWT_SECRET, so
there is no host-side path to sending. The cost is that this cannot report a
failure whose cause is the stack being down - that case stays a `failed` unit,
which is why the wrapper also logs to the journal before calling this.
"""
from __future__ import annotations

# Match promote_user.py: make both `python scripts/<name>.py` (the documented
# form) and `python -m scripts.<name>` work.
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import argparse  # noqa: E402
import sys  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services import error_alert as error_alert_svc  # noqa: E402
from app.utils.timeutil import utc_now  # noqa: E402

_MAX_BODY_CHARS = 4000


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="send_ops_alert")
    parser.add_argument("--code", required=True, help="short machine code, e.g. BACKUP_FAILED")
    parser.add_argument("--unit", required=True, help="the systemd unit that failed")
    parser.add_argument("--message", default="", help="one-line summary (default: derived)")
    args = parser.parse_args(argv[1:])

    detail = sys.stdin.read() if not sys.stdin.isatty() else ""
    # The server_error template renders `message` as the body text. Bound it:
    # a journal excerpt can be arbitrarily long and this ends up in email_log.
    if len(detail) > _MAX_BODY_CHARS:
        detail = detail[:_MAX_BODY_CHARS] + "\n[truncated]"

    message = args.message or f"{args.unit} failed"
    if detail.strip():
        message = f"{message}\n\n{detail.strip()}"

    # Shaped like error_alert._build_payload's output so the shared
    # `server_error` template renders it without a template of its own.
    payload = {
        "source": "ops",
        "exception_type": None,
        "message": message,
        "method": None,
        "path": None,
        "job_name": args.unit,
        "status_code": None,
        "code": args.code,
        "at": utc_now().isoformat(),
        "occurrences": 1,
    }

    db = SessionLocal()
    try:
        sent = error_alert_svc.send_to_configured_recipients(db, payload)
    finally:
        db.close()

    if not sent:
        print(
            "ops alert resolved to NO recipients - check error_alert.recipients_mode "
            "and that at least one enabled admin exists",
            file=sys.stderr,
        )
        return 1
    print(f"ops alert queued to {sent} recipient(s): {args.code} ({args.unit})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

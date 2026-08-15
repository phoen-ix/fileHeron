#!/usr/bin/env bash
# Called by systemd `OnFailure=` when a fileHeron ops unit fails.
#
# Without this, a failed backup is a `failed` unit plus a journald line and
# nothing else - and nothing polls either, which is how an instance ends up
# with no backups and no idea (2026-08-15).
#
# Mail goes out through the APP, not through host-side smtplib, because on a
# deploy that configured mail in the admin UI the host's .env has no SMTP_HOST
# and smtp.password is Fernet-encrypted under a key derived from JWT_SECRET.
# The trade-off is explicit: if the backup failed *because* the stack is down,
# this cannot send, and the failure stays visible only as a failed unit. So log
# to the journal FIRST, unconditionally, before attempting the mail.

# Deliberately not `set -e`: this runs because something already failed, and it
# must make a best effort rather than abort partway and mask the original.
set -uo pipefail

UNIT="${1:-unknown.service}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT" || exit 1

case "$UNIT" in
    *restore-drill*) CODE="RESTORE_DRILL_FAILED" ;;
    *backup*)        CODE="BACKUP_FAILED" ;;
    *)               CODE="OPS_UNIT_FAILED" ;;
esac

echo "[fileheron-ops] $UNIT failed ($CODE); attempting to alert the configured recipients" >&2

# Best-effort context. A system unit's journal is not readable by an
# unprivileged user unless it is in adm/systemd-journal, so treat this as a
# bonus rather than a requirement.
DETAIL="$(journalctl -u "$UNIT" -n 40 --no-pager 2>/dev/null)"
if [ -z "$DETAIL" ]; then
    DETAIL="(no journal excerpt available - run: journalctl -u $UNIT -n 50)"
fi

if ! printf '%s\n' "$DETAIL" | docker compose exec -T backend \
        python scripts/send_ops_alert.py --code "$CODE" --unit "$UNIT"; then
    echo "[fileheron-ops] could not send the alert for $UNIT - is the stack up?" \
         "The failure is still recorded: systemctl status $UNIT" >&2
    exit 1
fi

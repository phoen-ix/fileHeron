"""Inbound poll orchestration. Cadence/enable is owned by the cron scheduler
(services/cron_schedule.py 'imap_poll', v1.28.0); this only does the work + a
feature guard. ``run_poll(manual=True)`` powers the admin "Fetch now" button.

Sync (stdlib IMAP + DB); the worker runs it via ``asyncio.to_thread``. The IMAP
session is injectable (``session_opener``) so tests drive it with a fake.
"""
from __future__ import annotations

import contextlib
import logging

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..utils.timeutil import utc_now
from . import imap_config, inbound_mail, inbound_parse
from . import settings as settings_svc
from .imap_client import open_session

logger = logging.getLogger("fileheron.imap")

K = settings_svc.Keys


def _int_setting(db: Session, key: str) -> int:
    raw = settings_svc.get(db, key)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def run_poll(*, manual: bool, db: Session | None = None, session_opener=open_session) -> dict:
    """Fetch new mail and ingest it. ``manual`` marks the admin "Fetch now" call
    (kept for symmetry; scheduling is handled by the cron dispatcher now). Opens
    its own DB session when one isn't supplied (the worker thread case)."""
    own = db is None
    db = db or SessionLocal()
    try:
        # Feature guard only. Cadence/enable is owned by the cron scheduler
        # (services/cron_schedule.py 'imap_poll') as of v1.28.0; this no longer
        # self-gates on interval/mode.
        if not imap_config.is_enabled(db):
            return {"ok": True, "skipped": "disabled"}

        cfg = imap_config.resolve_imap_config(db)
        if not cfg.is_configured:
            return {"ok": False, "error": "not_configured"}

        action = imap_config.post_fetch_action(db)
        move_to = imap_config.move_folder(db)
        last_uid = _int_setting(db, K.IMAP_LAST_UID)
        prev_validity = _int_setting(db, K.IMAP_UIDVALIDITY)

        fetched = ingested = total = 0
        with session_opener(cfg) as sess:
            uidvalidity = sess.select(cfg.mailbox)
            total = getattr(sess, "message_count", 0)
            # Only reset on a REAL, changed UIDVALIDITY. A 0/unparseable value
            # (select() couldn't read it) must NOT trigger a full re-scan +
            # duplicate ingestion; treat it as "unchanged" and keep the highwater.
            if uidvalidity and uidvalidity != prev_validity:
                last_uid = 0  # mailbox reset -> re-evaluate from the start
            for uid in sess.search_uids_after(last_uid):
                raw = sess.fetch_raw(uid)
                if raw is None:
                    continue
                fetched += 1
                parsed = inbound_parse.parse(raw)
                msg = inbound_mail.ingest(db, parsed, uid=uid, uidvalidity=uidvalidity)
                if msg is not None:
                    ingested += 1
                db.commit()
                # Apply the server-side action only when we OWN this message: a
                # genuine new ingest, or a true re-poll of THIS (uidvalidity, uid).
                # If ingest returned None because a DIFFERENT message shares this
                # Message-ID, deleting/moving it would destroy a distinct, unread
                # mail that was never ingested.
                owns_message = msg is not None or inbound_mail.ingested_by_uid(
                    db, uidvalidity=uidvalidity, uid=uid
                )
                if owns_message:
                    try:
                        if action == "mark_read":
                            sess.mark_seen(uid)
                        elif action == "move":
                            sess.move(uid, move_to)
                        elif action == "delete":
                            sess.delete(uid)
                    except Exception:
                        logger.exception("post-fetch action %s failed for uid %s", action, uid)
                last_uid = max(last_uid, uid)

        now_iso = utc_now().isoformat()
        settings_svc.set_value(db, key=K.IMAP_LAST_UID, value=str(last_uid), actor=None)
        # Don't overwrite a known-good UIDVALIDITY with a 0/unparseable read, or
        # the next poll sees a spurious change and re-scans the whole mailbox.
        if uidvalidity:
            settings_svc.set_value(db, key=K.IMAP_UIDVALIDITY, value=str(uidvalidity), actor=None)
        settings_svc.set_value(db, key=K.IMAP_LAST_POLL_AT, value=now_iso, actor=None)
        settings_svc.set_value(db, key=K.IMAP_LAST_SUCCESS_AT, value=now_iso, actor=None)
        db.commit()
        logger.info(
            "imap poll: mailbox=%s total=%d fetched=%d ingested=%d last_uid=%d",
            cfg.mailbox, total, fetched, ingested, last_uid,
        )
        return {
            "ok": True, "fetched": fetched, "ingested": ingested,
            "last_uid": last_uid, "mailbox": cfg.mailbox, "total": total,
        }
    except Exception as exc:  # noqa: BLE001 - surface to caller/cron tracker
        with contextlib.suppress(Exception):
            settings_svc.set_value(
                db, key=K.IMAP_LAST_POLL_AT, value=utc_now().isoformat(), actor=None
            )
            db.commit()
        logger.exception("imap poll failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        if own:
            db.close()


def test_connection(db: Session, *, override: imap_config.ImapConfig | None = None) -> dict:
    """Admin diagnostic: connect, log in, list folders. Never raises."""
    cfg = override or imap_config.resolve_imap_config(db)
    if not cfg.is_configured:
        return {"ok": False, "error": "IMAP host is empty.", "hint": "Set the IMAP host first.", "folders": []}
    try:
        with open_session(cfg) as sess:
            folders = sess.list_folders()
        return {"ok": True, "error": None, "hint": None, "folders": folders}
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        text = str(exc)[:300]
        hint = None
        low = text.lower()
        if "authentication" in low or "login" in low or "auth" in low:
            hint = "Authentication failed - check the IMAP user and password."
        elif "ssl" in low or "tls" in low or "wrong version" in low:
            hint = "TLS mismatch - use 'implicit' for port 993, 'starttls' for 143."
        elif "timed out" in low or "refused" in low or "name or service" in low:
            hint = "Could not reach the server - check host, port, and firewall."
        return {"ok": False, "error": f"{name}: {text}", "hint": hint, "folders": []}

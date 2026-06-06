"""Inbound poll orchestration (v1.27.0) — mirrors release_check's gating
(enabled / auto-vs-manual / interval) and on-demand bypass.

Sync (stdlib IMAP + DB); the worker runs it via ``asyncio.to_thread``. The IMAP
session is injectable (``session_opener``) so tests drive it with a fake.
"""
from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..database import SessionLocal
from . import imap_config
from . import inbound_mail
from . import inbound_parse
from . import settings as settings_svc
from . import settings_registry as _sr
from .imap_client import open_session
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.imap")

K = settings_svc.Keys


def _too_soon(db: Session) -> bool:
    raw = settings_svc.get(db, K.IMAP_LAST_SUCCESS_AT)
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return False
    interval = _sr.effective(db, K.IMAP_POLL_INTERVAL_MINUTES)
    return (utc_now() - last) < timedelta(minutes=int(interval))


def _int_setting(db: Session, key: str) -> int:
    raw = settings_svc.get(db, key)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def run_poll(*, manual: bool, db: Session | None = None, session_opener=open_session) -> dict:
    """Fetch new mail and ingest it. ``manual=True`` bypasses the manual-mode and
    interval guards (the admin "Fetch now" button). Opens its own DB session when
    one isn't supplied (the worker thread case)."""
    own = db is None
    db = db or SessionLocal()
    try:
        if not imap_config.is_enabled(db):
            return {"ok": True, "skipped": "disabled"}
        if not manual:
            if imap_config.check_mode(db) == "manual":
                return {"ok": True, "skipped": "manual_mode"}
            if _too_soon(db):
                return {"ok": True, "skipped": "too_soon"}

        cfg = imap_config.resolve_imap_config(db)
        if not cfg.is_configured:
            return {"ok": False, "error": "not_configured"}

        action = imap_config.post_fetch_action(db)
        move_to = imap_config.move_folder(db)
        last_uid = _int_setting(db, K.IMAP_LAST_UID)
        prev_validity = _int_setting(db, K.IMAP_UIDVALIDITY)

        fetched = ingested = 0
        with session_opener(cfg) as sess:
            uidvalidity = sess.select(cfg.mailbox)
            if uidvalidity != prev_validity:
                last_uid = 0  # mailbox reset → re-evaluate from the start
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
                # Apply the server-side action only after a successful ingest+commit.
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
        settings_svc.set_value(db, key=K.IMAP_UIDVALIDITY, value=str(uidvalidity), actor=None)
        settings_svc.set_value(db, key=K.IMAP_LAST_POLL_AT, value=now_iso, actor=None)
        settings_svc.set_value(db, key=K.IMAP_LAST_SUCCESS_AT, value=now_iso, actor=None)
        db.commit()
        return {"ok": True, "fetched": fetched, "ingested": ingested, "last_uid": last_uid}
    except Exception as exc:  # noqa: BLE001 — surface to caller/cron tracker
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


def test_connection(db: Session, *, override: "imap_config.ImapConfig | None" = None) -> dict:
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
            hint = "Authentication failed — check the IMAP user and password."
        elif "ssl" in low or "tls" in low or "wrong version" in low:
            hint = "TLS mismatch — use 'implicit' for port 993, 'starttls' for 143."
        elif "timed out" in low or "refused" in low or "name or service" in low:
            hint = "Could not reach the server — check host, port, and firewall."
        return {"ok": False, "error": f"{name}: {text}", "hint": hint, "folders": []}

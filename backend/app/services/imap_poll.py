"""Inbound poll orchestration. Cadence/enable is owned by the cron scheduler
(services/cron_schedule.py 'imap_poll', v1.28.0); this only does the work + a
feature guard. ``run_poll(manual=True)`` powers the admin "Fetch now" button.

Sync (stdlib IMAP + DB); the worker runs it via ``asyncio.to_thread``. The IMAP
session is injectable (``session_opener``) so tests drive it with a fake.
"""
from __future__ import annotations

import contextlib
import logging
import secrets

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..redis_client import get_redis
from ..utils.timeutil import utc_now
from . import imap_config, inbound_mail, inbound_parse
from . import settings as settings_svc
from .imap_client import open_session

logger = logging.getLogger("fileheron.imap")

K = settings_svc.Keys

# Hard ceiling on a single fetched message. Deliberately below the worker's
# memory limit (WORKER_MEM_LIMIT defaults to 512m) because ingestion holds
# several copies: the raw bytes, the decoded payload per part, a BytesIO for the
# AV stream and a temp file.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# A cap on MIME parts, because the byte cap does not bound memory. Parsing
# allocates one object per part and the graph runs 20-30x the wire size, so a
# message well under MAX_MESSAGE_BYTES can still exceed the worker's memory
# limit. 5,000 parts is far above any real mail and far below the ~340,000 that
# first threatened a 512 MB worker in measurement.
MAX_MESSAGE_PARTS = 5_000

# Messages handled per run. The first poll against an existing mailbox searched
# ALL and iterated the lot: enabling inbound on an account with years of history
# meant one run trying to fetch tens of thousands of messages - the admin's
# "Fetch now" never returned (it runs inline in the request handler), the poll
# lock expired mid-run, ARQ killed and retried the job while the original thread
# kept going, and with `post_fetch_action=delete` the run expunged the whole
# historical mailbox. Bounded, the backlog drains over successive ticks with the
# highwater persisted after every message (audit #2).
MAX_MESSAGES_PER_RUN = 200


def _int_setting(db: Session, key: str) -> int:
    raw = settings_svc.get(db, key)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


_POLL_LOCK_KEY = "fh:imap:poll:lock"
# Must outlive the ARQ job it protects (`worker.job_timeout = 2100`), or the
# lock expires under a still-running poll and the next cron tick starts a
# second one against the same mailbox: with `post_fetch_action=delete` the
# newcomer can expunge a message the first run is still mid-handling, and the
# two race on the highwater. "One poll at a time across the whole stack" was
# only true for runs shorter than the TTL (audit #2). A killed worker still
# frees this within the window; the batch cap below is what keeps a run from
# needing anything like it.
_POLL_LOCK_TTL_SEC = 2400


def _acquire_poll_lock() -> str | None:
    """Claim the mailbox, or return None if another run already holds it.

    `fetch-now` runs the poll inline while the five-minute cron can enter the
    same function concurrently, and two runs both read the same `last_uid`,
    both fetch the same messages and race on the dedup insert. Redis being
    unavailable means no lock, not no poll: ingestion matters more than the
    narrow duplicate-fetch window this closes (audit 2026-07-30)."""
    token = secrets.token_hex(8)
    try:
        if get_redis().set(_POLL_LOCK_KEY, token, nx=True, ex=_POLL_LOCK_TTL_SEC):
            return token
        return None
    except Exception:
        logger.warning("imap: poll lock unavailable (redis); proceeding unguarded")
        return token


def _release_poll_lock(token: str) -> None:
    """Only release a lock we still own - a run that overran the TTL must not
    delete the successor's claim."""
    try:
        r = get_redis()
        if r.get(_POLL_LOCK_KEY) == token:
            r.delete(_POLL_LOCK_KEY)
    except Exception:
        pass


def run_poll(*, manual: bool, db: Session | None = None, session_opener=open_session) -> dict:
    """Fetch new mail and ingest it. ``manual`` marks the admin "Fetch now" call
    (kept for symmetry; scheduling is handled by the cron dispatcher now). Opens
    its own DB session when one isn't supplied (the worker thread case)."""
    own = db is None
    db = db or SessionLocal()
    lock_token: str | None = None
    try:
        # Feature guard only. Cadence/enable is owned by the cron scheduler
        # (services/cron_schedule.py 'imap_poll') as of v1.28.0; this no longer
        # self-gates on interval/mode.
        if not imap_config.is_enabled(db):
            return {"ok": True, "skipped": "disabled"}

        # One poll at a time across the whole stack. `fetch-now` runs this
        # inline in the request while the five-minute cron can enter it
        # concurrently in the worker; both then read the same `last_uid`, fetch
        # the same messages and race on the dedup insert - and with a
        # post-fetch action of delete/move, the loser can apply that action to
        # a message the winner is mid-ingest on (audit 2026-07-30).
        lock_token = _acquire_poll_lock()
        if lock_token is None:
            return {"ok": True, "skipped": "already_running"}

        cfg = imap_config.resolve_imap_config(db)
        if not cfg.is_configured:
            return {"ok": False, "error": "not_configured"}

        action = imap_config.post_fetch_action(db)
        move_to = imap_config.move_folder(db)
        last_uid = _int_setting(db, K.IMAP_LAST_UID)
        prev_validity = _int_setting(db, K.IMAP_UIDVALIDITY)

        fetched = ingested = total = skipped = refused = backlog = 0
        with session_opener(cfg) as sess:
            uidvalidity = sess.select(cfg.mailbox)
            total = getattr(sess, "message_count", 0)
            # Only reset on a REAL, changed UIDVALIDITY. A 0/unparseable value
            # (select() couldn't read it) must NOT trigger a full re-scan +
            # duplicate ingestion; treat it as "unchanged" and keep the highwater.
            if uidvalidity and uidvalidity != prev_validity:
                last_uid = 0  # mailbox reset -> re-evaluate from the start
            pending = sess.search_uids_after(last_uid)
            backlog = max(0, len(pending) - MAX_MESSAGES_PER_RUN)
            if backlog:
                logger.info(
                    "imap poll: %d messages pending, handling %d this run",
                    len(pending), MAX_MESSAGES_PER_RUN,
                )
            for uid in pending[:MAX_MESSAGES_PER_RUN]:
                # Per-message boundary. Without it, one message that parse() or
                # ingest() chokes on propagates to the outer handler - which
                # returns before `last_uid` is ever persisted. The next poll
                # then starts from the same highwater, fetches the same message,
                # and dies the same way: ALL inbound ingestion stops permanently
                # on a single malformed mail, with no way out but manual
                # intervention on the mailbox (audit 2026-07-30).
                #
                # We advance past the offender rather than retrying it forever.
                # It is left untouched on the server (no post-fetch action runs
                # for it), so it stays available for an admin to inspect.
                try:
                    # Refuse an oversize message BEFORE materialising it. The
                    # per-message try/except below cannot save us here: decoding
                    # a huge mail (payload decode, then a BytesIO copy for the
                    # AV stream, then a temp file) against a 512 MB worker limit
                    # gets the process OOM-KILLED, and SIGKILL raises nothing.
                    # The highwater is only persisted after the loop, so the
                    # same message re-killed the worker on every poll - taking
                    # AV scans, outbound email and every cron with it. That is
                    # the same permanent wedge v2.2.0 closed for exceptions,
                    # reachable by a different route (audit 2026-07-30).
                    size = None
                    if hasattr(sess, "fetch_size"):
                        size = sess.fetch_size(uid)
                    if size is not None and size > MAX_MESSAGE_BYTES:
                        skipped += 1
                        logger.warning(
                            "imap poll: uid %s is %d bytes (limit %d); skipping "
                            "without fetching (message left on the server)",
                            uid, size, MAX_MESSAGE_BYTES,
                        )
                        last_uid = max(last_uid, uid)
                        continue
                    # An unknown size used to fall through to an UNBOUNDED
                    # download - which is the one thing this guard exists to
                    # prevent, and `fetch_size` returns None whenever the server
                    # declines RFC822.SIZE. Ask for a bounded body instead.
                    raw = (
                        sess.fetch_raw(uid)
                        if size is not None
                        else sess.fetch_raw(uid, max_bytes=MAX_MESSAGE_BYTES)
                    )
                    if raw is None:
                        # Advance past it. The `continue` used to skip the
                        # highwater update, so a UID whose FETCH returns nothing
                        # - a message deleted in webmail between the SEARCH and
                        # the FETCH - was re-selected on every poll from then
                        # on, indefinitely (audit #2).
                        skipped += 1
                        last_uid = max(last_uid, uid)
                        continue
                    # Belt and braces for a server that under-reports or does
                    # not answer RFC822.SIZE at all.
                    if len(raw) > MAX_MESSAGE_BYTES:
                        skipped += 1
                        logger.warning(
                            "imap poll: uid %s fetched %d bytes, over the %d limit; "
                            "skipping",
                            uid, len(raw), MAX_MESSAGE_BYTES,
                        )
                        last_uid = max(last_uid, uid)
                        continue
                    # Bound the STRUCTURE, not just the byte count. The two
                    # guards above bound raw octets; `email.message_from_bytes`
                    # then builds one Message object per MIME part, and for many
                    # tiny parts the object graph is 20-30x the wire size.
                    # Measured in a container at the worker's 512m limit: 48 MB
                    # of ~513,000 minimal parts passed both size guards and was
                    # SIGKILLed; 63 MB peaked at 883 MB RSS.
                    #
                    # SIGKILL raises nothing, so the per-message try/except
                    # cannot help - and the highwater for this UID is only
                    # written further down, AFTER the parse, so the next poll
                    # re-selected the same message and died again. Docker
                    # restarts the worker, cron re-enqueues every 5 minutes, and
                    # inbound ingestion, AV scanning, outbound email and every
                    # other cron stop permanently until someone deletes the mail
                    # by hand. Exactly the wedge the size guard was added to
                    # close, reachable by a dimension it did not measure
                    # (audit #2, inbound).
                    parts = raw.count(b"Content-Type:")
                    if parts > MAX_MESSAGE_PARTS:
                        skipped += 1
                        logger.warning(
                            "imap poll: uid %s declares %d MIME parts (limit %d) "
                            "in %d bytes; skipping without parsing (message left "
                            "on the server)",
                            uid, parts, MAX_MESSAGE_PARTS, len(raw),
                        )
                        last_uid = max(last_uid, uid)
                        continue
                    # Advance the highwater BEFORE parsing. A hard process kill
                    # during parse must not re-select the same message forever;
                    # losing one message to a skip is recoverable, an endless
                    # crash loop is not.
                    settings_svc.set_value(
                        db, key=K.IMAP_LAST_UID, value=str(max(last_uid, uid)), actor=None
                    )
                    db.commit()
                    fetched += 1
                    parsed = inbound_parse.parse(raw)
                    try:
                        msg = inbound_mail.ingest(
                            db, parsed, uid=uid, uidvalidity=uidvalidity
                        )
                    except inbound_mail.UnknownSenderError:
                        # Nothing was stored. Skip it and leave it on the server
                        # so an admin can still see it in the mailbox and, if it
                        # is legitimate, invite the sender.
                        db.rollback()
                        refused += 1
                        last_uid = max(last_uid, uid)
                        continue
                    if msg is not None:
                        ingested += 1
                    db.commit()
                except Exception:
                    db.rollback()
                    skipped += 1
                    logger.exception(
                        "imap poll: uid %s could not be ingested; skipping it so the "
                        "poll can continue (message left on the server)",
                        uid,
                    )
                    last_uid = max(last_uid, uid)
                    continue
                # Apply the server-side action only when we OWN this message: a
                # genuine new ingest, or a true re-poll of THIS (uidvalidity, uid).
                # If ingest returned None because a DIFFERENT message shares this
                # Message-ID, deleting/moving it would destroy a distinct, unread
                # mail that was never ingested.
                owns_message = msg is not None or inbound_mail.ingested_by_uid(
                    db, uidvalidity=uidvalidity, uid=uid
                )
                # A message whose attachments could not all be stored is NOT
                # safely ours: `delete` would expunge the only remaining copy of
                # a file that exists nowhere else (audit #2). Downgrade to the
                # non-destructive action and leave the mail on the server.
                incomplete = bool(msg is not None and getattr(msg, "_fh_incomplete", False))
                if incomplete:
                    logger.warning(
                        "imap poll: uid %s stored with missing attachment bytes; "
                        "leaving the message on the server",
                        uid,
                    )
                if owns_message and not incomplete:
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
                # Persist the highwater per message, not once the loop is over.
                # A failure anywhere in the loop - a dropped connection, a
                # session teardown error - threw away the progress of every
                # message already committed, so the next poll re-downloaded the
                # whole batch and re-applied the post-fetch action to mail that
                # had already been ingested (audit 2026-07-30). Written after
                # the server-side action so the highwater never runs ahead of a
                # message we have not finished handling.
                settings_svc.set_value(
                    db, key=K.IMAP_LAST_UID, value=str(last_uid), actor=None
                )
                db.commit()

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
            "imap poll: mailbox=%s total=%d fetched=%d ingested=%d skipped=%d "
            "refused=%d backlog=%d last_uid=%d",
            cfg.mailbox, total, fetched, ingested, skipped, refused, backlog, last_uid,
        )
        return {
            "ok": True, "fetched": fetched, "ingested": ingested,
            "skipped": skipped, "refused_unknown_sender": refused,
            "backlog": backlog,
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
        if lock_token is not None:
            _release_poll_lock(lock_token)
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

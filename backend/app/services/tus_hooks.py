"""tusd hook handlers.

tusd POSTs to ``/api/internal/tus-hooks`` for each hook event with a JSON
body shaped like::

    {
      "Type": "pre-create" | "pre-finish" | "post-finish" | "post-terminate",
      "Event": {
        "Upload": {
          "ID": "<tus-upload-id>",
          "Size": <int>,
          "MetaData": { "filename": "...", "fh_payload": "...", "fh_sig": "..." }
        }
      }
    }

We re-verify the HMAC envelope on EVERY hook call. The shared secret is
``settings.TUS_HOOK_SECRET``; nothing else can mint a valid envelope.

Defense-in-depth: the hook endpoint is on the internal-only `internal`
Docker network; even without HMAC, only tusd can reach it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.file import File, FileState
from ..models.user import User
from ..utils.timeutil import utc_now
from . import file as file_svc
from . import quota as quota_svc
from .tus_signing import UploadEnvelope, verify_envelope

logger = logging.getLogger("fileheron.tus_hooks")

# tusd's default upload ID is a hex / urlsafe-base64 string. Validate
# defensively so a malicious / malformed ID can never escape the
# upload directory in a future refactor of finalize_to_disk.
#
# The bound is 64, not 128, because `files.tus_upload_id` is String(64): an id
# between 65 and 128 characters passed this check and then raised a DataError
# under MariaDB's strict mode, turning a malformed input into a 500 on the
# hook rather than the clean 400 this validator exists to produce. SQLite is
# permissive about length, so the test suite could not have caught it
# (audit 2026-07-30). tusd's own ids are 32 hex chars, so nothing legitimate
# is refused.
_TUS_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _check_tus_upload_id(tus_upload_id: str | None) -> str:
    if not tus_upload_id or not _TUS_UPLOAD_ID_RE.match(tus_upload_id):
        raise AppError(
            400, "TUSD_INVALID_UPLOAD_ID", "Malformed tusd upload ID."
        )
    return tus_upload_id


def _extract_envelope(
    meta: dict[str, str], *, enforce_exp: bool = False
) -> UploadEnvelope:
    """tusd's MetaData object is already a {key: utf8_value} dict (the
    Upload-Metadata header is parsed before we see it). Pull and verify
    fh_payload + fh_sig.

    `enforce_exp` defaults to False: the envelope's 1h expiry is authorisation
    to BEGIN an upload, so only handle_pre_create passes True. Enforcing it on
    the later hooks killed any transfer slower than the TTL at finalize, after
    every byte had already been uploaded (see verify_envelope's docstring). The
    HMAC is still verified on every hook - that is the control tusd cannot
    forge, and it is unaffected by this.
    """
    payload_b64 = meta.get("fh_payload", "")
    sig = meta.get("fh_sig", "")
    return verify_envelope(payload_b64, sig, enforce_exp=enforce_exp)


def _extract_upload(event_body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Returns (upload_dict, metadata_dict)."""
    event = event_body.get("Event") or {}
    upload = event.get("Upload") or {}
    meta = upload.get("MetaData") or {}
    return upload, meta


# ---------------------------------------------------------------------------
# pre-create - happens before tusd accepts any bytes. Validate envelope,
# load file/share rows, check quota, return 200 (allow) or 4xx (deny).
# ---------------------------------------------------------------------------


def handle_pre_create(db: Session, body: dict[str, Any]) -> None:
    # Block brand-new tusd uploads during maintenance. An already-in-progress
    # resumable upload continues via PATCH (no pre-create) and is untouched.
    from . import maintenance as maintenance_svc
    from . import storage_guard

    # Same disk-pressure gate the direct-upload routes apply. Without it here,
    # the guard covered only uploads under 100 MB while multi-GB resumable
    # uploads - the ones that actually fill a volume - proceeded onto a disk
    # already flagged critically low (audit 2026-07-30).
    storage_guard.refuse_if_critical_low(db)
    maintenance_svc.refuse_if_maintenance(db, kind="upload")

    upload, meta = _extract_upload(body)
    envelope = _extract_envelope(meta, enforce_exp=True)

    # A deferred-length creation (`Upload-Defer-Length: 1`) announces Size=0 and
    # declares the real length later, on a PATCH that fires no hook. Nothing
    # downstream bounds it: tusd carries no `-max-size`, so the client can then
    # push arbitrary bytes into the working dir against ONE authorised file row.
    # pre-finish does reject the mismatch, but only after the bytes have landed,
    # and the only thing that reclaims them is the 24h abandoned-upload sweeper -
    # `refuse_if_critical_low` cannot throttle the burst because it reads a kv
    # flag written by the HOURLY disk_check cron, not a live stat.
    #
    # The envelope authorises exactly `max_size` bytes, so an upload that
    # declines to state its length up front has nothing legitimate to gain.
    if upload.get("SizeIsDeferred"):
        raise AppError(
            400,
            "DEFERRED_LENGTH_REFUSED",
            "Deferred-length uploads are not accepted; declare Upload-Length up front.",
        )

    # Equality, not `<=`. The envelope IS the authorisation and pre-finish
    # already forces the final size to equal it, so an announcement that
    # disagrees is either a client bug or an attempt to widen the window
    # between what was authorised and what is written.
    announced_size = int(upload.get("Size", 0))
    if announced_size != envelope["max_size"]:
        raise AppError(
            413,
            "SIZE_OVER_ENVELOPE",
            f"Announced size ({announced_size}) doesn't match the authorised size ({envelope['max_size']}).",
        )

    # Pre-create runs BEFORE tusd assigns its upload id, so we can't store
    # it yet. We DO check the file row exists and is in the right state.
    file_row = db.query(File).filter(File.id == envelope["file_id"]).one_or_none()
    if file_row is None or file_row.state != FileState.uploading:
        raise AppError(404, "FILE_RECORD_MISSING", "Upload was not authorised.")
    if file_row.size_bytes != envelope["max_size"]:
        raise AppError(
            413, "SIZE_MISMATCH", "File size in envelope doesn't match the registered file."
        )

    # Reserve quota.
    user = db.query(User).filter(User.id == envelope["owner_user_id"]).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(403, "FORBIDDEN", "Uploader is no longer active.")
    # Reserve the HMAC-authorised max_size (== file_row.size_bytes; pre-finish
    # forces the final actual size to equal it too), NOT the client-announced
    # Size. A deferred-length upload announces Size=0, so reserving `announced_size`
    # reserved 0 - and post-terminate then released a client-set Size, letting an
    # attacker drain the quota counter below true usage (repeatable bypass).
    # Reserve at most once per file. Pre-create is the one hook that cannot be
    # bound to a single tusd upload, and @uppy/tus replays the creation POST
    # whenever its response is lost - so the same file used to reserve its bytes
    # twice while only ever being released once, locking the uploader out of
    # their own quota until the hourly reconcile repaired the counter.
    #
    # The Redis marker inside reserve_bytes_once is what actually guards this.
    # This condition reads like a second, independent guard and is not one:
    # tusd supplies no Upload.ID on pre-create (measured - see the block below),
    # and finalize clears the column, so `tus_upload_id` is None on every
    # pre-create this code has ever seen. It is kept as a cheap short-circuit
    # for a future tusd that does populate the field, not as a fallback for a
    # Redis outage - there is none (audit 2026-07-30, corrected 2026-08-16).
    if file_row.tus_upload_id is None:
        quota_svc.reserve_bytes_once(
            db,
            user=user,
            additional_bytes=envelope["max_size"],
            file_id=file_row.id,
        )

    # Link the row to the tusd upload NOW, not at finalize.
    #
    # `files.tus_upload_id` was only ever written by finalize_to_disk, which
    # then clears it - so an IN-FLIGHT upload always had NULL there. That made
    # cleanup_abandoned_uploads' "leave it alone, the finalize hook may yet
    # land" guard structurally unreachable: it looks up
    # `tus_upload_id == <id> AND state == uploading`, which no live upload can
    # ever satisfy. The only thing protecting a slow multi-hour upload from the
    # sweeper was the mtime cutoff (audit 2026-07-30).
    #
    # This comment used to assert "tusd v2 supplies Event.Upload.ID on
    # pre-create". It does not. Measured against the pinned
    # tusproject/tusd:v2.9.2 with a logging hooks-dir, the pre-create payload
    # carries `"ID": ""` - the id does not exist yet, because the upload has not
    # been created. So this branch never fired, tus_upload_id stayed NULL for the
    # whole transfer, and the guard this stamp was added to enable stayed
    # unreachable. handle_post_receive does the stamping that actually works;
    # this is kept because a future tusd that does populate the field costs
    # nothing here, and because removing it would silently change behaviour if
    # one ever does.
    pre_create_id = upload.get("ID")
    if pre_create_id:
        try:
            file_row.tus_upload_id = _check_tus_upload_id(pre_create_id)
        except AppError:
            logger.warning(
                "pre-create supplied an unusable upload id for file %s; "
                "the abandoned-upload sweeper will fall back to mtime",
                file_row.id,
            )
    db.commit()


# ---------------------------------------------------------------------------
# pre-finish - happens just before tusd marks the upload complete. We can
# still 4xx here to refuse the upload. Last sanity check on size.
# ---------------------------------------------------------------------------


def handle_pre_finish(db: Session, body: dict[str, Any]) -> None:
    upload, meta = _extract_upload(body)
    envelope = _extract_envelope(meta)

    actual_size = int(upload.get("Size", 0))
    if actual_size != envelope["max_size"]:
        raise AppError(
            413,
            "FINAL_SIZE_MISMATCH",
            f"Final size ({actual_size}) doesn't match the authorised size ({envelope['max_size']}).",
        )

    # Capture the tusd upload id on the file row so post-finish knows where
    # to read from.
    tus_upload_id = _check_tus_upload_id(upload.get("ID"))

    file_row = db.query(File).filter(File.id == envelope["file_id"]).one_or_none()
    if file_row is None or file_row.state != FileState.uploading:
        raise AppError(404, "FILE_RECORD_MISSING", "Upload was not authorised.")
    file_row.tus_upload_id = tus_upload_id
    db.commit()


# ---------------------------------------------------------------------------
# post-finish - tusd has finalized the upload. Move the file into permanent
# storage, mark ready_unscanned. (Phase 5 will additionally enqueue an AV
# scan here.)
# ---------------------------------------------------------------------------


def handle_post_finish(db: Session, body: dict[str, Any]) -> None:
    upload, meta = _extract_upload(body)
    envelope = _extract_envelope(meta)

    tus_upload_id = _check_tus_upload_id(upload.get("ID"))
    file_row = db.query(File).filter(File.id == envelope["file_id"]).one_or_none()
    if file_row is None:
        logger.warning("post-finish for unknown file %s; ignoring", envelope["file_id"])
        return
    if file_row.state != FileState.uploading:
        logger.info("post-finish on file %s in state %s; idempotent skip", file_row.id, file_row.state)
        return

    file_svc.finalize_to_disk(db, file=file_row, tus_upload_id=tus_upload_id)
    db.commit()


    # Enqueue the AV scan. The file is in `ready_unscanned` after
    # finalize_to_disk; downloads are blocked until the worker flips it
    # to `clean`. Failures here are logged but don't fail the upload -
    # the `cleanup_stale_uploads` cron re-enqueues anything still
    # `ready_unscanned` once it is 30 minutes past finalize - and that cron runs
    # hourly, so recovery lands 30-90 minutes out, not at the 30-minute mark. (This said "a manual rescan
    # can recover", which named a thing that does not exist: there is no rescan
    # endpoint or admin action anywhere. The cron is the recovery.)
    from . import job_queue
    job_queue.enqueue("av_scan_file", file_row.id)


# ---------------------------------------------------------------------------
# post-terminate - upload was abandoned. Release the quota reservation, mark
# the file row deleted (not finalized). The bytes that did land in tusd's
# working dir are cleaned up by tusd itself.
# ---------------------------------------------------------------------------


def handle_post_terminate(db: Session, body: dict[str, Any]) -> None:
    upload, meta = _extract_upload(body)
    try:
        envelope = _extract_envelope(meta)
    except AppError:
        # If we can't verify, we can't safely release quota. Just log.
        logger.warning("post-terminate with invalid envelope; cannot reconcile quota")
        return

    file_row = db.query(File).filter(File.id == envelope["file_id"]).one_or_none()
    if file_row is None:
        return
    if file_row.state == FileState.uploading:
        # Release exactly what pre-create reserved (the authorised max_size), never
        # the client-reported Size (attacker-controllable and unbounded).
        quota_svc.release_bytes(user_id=envelope["owner_user_id"], bytes_to_free=envelope["max_size"])
        # Let a genuine retry of this file reserve again.
        quota_svc.clear_reserve_marker(envelope["file_id"])
        file_row.state = FileState.deleted
        db.commit()


# ---------------------------------------------------------------------------
# post-receive - tusd reports progress for an in-flight upload, on the interval
# set by -progress-hooks-interval. This is the ONLY hook that fires while bytes
# are moving, and enabling it is what makes "is this upload still alive?" an
# answerable question.
# ---------------------------------------------------------------------------


def handle_post_receive(db: Session, body: dict[str, Any]) -> None:
    upload, meta = _extract_upload(body)
    try:
        envelope = _extract_envelope(meta)
    except AppError:
        # Progress is advisory. An unverifiable envelope must not 4xx: tusd logs
        # a failed progress hook and, worse, this runs on every interval tick,
        # so a noisy failure here would be a per-second error for the whole
        # transfer. The sweepers' COALESCE fallback covers a row we cannot stamp.
        logger.warning("post-receive with invalid envelope; ignoring")
        return

    file_row = db.query(File).filter(File.id == envelope["file_id"]).one_or_none()
    if file_row is None or file_row.state != FileState.uploading:
        return

    now = utc_now()
    file_row.last_progress_at = now

    # Stamp the tusd upload id here too. It is NOT available at pre-create -
    # measured against the pinned tusproject/tusd v2.9.2, Event.Upload.ID is the
    # empty string there, which the comment above handle_pre_create's stamp
    # assumed otherwise. That left tus_upload_id NULL for the whole transfer,
    # which in turn made cleanup_abandoned_uploads' "leave it alone, the finalize
    # hook may yet land" guard - `tus_upload_id == <id> AND state == uploading` -
    # unmatchable by any live upload. post-receive is the first hook that
    # actually carries the id, so stamping it here is what makes that guard real.
    if not file_row.tus_upload_id:
        pre_id = upload.get("ID")
        if pre_id:
            try:
                file_row.tus_upload_id = _check_tus_upload_id(pre_id)
            except AppError:
                logger.warning("post-receive supplied an unusable upload id for file %s", file_row.id)

    db.commit()


HOOK_DISPATCH = {
    "pre-create": handle_pre_create,
    "pre-finish": handle_pre_finish,
    "post-receive": handle_post_receive,
    "post-finish": handle_post_finish,
    "post-terminate": handle_post_terminate,
}

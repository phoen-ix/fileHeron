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
from . import file as file_svc
from . import quota as quota_svc
from .tus_signing import UploadEnvelope, verify_envelope

logger = logging.getLogger("fileheron.tus_hooks")

# tusd's default upload ID is a hex / urlsafe-base64 string. Validate
# defensively so a malicious / malformed ID can never escape the
# upload directory in a future refactor of finalize_to_disk.
_TUS_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _check_tus_upload_id(tus_upload_id: str | None) -> str:
    if not tus_upload_id or not _TUS_UPLOAD_ID_RE.match(tus_upload_id):
        raise AppError(
            400, "TUSD_INVALID_UPLOAD_ID", "Malformed tusd upload ID."
        )
    return tus_upload_id


def _extract_envelope(meta: dict[str, str]) -> UploadEnvelope:
    """tusd's MetaData object is already a {key: utf8_value} dict (the
    Upload-Metadata header is parsed before we see it). Pull and verify
    fh_payload + fh_sig."""
    payload_b64 = meta.get("fh_payload", "")
    sig = meta.get("fh_sig", "")
    return verify_envelope(payload_b64, sig)


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
    maintenance_svc.refuse_if_maintenance(db, kind="upload")

    upload, meta = _extract_upload(body)
    envelope = _extract_envelope(meta)

    announced_size = int(upload.get("Size", 0))
    if announced_size > envelope["max_size"]:
        raise AppError(
            413,
            "SIZE_OVER_ENVELOPE",
            f"Announced size ({announced_size}) exceeds authorised max ({envelope['max_size']}).",
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
    quota_svc.reserve_bytes(db, user=user, additional_bytes=announced_size)
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
    # a manual rescan can recover.
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
        quota_svc.release_bytes(user_id=envelope["owner_user_id"], bytes_to_free=int(upload.get("Size", 0)))
        file_row.state = FileState.deleted
        db.commit()


HOOK_DISPATCH = {
    "pre-create": handle_pre_create,
    "pre-finish": handle_pre_finish,
    "post-finish": handle_post_finish,
    "post-terminate": handle_post_terminate,
}

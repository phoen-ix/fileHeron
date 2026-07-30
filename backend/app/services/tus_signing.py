"""HMAC envelope signing for TUS Upload-Metadata.

Flow:
1. Backend issues an envelope on /api/uploads/init that authorises an
   upload (share_id, file_id, owner_user_id, max_size, exp).
2. Client embeds {fh_payload: <b64-json>, fh_sig: <hex>} in TUS
   Upload-Metadata (it's a comma-separated `key value` map per the TUS spec).
3. tusd forwards Upload-Metadata to every hook call (pre-create, pre-finish,
   post-finish, post-terminate).
4. Backend hook re-extracts payload + sig, verifies HMAC, checks exp, acts.

The shared secret is `settings.TUS_HOOK_SECRET`. tusd does not see it.
Forged envelopes from any party that doesn't know the secret are rejected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import TypedDict

from ..config import settings
from ..middleware.errors import AppError


class UploadEnvelope(TypedDict):
    v: int
    share_id: str
    file_id: str
    owner_user_id: int
    filename: str
    mime_type: str
    max_size: int
    exp: int  # unix epoch seconds


def sign_envelope(payload: UploadEnvelope) -> tuple[str, str]:
    """Returns (payload_b64, sig_hex). The caller embeds both in
    Upload-Metadata as `fh_payload <b64>,fh_sig <hex_b64_or_plain>`.

    The signature is HMAC-SHA256 over the canonical JSON bytes."""
    json_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(settings.TUS_HOOK_SECRET.encode("utf-8"), json_bytes, hashlib.sha256).hexdigest()
    payload_b64 = base64.urlsafe_b64encode(json_bytes).rstrip(b"=").decode("ascii")
    return payload_b64, sig


def verify_envelope(
    payload_b64: str, sig_hex: str, *, enforce_exp: bool = True
) -> UploadEnvelope:
    """Re-decode and re-HMAC. Raises AppError on any mismatch.

    `enforce_exp` gates ONLY the wall-clock expiry check. The envelope's `exp`
    is authorisation to *begin* an upload, so it is enforced at pre-create and
    nowhere else.

    It used to be enforced on every hook, including pre-finish/post-finish. An
    upload that took longer than the 1h TTL to transfer therefore died at
    finalize with TUS_ENVELOPE_EXPIRED after the bytes were fully uploaded, and
    cleanup_stale_uploads later flipped the share to `failed`. That is not
    hypothetical: on the reference deployment 3 of 10 shares died exactly this
    way after 3.07 GB, 3.07 GB and 0.37 GB transfers (audit 2026-07-30). At
    10 Mbps the practical ceiling was ~4.5 GB against an advertised 30 GB.

    Dropping the check on the later hooks costs nothing: the HMAC (which tusd
    cannot mint) still authenticates every hook, and the file/share rows named
    in the envelope carry their own state gates.
    """
    if not payload_b64 or not sig_hex:
        raise AppError(403, "INVALID_TUS_ENVELOPE", "Missing upload envelope.")
    try:
        # Add back the base64 padding stripped during encode.
        padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
        json_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception:
        raise AppError(403, "INVALID_TUS_ENVELOPE", "Malformed upload envelope.") from None

    expected = hmac.new(settings.TUS_HOOK_SECRET.encode("utf-8"), json_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_hex):
        raise AppError(403, "INVALID_TUS_ENVELOPE", "Upload envelope signature is invalid.")

    try:
        payload: UploadEnvelope = json.loads(json_bytes.decode("utf-8"))
    except Exception:
        raise AppError(403, "INVALID_TUS_ENVELOPE", "Upload envelope JSON is invalid.") from None

    if payload.get("v") != 1:
        raise AppError(403, "INVALID_TUS_ENVELOPE", "Unknown envelope version.")
    if enforce_exp and payload.get("exp", 0) < int(time.time()):
        raise AppError(403, "TUS_ENVELOPE_EXPIRED", "Upload authorisation expired.")
    return payload


# ---------------------------------------------------------------------------
# Upload-Metadata helpers - the TUS protocol metadata format.
# `Upload-Metadata: key1 base64,key2 base64`. Values are base64-of-utf8.
# ---------------------------------------------------------------------------


def parse_upload_metadata(header: str) -> dict[str, str]:
    """Returns map of {key: utf8_value}. Skips malformed entries."""
    out: dict[str, str] = {}
    if not header:
        return out
    for entry in header.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(" ", 1)
        if len(parts) != 2:
            # tusd allows valueless keys; we skip them.
            continue
        key, b64 = parts
        try:
            value = base64.b64decode(b64).decode("utf-8")
        except Exception:
            continue
        out[key.strip()] = value
    return out


def build_upload_metadata_header(payload_b64: str, sig_hex: str, filename: str) -> str:
    """Convenience for clients that don't want to assemble the header
    themselves. Returns the value to set as Upload-Metadata."""
    parts = [
        ("filename", filename),
        ("fh_payload", payload_b64),
        ("fh_sig", sig_hex),
    ]
    return ",".join(
        f"{k} {base64.b64encode(v.encode('utf-8')).decode('ascii')}" for k, v in parts
    )

"""HMAC envelope signing for TUS Upload-Metadata."""
from __future__ import annotations

import base64
import time

import pytest

from app.middleware.errors import AppError
from app.services import tus_signing as ts


def _parse_upload_metadata(header: str) -> dict[str, str]:
    """The inverse of `build_upload_metadata_header`, as tusd reads it:
    `key1 base64,key2 base64`, malformed entries skipped. Lives here because
    only these tests ever parsed the header - production receives tusd's
    already-decoded map in the hook payload."""
    out: dict[str, str] = {}
    for entry in header.split(","):
        parts = entry.strip().split(" ", 1)
        if len(parts) != 2:
            continue
        key, b64 = parts
        try:
            out[key.strip()] = base64.b64decode(b64).decode("utf-8")
        except Exception:
            continue
    return out


def _good_envelope(**overrides):
    base = {
        "v": 1,
        "share_id": "00000000-0000-0000-0000-000000000001",
        "file_id": "00000000-0000-0000-0000-000000000002",
        "owner_user_id": 7,
        "filename": "x.bin",
        "mime_type": "application/octet-stream",
        "max_size": 1024,
        "exp": int(time.time()) + 600,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_sign_and_verify_round_trip():
    env = _good_envelope()
    payload_b64, sig = ts.sign_envelope(env)
    out = ts.verify_envelope(payload_b64, sig)
    assert out == env


@pytest.mark.asyncio
async def test_verify_rejects_tampered_payload():
    env = _good_envelope()
    payload_b64, sig = ts.sign_envelope(env)
    # Flip one byte by re-signing a different payload but using the old sig.
    other = _good_envelope(owner_user_id=99)
    other_b64, _ = ts.sign_envelope(other)
    with pytest.raises(AppError) as exc:
        ts.verify_envelope(other_b64, sig)
    assert exc.value.code == "INVALID_TUS_ENVELOPE"


@pytest.mark.asyncio
async def test_verify_rejects_expired():
    env = _good_envelope(exp=int(time.time()) - 10)
    payload_b64, sig = ts.sign_envelope(env)
    with pytest.raises(AppError) as exc:
        ts.verify_envelope(payload_b64, sig)
    assert exc.value.code == "TUS_ENVELOPE_EXPIRED"


@pytest.mark.asyncio
async def test_verify_rejects_unknown_version():
    env = _good_envelope(v=999)
    payload_b64, sig = ts.sign_envelope(env)
    with pytest.raises(AppError) as exc:
        ts.verify_envelope(payload_b64, sig)
    assert exc.value.code == "INVALID_TUS_ENVELOPE"


@pytest.mark.asyncio
async def test_parse_upload_metadata_round_trip():
    env = _good_envelope()
    payload_b64, sig = ts.sign_envelope(env)
    header = ts.build_upload_metadata_header(payload_b64=payload_b64, sig_hex=sig, filename="hi.txt")
    parsed = _parse_upload_metadata(header)
    assert parsed["filename"] == "hi.txt"
    assert parsed["fh_payload"] == payload_b64
    assert parsed["fh_sig"] == sig


@pytest.mark.asyncio
async def test_parse_upload_metadata_skips_bad_entries():
    parsed = _parse_upload_metadata("filename aGVsbG8=,bare,broken !!!")
    assert "filename" in parsed
    assert parsed["filename"] == "hello"
    # Malformed entries silently ignored.

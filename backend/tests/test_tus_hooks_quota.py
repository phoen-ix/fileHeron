"""Regression: tusd hook quota reserve/release must use the HMAC-authorised
max_size, never the client-reported Upload.Size.

A deferred-length tus upload reports Size=0 at pre-create (reserving 0) and lets
the client set an arbitrary Upload-Length before a terminate, so releasing the
client Size drained the quota counter below true usage - a repeatable quota
bypass. Both sides must key off envelope["max_size"] (== file_row.size_bytes,
which pre-finish also forces the final size to equal).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import tus_hooks
from app.services import tus_signing as ts

_MAX = 1_000_000_000
_FILE_ID = "00000000-0000-0000-0000-0000000000ff"


def _seed(db, owner):
    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, subject=None, message=None,
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(File(
        id=_FILE_ID, share_id=share.id, original_filename="big.bin",
        mime_type="application/octet-stream", size_bytes=_MAX,
        state=FileState.uploading, uploaded_by_id=owner.id,
    ))
    db.commit()


def _body(owner_id, upload_size):
    env = {
        "v": 1, "share_id": "00000000-0000-0000-0000-000000000001",
        "file_id": _FILE_ID, "owner_user_id": owner_id, "filename": "big.bin",
        "mime_type": "application/octet-stream", "max_size": _MAX,
        "exp": int(time.time()) + 600,
    }
    payload_b64, sig = ts.sign_envelope(env)
    return {"Event": {"Upload": {"ID": "abc123", "Size": upload_size,
            "MetaData": {"fh_payload": payload_b64, "fh_sig": sig}}}}


@pytest.fixture(autouse=True)
def _no_maintenance(monkeypatch):
    monkeypatch.setattr("app.services.maintenance.refuse_if_maintenance", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_pre_create_reserves_max_size_not_client_size(make_user, db, monkeypatch):
    owner = make_user(email="up@test.local", role=UserRole.client)
    _seed(db, owner)
    captured = {}
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes: captured.update(n=additional_bytes) or 0,
    )
    # Deferred-length upload: tusd reports Size=0.
    tus_hooks.handle_pre_create(db, _body(owner.id, upload_size=0))
    assert captured["n"] == _MAX  # reserved the authorised max, NOT 0


@pytest.mark.asyncio
async def test_post_terminate_releases_max_size_not_client_size(make_user, db, monkeypatch):
    owner = make_user(email="up@test.local", role=UserRole.client)
    _seed(db, owner)
    captured = {}
    monkeypatch.setattr(
        tus_hooks.quota_svc, "release_bytes",
        lambda *, user_id, bytes_to_free: captured.update(n=bytes_to_free),
    )
    # Attacker sets a huge Upload.Size before the terminate.
    tus_hooks.handle_post_terminate(db, _body(owner.id, upload_size=999_000_000_000))
    assert captured["n"] == _MAX  # released only the authorised max, NOT 999 GB

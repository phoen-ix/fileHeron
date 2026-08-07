"""Regression: tusd hook quota reserve/release must use the HMAC-authorised
max_size, never the client-reported Upload.Size.

A deferred-length tus upload reports Size=0 at pre-create (reserving 0) and lets
the client set an arbitrary Upload-Length before a terminate, so releasing the
client Size drained the quota counter below true usage - a repeatable quota
bypass. Both sides must key off envelope["max_size"] (== file_row.size_bytes,
which pre-finish also forces the final size to equal).

Deferred length is now refused outright at pre-create (see
`test_deferred_length_is_refused`): the later PATCH that declares the real
length fires no hook, so accepting the creation authorised one file row and let
it absorb arbitrary bytes. The release-side assertion below still matters - it
guards the terminate path, which is reached by uploads that were created
legitimately and can still carry an attacker-set Size.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.middleware.errors import AppError
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


def _body(owner_id, upload_size, *, deferred=False, upload_id="abc123"):
    env = {
        "v": 1, "share_id": "00000000-0000-0000-0000-000000000001",
        "file_id": _FILE_ID, "owner_user_id": owner_id, "filename": "big.bin",
        "mime_type": "application/octet-stream", "max_size": _MAX,
        "exp": int(time.time()) + 600,
    }
    payload_b64, sig = ts.sign_envelope(env)
    upload = {"ID": upload_id, "Size": upload_size,
              "MetaData": {"fh_payload": payload_b64, "fh_sig": sig}}
    if deferred:
        upload["SizeIsDeferred"] = True
    return {"Event": {"Upload": upload}}


@pytest.fixture(autouse=True)
def _no_maintenance(monkeypatch):
    monkeypatch.setattr("app.services.maintenance.refuse_if_maintenance", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fresh_reserve_marker():
    """Every test here seeds the same `_FILE_ID`, but `reserve_bytes_once`'s
    marker lives in Redis and outlives the DB fixture. With a reachable Redis
    the first test's marker made every later test skip the reservation it was
    asserting on; without one the fallback masks it. Clear both sides."""
    from app.services import quota as quota_svc

    quota_svc.clear_reserve_marker(_FILE_ID)
    yield
    quota_svc.clear_reserve_marker(_FILE_ID)


@pytest.mark.asyncio
async def test_pre_create_reserves_max_size_not_client_size(make_user, db, monkeypatch):
    owner = make_user(email="up@test.local", role=UserRole.client)
    _seed(db, owner)
    captured = {}
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes, exclude_file_id=None: (
            captured.update(n=additional_bytes, excluded=exclude_file_id) or 0
        ),
    )
    tus_hooks.handle_pre_create(db, _body(owner.id, upload_size=_MAX))
    # Reserved from the ENVELOPE, not from the (now equality-checked) client
    # Size - the envelope stays the single authority for what was authorised.
    assert captured["n"] == _MAX
    # The row is already committed and in STORED_STATES by now, so it must be
    # kept out of any lazy counter seed or it is charged twice.
    assert captured["excluded"] == _FILE_ID


@pytest.mark.asyncio
async def test_deferred_length_is_refused(make_user, db, monkeypatch):
    """`Upload-Defer-Length: 1` declares the real length on a later PATCH, which
    fires no hook - so the creation must not be authorised at all. Accepting it
    let one authorised file row absorb arbitrary bytes into the working dir,
    reclaimed only by the 24h abandoned-upload sweeper."""
    owner = make_user(email="up@test.local", role=UserRole.client)
    _seed(db, owner)
    charged = {}
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes, exclude_file_id=None: charged.update(n=additional_bytes) or 0,
    )
    with pytest.raises(AppError) as exc:
        tus_hooks.handle_pre_create(db, _body(owner.id, upload_size=0, deferred=True))
    assert exc.value.code == "DEFERRED_LENGTH_REFUSED"
    assert exc.value.status_code == 400
    # Refused before any reservation - a rejected creation must not charge quota.
    assert charged == {}


@pytest.mark.asyncio
async def test_announced_size_must_equal_authorised_size(make_user, db, monkeypatch):
    """An announcement that disagrees with the envelope widens the gap between
    what was authorised and what gets written. pre-finish already forces final
    == max_size; pre-create must not accept a different claim up front."""
    owner = make_user(email="up@test.local", role=UserRole.client)
    _seed(db, owner)
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes, exclude_file_id=None: 0,
    )
    for bad in (0, _MAX - 1, _MAX + 1):
        with pytest.raises(AppError) as exc:
            tus_hooks.handle_pre_create(db, _body(owner.id, upload_size=bad))
        assert exc.value.code == "SIZE_OVER_ENVELOPE"


@pytest.mark.asyncio
async def test_uppy_style_creation_retry_still_succeeds(make_user, db, monkeypatch):
    """@uppy/tus replays the creation POST when its response is lost. That
    legitimate retry must keep working - the size guards above must not turn a
    dropped response into a failed upload."""
    owner = make_user(email="up@test.local", role=UserRole.client)
    _seed(db, owner)
    calls = []
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes, exclude_file_id=None: calls.append(additional_bytes) or 0,
    )
    tus_hooks.handle_pre_create(db, _body(owner.id, _MAX, upload_id="first"))
    tus_hooks.handle_pre_create(db, _body(owner.id, _MAX, upload_id="second"))
    # Both accepted; charged exactly once (the tus_upload_id guard).
    assert calls == [_MAX]


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

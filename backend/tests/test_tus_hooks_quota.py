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


@pytest.fixture
def fake_quota_redis(monkeypatch):
    """An in-memory stand-in for `quota`'s Redis, following conftest's
    `_isolated_transfer_marks`.

    Not a nicety. `reserve_bytes_once` reaches Redis through a bare
    `get_redis()`, and this suite runs inside the compose network, so without
    this the once-marker is written to the LIVE Redis - which is also the only
    reason a marker test can pass locally at all. CI's `backend-tests` job has
    no Redis, `reserve_bytes_once` then fails OPEN by design, and the assertion
    that the charge is held to one becomes false. A test whose result depends on
    whether a production service happens to be reachable is not a test.
    """
    from app.services import quota as quota_svc

    store: dict[str, str] = {}

    class _Fake:
        def set(self, key, value, ex=None, nx=False):
            if nx and key in store:
                return None
            store[key] = value
            return True

        def get(self, key):
            return store.get(key)

        def delete(self, *keys):
            for k in keys:
                store.pop(k, None)

    monkeypatch.setattr(quota_svc, "get_redis", lambda: _Fake())
    yield store
    store.clear()


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
    # Both accepted; charged exactly once.
    #
    # NB: passing upload_id here does NOT exercise what it looks like. tusd
    # v2.9.2 sends Event.Upload.ID == "" at pre-create - measured against the
    # pinned image - so files.tus_upload_id is NULL for the whole transfer and
    # that guard never fires in production. What actually holds the charge to
    # one is quota.reserve_bytes_once's Redis NX marker, keyed on file_id. The
    # test below pins that directly, so the real mechanism has cover even if
    # this fixture's shape stops matching reality again.
    assert calls == [_MAX]


@pytest.mark.asyncio
async def test_a_replayed_creation_is_charged_once_by_the_redis_marker(
    make_user, db, monkeypatch, fake_quota_redis
):
    """The mechanism that actually bounds the replay, with tus_upload_id left
    NULL exactly as tusd leaves it.

    The replay itself is an accepted residual, not a defect: only ONE upload can
    finalize (pre-finish and post-finish both gate on state == uploading), so
    permanent storage is capped at the 1x max_size that was charged;
    post-terminate marks the row deleted, after which further replays 404;
    cleanup_abandoned_uploads reclaims superseded working files within 24h; and
    quota_bytes is NULL by default, so a stock install has no quota to bypass.
    Commit 6f09cf9 adjudicated it privilege-equivalent on those grounds and the
    reasoning still holds. What is left is transient staging-space
    amplification on an install that HAS set a quota - and pre-create's
    idempotency is load-bearing for the @uppy/tus retry above, so tightening it
    would trade a real regression for a bounded, self-healing one.
    """
    owner = make_user(email="up2@test.local", role=UserRole.client)
    _seed(db, owner)
    charged = []
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes, exclude_file_id=None: charged.append(additional_bytes) or 0,
    )

    # No upload_id at all - the shape tusd actually sends at pre-create.
    for _ in range(3):
        tus_hooks.handle_pre_create(db, _body(owner.id, _MAX, upload_id=""))

    assert charged == [_MAX], (
        "the Redis reservation marker did not hold the charge to one; "
        "tus_upload_id is NULL here, so nothing else can"
    )
    row = db.query(File).filter(File.id == _FILE_ID).one()
    assert row.tus_upload_id is None, (
        "fixture drift: tusd sends no upload id at pre-create"
    )


@pytest.mark.asyncio
async def test_a_replay_with_redis_down_charges_every_time_by_design(
    make_user, db, monkeypatch
):
    """With Redis unreachable the once-marker cannot be taken, and
    `reserve_bytes_once` deliberately falls back to reserving - it fails OPEN.

    So the replay bound documented on the test above exists ONLY while Redis is
    up. That is the intended trade (`quota.py`: the double-charge self-heals
    within the hour, refusing the upload does not), but it was unwritten, and
    the gap is exactly what made the marker test pass locally against a live
    Redis and fail in CI where there is none. Pinning the fallback here means
    the next person sees both halves instead of rediscovering one of them from
    a red pipeline.
    """
    owner = make_user(email="up3@test.local", role=UserRole.client)
    _seed(db, owner)

    def _down():
        raise RuntimeError("redis unreachable")

    from app.services import quota as quota_svc

    monkeypatch.setattr(quota_svc, "get_redis", _down)
    charged = []
    monkeypatch.setattr(
        tus_hooks.quota_svc, "reserve_bytes",
        lambda db, *, user, additional_bytes, exclude_file_id=None: charged.append(additional_bytes) or 0,
    )

    for _ in range(3):
        tus_hooks.handle_pre_create(db, _body(owner.id, _MAX, upload_id=""))

    assert charged == [_MAX, _MAX, _MAX], (
        "reserve_bytes_once must fail OPEN when the marker is unavailable - "
        "refusing the upload instead would turn a Redis blip into a failed transfer"
    )


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


# --- post-receive: the only hook that fires while bytes are moving ---------


@pytest.mark.asyncio
async def test_post_receive_stamps_progress(make_user, db, monkeypatch):
    """Without this the files row is untouched between pre-create and
    pre-finish, so the sweeper can only measure age-since-start."""
    owner = make_user(email="p@test.local", role=UserRole.admin)
    _seed(db, owner)
    before = db.query(File).filter(File.id == _FILE_ID).one()
    assert before.last_progress_at is None

    tus_hooks.handle_post_receive(db, _body(owner.id, upload_size=_MAX, upload_id="tus-live-1"))

    row = db.query(File).filter(File.id == _FILE_ID).one()
    assert row.last_progress_at is not None
    # post-receive is also the first hook that carries a real upload id, which
    # is what makes cleanup_abandoned_uploads' live-upload guard reachable.
    assert row.tus_upload_id == "tus-live-1"


@pytest.mark.asyncio
async def test_post_receive_is_inert_once_the_row_left_uploading(make_user, db, monkeypatch):
    """A late progress tick must not resurrect a finalized or reaped row."""
    owner = make_user(email="p2@test.local", role=UserRole.admin)
    _seed(db, owner)
    row = db.query(File).filter(File.id == _FILE_ID).one()
    row.state = FileState.deleted
    db.commit()

    tus_hooks.handle_post_receive(db, _body(owner.id, upload_size=_MAX, upload_id="tus-late"))

    row = db.query(File).filter(File.id == _FILE_ID).one()
    assert row.last_progress_at is None
    assert row.state == FileState.deleted


@pytest.mark.asyncio
async def test_post_receive_with_a_bad_envelope_does_not_raise(make_user, db, monkeypatch):
    """This fires every progress interval for the whole transfer. Raising here
    would turn one unverifiable upload into a per-tick error storm, and tusd
    logs a failed progress hook on each one."""
    owner = make_user(email="p3@test.local", role=UserRole.admin)
    _seed(db, owner)
    bad = {"Event": {"Upload": {"ID": "x", "Size": 1, "MetaData": {"fh_payload": "junk", "fh_sig": "junk"}}}}

    tus_hooks.handle_post_receive(db, bad)  # must not raise

    assert db.query(File).filter(File.id == _FILE_ID).one().last_progress_at is None

"""The lazy Redis seed must not charge the file it is being seeded for.

`_initialize_from_db` sums STORED_STATES, which includes `uploading`, and the
tus flow commits that `uploading` row in `/api/uploads/init` a whole HTTP
round-trip before tusd's pre-create hook reserves against it. So on the first
access - a brand-new quota'd user, or any user whose counter was flushed - the
seed already held the bytes, the INCRBY added them again, and a 6 GiB first
upload was refused against a 10 GiB quota.

Retrying was worse than failing. `reserve_bytes_once` sets its marker BEFORE
calling `reserve_bytes` and never cleared it when the reservation raised, so the
next pre-create for that same file skipped the charge entirely and uploaded
unmetered. That is a repeatable quota bypass, and the seed fix alone does not
close it.

The belief that produced all of this was written down: `workers/quota_reconcile`
asserted the seed "only seeds from finalized files - in-flight `uploading` rows
aren't yet visible at seed time". They are. A docstring cannot carry a test,
which is why it survived four releases.

From the 2026-07-30 audit residual sweep (res-05).
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import quota as quota_svc

_GB = 1024**3
_SIZE = 6 * _GB
_FILE_ID = "00000000-0000-0000-0000-0000000005a5"


class _FakeRedis:
    """Enough of redis-py for quota.py, with real SET NX and a faithful
    re-implementation of _RESERVE_LUA. The script itself is exercised against a
    real Redis in test_quota_lua_real_redis.py; this covers the Python around
    it, which is where the defect lived."""

    def __init__(self):
        self.store: dict[str, int | str] = {}

    def exists(self, key):
        return key in self.store

    def set(self, key, value, ex=None, nx=False, **_kw):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        return 1

    def eval(self, _script, _n, key, additional, limit):
        cur = int(self.store.get(key, 0))
        additional, limit = int(additional), int(limit)
        if limit > 0 and cur + additional > limit:
            return -1
        self.store[key] = str(cur + additional)
        return cur + additional


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(quota_svc, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def uploading_row(db, make_user):
    """A quota'd user with one committed `uploading` row - exactly the state
    `/api/uploads/init` leaves behind before tusd's hook runs."""
    owner = make_user(email="quota@test.local", role=UserRole.client)
    owner.quota_bytes = 10 * _GB
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(
        id=_FILE_ID,
        share_id=sh.id,
        original_filename="big.bin",
        mime_type="application/octet-stream",
        size_bytes=_SIZE,
        storage_path=None,
        state=FileState.uploading,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    db.commit()
    return owner, f


# --- the false refusal ------------------------------------------------------


def test_the_first_large_upload_is_not_refused(db, uploading_row, fake_redis):
    """6 GiB against a 10 GiB quota, on a cold counter. It fits, with 4 GiB to
    spare, and the UI already accepted it."""
    owner, f = uploading_row

    total = quota_svc.reserve_bytes(
        db, user=owner, additional_bytes=_SIZE, exclude_file_id=f.id
    )
    assert total == _SIZE, f"charged {total} for a {_SIZE}-byte file"


def test_the_seed_leaves_out_the_row_being_charged(db, uploading_row, fake_redis):
    """The mechanism, isolated: the seed itself must not contain the file whose
    bytes the caller is about to add."""
    owner, f = uploading_row

    seeded = quota_svc._initialize_from_db(db, owner.id, exclude_file_id=f.id)
    assert seeded == 0

    fake_redis.store.clear()
    assert quota_svc._initialize_from_db(db, owner.id) == _SIZE


def test_other_files_still_count(db, uploading_row, fake_redis, make_user):
    """The exclusion is one row, not a blanket skip - a second in-flight upload
    must still be charged against the quota."""
    owner, f = uploading_row
    other = File(
        id="00000000-0000-0000-0000-0000000005b6",
        share_id=f.share_id,
        original_filename="other.bin",
        mime_type="application/octet-stream",
        size_bytes=1 * _GB,
        storage_path=None,
        state=FileState.uploading,
        uploaded_by_id=owner.id,
    )
    db.add(other)
    db.commit()

    seeded = quota_svc._initialize_from_db(db, owner.id, exclude_file_id=f.id)
    assert seeded == 1 * _GB


def test_a_genuinely_oversized_upload_is_still_refused(db, uploading_row, fake_redis):
    """The control. Fixing the double-count must not stop the quota enforcing."""
    owner, f = uploading_row
    owner.quota_bytes = 4 * _GB
    db.commit()

    with pytest.raises(AppError) as exc:
        quota_svc.reserve_bytes(
            db, user=owner, additional_bytes=_SIZE, exclude_file_id=f.id
        )
    assert exc.value.code == "QUOTA_EXCEEDED"


# --- the bypass the refusal left behind -------------------------------------


def test_a_refused_reservation_does_not_leave_the_file_uncharged(
    db, uploading_row, fake_redis
):
    """`reserve_bytes_once` marks BEFORE it charges. When the charge raised, the
    marker stayed set - so the very next pre-create for the same file skipped
    the charge and the upload proceeded unmetered. A repeatable bypass, and the
    real mechanism behind "the retry succeeds"."""
    owner, f = uploading_row
    owner.quota_bytes = 4 * _GB
    db.commit()

    with pytest.raises(AppError):
        quota_svc.reserve_bytes_once(
            db, user=owner, additional_bytes=_SIZE, file_id=f.id
        )

    # Second attempt must be a real attempt, not a silent skip.
    with pytest.raises(AppError) as exc:
        quota_svc.reserve_bytes_once(
            db, user=owner, additional_bytes=_SIZE, file_id=f.id
        )
    assert exc.value.code == "QUOTA_EXCEEDED"


def test_a_successful_reservation_is_still_charged_only_once(
    db, uploading_row, fake_redis
):
    """What the marker is for. A tusd hook replay - which happens whenever the
    client loses the response - must not double-charge."""
    owner, f = uploading_row

    first = quota_svc.reserve_bytes_once(
        db, user=owner, additional_bytes=_SIZE, file_id=f.id
    )
    second = quota_svc.reserve_bytes_once(
        db, user=owner, additional_bytes=_SIZE, file_id=f.id
    )
    assert first == _SIZE
    assert second is None, "a replay re-charged the same file"
    assert int(fake_redis.store[quota_svc._key(owner.id)]) == _SIZE


# --- the docstring that caused it -------------------------------------------


def test_the_reconcile_worker_no_longer_claims_uploading_is_invisible():
    """It said the seed "only seeds from finalized files". `uploading` is in
    STORED_STATES. Believing that is what produced the double-charge."""
    import inspect

    from app.workers import quota_reconcile

    src = inspect.getsource(quota_reconcile)
    # The old sentence may survive as a QUOTED retraction - that is the point of
    # writing down what was wrong - but it must not still be asserted.
    if "only seeds from finalized files" in src:
        assert "used to say" in src, (
            "the reconcile docstring still asserts that in-flight uploading "
            "rows are invisible to the seed; they are in STORED_STATES"
        )
    assert FileState.uploading in quota_svc.STORED_STATES

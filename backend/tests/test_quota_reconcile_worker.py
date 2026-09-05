"""quota_reconcile sums every uploader in one grouped query and corrects only
the counters that drifted.

The hourly loop used to issue one SUM per user; with the grouped query a user
who has no stored files must still reconcile to 0, and a user whose counter
matches must be left alone.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.workers import quota_reconcile as mod


def _naive_future() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def _seed_file(db, owner, size: int, state: FileState) -> None:
    share = Share(
        created_by_id=owner.id,
        kind=ShareKind.outbound,
        expires_at=_naive_future(),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(
        File(
            share_id=share.id,
            original_filename="f.bin",
            mime_type="application/octet-stream",
            size_bytes=size,
            storage_path="/nowhere/f.bin",
            state=state,
            uploaded_by_id=owner.id,
        )
    )
    db.commit()


class _FakeRedis:
    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values

    def get(self, key: str):
        return self.values.get(key)


@pytest.mark.asyncio
async def test_grouped_sum_reconciles_drift_only(db, make_user, monkeypatch):
    alice = make_user(email="a@test.local", role=UserRole.employee)
    bob = make_user(email="b@test.local", role=UserRole.employee)
    make_user(email="c@test.local", role=UserRole.client)  # no files: checked, never fixed
    _seed_file(db, alice, 5_000_000, FileState.clean)
    _seed_file(db, alice, 3_000_000, FileState.uploading)  # STORED_STATES includes it
    _seed_file(db, alice, 1_000_000, FileState.deleted)  # not stored
    _seed_file(db, bob, 2_000_000, FileState.clean)

    fake = _FakeRedis(
        {
            mod._key(alice.id): "1",  # far below the 8 MB the DB holds -> fix
            mod._key(bob.id): "2000000",  # exact -> leave alone
            # carol has no files and no counter -> nothing to write
        }
    )
    writes: list[tuple] = []
    monkeypatch.setattr(mod, "get_redis", lambda: fake)
    monkeypatch.setattr(mod, "sync", lambda v: v)
    monkeypatch.setattr(mod, "eval_script", lambda *a: writes.append(a) or 1)
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await mod.quota_reconcile(None)

    assert result == {"checked": 3, "fixed": 1}
    assert len(writes) == 1
    _redis, _lua, _nkeys, key, expected, db_sum, had = writes[0]
    assert key == mod._key(alice.id)
    assert (expected, db_sum, had) == ("1", 8_000_000, "1")

"""`disk_check`'s object-store branch, which nothing ran.

Only two test files in the suite ever select the S3 backend
(`test_s3_routes.py`, `test_s3_backend.py`), and neither touches this cron. So
of the two `supports_disk_stats` consumers, the branch that MATTERS was
unreachable: `disk_check` is the only writer of `storage.critical_low` and there
is no admin control for it.

The failure it guards against is recorded in its own comment (audit 2026-07-30):
an instance that flipped the flag while on local storage and then moved to
object storage - the obvious response to a full disk - "would refuse every
upload with 507 forever, with a manual DB edit as the only way back".

No moto bucket is needed: the branch returns before touching storage at all.
"""
from __future__ import annotations

import pytest

from app.services import settings as settings_svc
from app.workers import disk_check as disk_check_mod

_K = settings_svc.Keys


@pytest.fixture
def object_store(monkeypatch):
    """Select the S3 backend. Teardown is conftest's autouse
    `_reset_storage_backend`; the reset here is for the SELECTION to take."""
    monkeypatch.setattr("app.config.settings.STORAGE_BACKEND", "s3")
    monkeypatch.setattr("app.config.settings.S3_BUCKET", "unused-by-this-branch")
    from app.services import storage_backend

    storage_backend.reset_storage_backend_cache()
    yield
    storage_backend.reset_storage_backend_cache()


@pytest.mark.asyncio
async def test_the_stale_critical_low_flag_is_cleared(db, object_store):
    """The recovery path. Without it, a local->S3 move strands the instance."""
    settings_svc.set_value(
        db, key=_K.STORAGE_CRITICAL_LOW, value="true", actor=None
    )
    db.commit()

    out = await disk_check_mod.disk_check(None)

    assert out["skipped"] is True
    assert out["cleared_flag"] is True
    assert settings_svc.get_bool(db, _K.STORAGE_CRITICAL_LOW, default=False) is False


@pytest.mark.asyncio
async def test_it_reports_not_clearing_when_the_flag_was_already_off(db, object_store):
    """The control: `cleared_flag` must mean "this run cleared it", not "the
    branch ran". An unconditional True would hide the recovery never happening."""
    out = await disk_check_mod.disk_check(None)
    assert out["skipped"] is True
    assert out["cleared_flag"] is False


@pytest.mark.asyncio
async def test_the_local_backend_still_takes_the_real_path(db, monkeypatch):
    """The other control: this must be the OBJECT-STORE branch, not a cron that
    skips unconditionally. On local storage it must reach get_disk_stats."""
    reached = {}

    def _stats(path):
        reached["path"] = path
        # Shape matters: get_disk_stats returns a dict, not an object.
        return {"total_bytes": 100, "free_bytes": 99, "used_bytes": 1,
                "percent_free": 99.0}

    monkeypatch.setattr(disk_check_mod.storage_svc, "get_disk_stats", _stats)
    # Same trick the existing disk_check tests use: make the dedup check
    # fail-open fast, since there is no Redis in this harness.
    monkeypatch.setattr(
        disk_check_mod, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    out = await disk_check_mod.disk_check(None)
    assert reached, "the local path never reached get_disk_stats"
    assert out.get("skipped") is not True

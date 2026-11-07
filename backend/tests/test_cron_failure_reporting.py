"""Every tracked cron that swallows an exception must report the run as failed.

`track_cron` records a run as a success unless the job raises or returns a dict
carrying CRON_FAILED_KEY (`cron_tracker.mark_failed`). Ten workers caught their
per-item errors, logged them and returned a plain dict - so a job could be
broken indefinitely (Redis unreachable for the quota reconcile, every expiry
warning failing to dispatch, every orphan unlink failing) while the
Scheduled-tasks page stayed green and no `cron_failed` audit row or ops alert
was ever written (audit 2026-11-07).

Pinned GENERICALLY: an AST scan over every `@track_cron` function. One whose
own body (nested functions excluded) has an `except` handler that does not
re-raise must call `mark_failed` or set CRON_FAILED_KEY somewhere. The
allowlist names the deliberate exceptions with their reason; a new swallowing
handler fails here until it is classified rather than silently inheriting the
green status.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.cron_run import CronRun, CronRunStatus
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services.cron_tracker import CRON_FAILED_KEY, mark_failed, track_cron

_APP = Path(__file__).resolve().parents[1] / "app"
_SCANNED = sorted(_APP.glob("workers/*.py")) + [_APP / "services" / "release_check.py"]

# (module stem, function name) -> why its swallowing handler is not a failure.
_ALLOWED: dict[tuple[str, str], str] = {
    ("drain_pending_update", "_drain_pending_update_tracked"): (
        "an unparseable deadline fires the update instead of wedging it forever: "
        "a fallback value, nothing failed"
    ),
}

_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(node: ast.AST):
    """Yield the nodes of `node`'s own body, never descending into a nested
    function, lambda or class - their handlers belong to them."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _NESTED):
            continue
        yield child
        yield from _own_nodes(child)


def _is_tracked(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
        if name == "track_cron":
            return True
    return False


def _tracked_functions():
    for path in _SCANNED:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_tracked(node):
                yield path.stem, node, ast.get_source_segment(path.read_text(), node) or ""


def _swallowing_handlers(fn: ast.AST) -> list[int]:
    """Line numbers of `except` handlers in `fn`'s own body that never raise."""
    out = []
    for node in _own_nodes(fn):
        if isinstance(node, ast.ExceptHandler) and not any(
            isinstance(n, ast.Raise) for n in _own_nodes(node)
        ):
            out.append(node.lineno)
    return out


def test_every_tracked_cron_that_swallows_an_error_reports_the_run_as_failed():
    seen = []
    violations = []
    for module, fn, source in _tracked_functions():
        seen.append((module, fn.name))
        swallowing = _swallowing_handlers(fn)
        if not swallowing or (module, fn.name) in _ALLOWED:
            continue
        if "mark_failed(" in source or "CRON_FAILED_KEY" in source:
            continue
        violations.append(f"{module}.{fn.name}: except at line(s) {swallowing} never raises")
    # The scan must have found the fleet, or a moved decorator would let it pass
    # vacuously.
    assert len(seen) >= 20, seen
    assert violations == [], "\n".join(violations)


def test_the_allowlist_names_only_functions_that_still_exist_and_still_swallow():
    tracked = {(m, fn.name): fn for m, fn, _ in _tracked_functions()}
    for key, reason in _ALLOWED.items():
        assert key in tracked, f"{key} is allowlisted but no longer a tracked cron ({reason})"
        assert _swallowing_handlers(tracked[key]), f"{key} no longer swallows; drop the entry"


@pytest.mark.asyncio
async def test_mark_failed_is_recorded_as_a_failed_run(db):
    @track_cron("test_job_marked_failed")
    async def _job(_ctx):
        return mark_failed({"done": 3}, "1 of 4 items failed: OSError: disk gone")

    out = await _job({})
    assert out["done"] == 3 and out[CRON_FAILED_KEY] is True

    db.expire_all()
    row = db.query(CronRun).filter(CronRun.job_name == "test_job_marked_failed").one()
    assert row.status == CronRunStatus.failure
    assert "disk gone" in (row.error_msg or "")


@pytest.mark.asyncio
async def test_quota_reconcile_reports_an_unreachable_redis_as_a_failed_run(db, monkeypatch):
    from app.workers import quota_reconcile as mod

    def _down():
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(mod, "get_redis", _down)
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    result = await mod.quota_reconcile({})
    assert result[CRON_FAILED_KEY] is True
    assert "redis unreachable" in result["error"]
    assert result["checked"] == 0

    db.expire_all()
    rows = db.query(CronRun).filter(CronRun.job_name == "quota_reconcile").all()
    assert rows and rows[-1].status == CronRunStatus.failure


@pytest.mark.asyncio
async def test_share_expiring_reports_a_warning_it_could_not_dispatch(make_user, db, monkeypatch):
    from app.workers.share_expiring import share_expiring_24h_warning

    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="Quarterly figures",
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None)
        + timedelta(hours=24, minutes=15),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    db.commit()

    def _boom(db, **kw):
        raise RuntimeError("template exploded")

    monkeypatch.setattr("app.services.notification.dispatch", _boom)
    monkeypatch.setattr("app.workers.share_expiring.SessionLocal", lambda: db)

    result = await share_expiring_24h_warning(None)
    # The idempotency marker still lands - the failure is REPORTED, not hidden
    # and not retried into a second mail storm.
    assert result["notified_shares"] == 1
    assert result[CRON_FAILED_KEY] is True
    assert "2 warning(s) not dispatched" in result["error"]
    assert "template exploded" in result["error"]


@pytest.mark.asyncio
async def test_ops_check_reports_an_alert_it_could_not_deliver(make_user, db, monkeypatch):
    from app.workers import ops_check as mod

    make_user(email="admin@test.local", role=UserRole.admin)

    async def _av_ok():
        return None

    monkeypatch.setattr(mod, "_check_av", _av_ok)
    monkeypatch.setattr(mod, "_check_redis", lambda: "Redis ping raised: boom")
    monkeypatch.setattr(mod, "_check_smtp", lambda _db: None)
    monkeypatch.setattr(mod, "_check_failing_crons", lambda _db: None)
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    def _boom(db, **kw):
        raise RuntimeError("bell is broken")

    monkeypatch.setattr("app.services.notification.dispatch", _boom)

    result = await mod.ops_check({})
    assert result["redis"] != "ok"
    assert result["dispatched"] == 0
    assert result[CRON_FAILED_KEY] is True
    assert "1 alert dispatch(es) failed" in result["error"]

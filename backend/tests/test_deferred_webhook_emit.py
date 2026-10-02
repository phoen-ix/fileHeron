"""Deferred side-effects must be able to do the work they were deferred for.

From audit #2. `run_after_commit` was introduced so a rolled-back transaction
could not deliver a ghost webhook for a change that never persisted. It fires
from SQLAlchemy's `after_commit` event - and inside that event the originating
session is in `committed` state and refuses to emit SQL:

    InvalidRequestError: This session is in 'committed' state; no further SQL
    can be emitted within this transaction.

`webhook.emit` starts with `db.query(Webhook)`. That query raised, emit's own
never-raise guard swallowed it, and every outbound webhook was silently dropped
- from the audit fan-out and from all three ops-alert paths. No
`webhook_deliveries` row is ever written by the emitter (the worker owns that
row), so the admin webhook page showed no delivery AND no failure. The
integration simply received nothing, forever.

It survived because the one test covering the audit fan-out replaced
`webhook_svc.emit` with a list-appending lambda - which emits no SQL, so it
could not reach the defect it was written to guard.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType
from app.models.webhook import Webhook
from app.services import webhook as webhook_svc
from app.services.audit import record_audit_event
from app.utils.crypto import encrypt_setting


@pytest.fixture
def collected(monkeypatch):
    """Collect at the queue boundary, so the whole real emit path runs."""
    jobs: list = []
    monkeypatch.setattr(
        webhook_svc.job_queue, "enqueue_many", lambda batch: jobs.extend(batch)
    )
    return jobs


def _active_wildcard(db) -> Webhook:
    wh = Webhook(
        name="ops",
        url="https://hook.example.com/x",
        secret_encrypted=encrypt_setting("s3cret"),
        event_types=["*"],
        active=True,
    )
    db.add(wh)
    db.flush()
    return wh


def test_an_audited_event_actually_reaches_the_webhook_queue(db, collected):
    """The headline case: a real subscription, the real emit, the real commit."""
    _active_wildcard(db)
    db.commit()

    record_audit_event(
        db, event_type=AuditEventType.share_created, target_type="share", target_id="s1"
    )
    db.commit()

    assert collected, (
        "the deferred emit ran inside after_commit, where the session cannot "
        "emit SQL - so the subscription query raised, emit swallowed it, and "
        "the webhook was dropped with no delivery row and no error surfaced"
    )
    assert collected[0][0] == "webhook_deliver"


def test_a_rolled_back_action_still_delivers_nothing(db, collected):
    """The property `run_after_commit` was added for, which the fix must keep:
    no ghost event for a change that never persisted."""
    _active_wildcard(db)
    db.commit()

    record_audit_event(
        db, event_type=AuditEventType.share_created, target_type="share", target_id="s2"
    )
    db.rollback()

    assert collected == []


def test_an_ops_alert_reaches_the_webhook_queue(db, make_user, collected, monkeypatch):
    """The same defect, on the path an operator relies on precisely when
    something is already wrong."""
    from app.models.user import UserRole
    from app.workers import ops_check as ops

    make_user(email="admin@test.local", role=UserRole.admin)
    _active_wildcard(db)
    db.commit()

    monkeypatch.setattr(ops, "dispatch", lambda *a, **kw: None)
    ops._alert_admins(db, reason="redis_unhealthy", detail="probe")
    db.commit()

    assert collected, "an ops alert fired and its webhook was dropped"


def test_the_originating_session_cannot_emit_sql_after_commit(db):
    """The control that gives the tests above their meaning - the platform
    constraint the deferred emit was written against. Kept as a test so a future
    `run_after_commit(db, lambda: db.query(...))` is a known-bad shape rather
    than a discovery."""
    from sqlalchemy.exc import InvalidRequestError

    from app.database import run_after_commit

    seen: dict = {}

    def _thunk() -> None:
        try:
            db.query(Webhook).count()
        except InvalidRequestError as e:
            seen["err"] = str(e)

    run_after_commit(db, _thunk)
    db.commit()
    assert "committed" in seen.get("err", ""), (
        "if this stops raising, run_after_commit thunks may query the "
        "originating session again and emit_after_commit's own session can go"
    )


def test_every_deferred_webhook_emit_uses_the_session_safe_helper():
    """Four call sites had the same bug because each wrote the same wrong line.
    Structural, so a fifth cannot."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"run_after_commit\(\s*\n?\s*db,(.{0,120})", src, re.S):
            if "webhook" in m.group(1):
                offenders.append(f"{path.name}: {m.group(1).strip()[:60]}")
    assert offenders == [], (
        "a webhook emit is deferred with the raw helper and will run against a "
        "committed session; use webhook.emit_after_commit"
    )

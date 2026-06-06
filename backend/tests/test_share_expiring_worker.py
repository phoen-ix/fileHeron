"""share_expiring_24h_warning worker - window correctness + idempotency."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.workers.share_expiring import share_expiring_24h_warning


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _make_share(db, sender, recipient_id: int, expires_at: datetime) -> Share:
    s = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="Quarterly figures",
        message=None,
        expires_at=expires_at,
        state=ShareState.active,
    )
    db.add(s)
    db.flush()
    db.add(ShareRecipient(share_id=s.id, recipient_user_id=recipient_id))
    db.commit()
    return s


@pytest.mark.asyncio
async def test_marks_expiring_notified_at_and_dispatches(
    make_user, db, monkeypatch
):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = _make_share(db, sender, recipient.id, _now() + timedelta(hours=24, minutes=15))

    dispatched = []
    monkeypatch.setattr(
        "app.services.notification.dispatch",
        lambda db, **kw: dispatched.append(kw) or None,
    )
    monkeypatch.setattr(
        "app.workers.share_expiring.SessionLocal", lambda: db
    )

    result = await share_expiring_24h_warning(None)
    assert result["notified_shares"] == 1
    # Sender + 1 user-recipient = 2 dispatches.
    assert len(dispatched) == 2

    db.expire_all()
    s_after = db.query(Share).filter(Share.id == share.id).one()
    assert s_after.expiring_notified_at is not None


@pytest.mark.asyncio
async def test_idempotent_via_expiring_notified_at(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    _make_share(db, sender, recipient.id, _now() + timedelta(hours=24, minutes=15))

    monkeypatch.setattr(
        "app.services.notification.dispatch",
        lambda db, **kw: None,
    )
    monkeypatch.setattr(
        "app.workers.share_expiring.SessionLocal", lambda: db
    )
    r1 = await share_expiring_24h_warning(None)
    r2 = await share_expiring_24h_warning(None)
    assert r1["notified_shares"] == 1
    assert r2["notified_shares"] == 0  # marked, won't pick up again


@pytest.mark.asyncio
async def test_outside_window_skipped(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    # 3 days from now - way outside the (24h, 25h) window.
    _make_share(db, sender, recipient.id, _now() + timedelta(days=3))

    monkeypatch.setattr(
        "app.services.notification.dispatch",
        lambda db, **kw: None,
    )
    monkeypatch.setattr(
        "app.workers.share_expiring.SessionLocal", lambda: db
    )
    r = await share_expiring_24h_warning(None)
    assert r["notified_shares"] == 0


@pytest.mark.asyncio
async def test_revoked_shares_skipped(make_user, db, monkeypatch):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    s = _make_share(db, sender, recipient.id, _now() + timedelta(hours=24, minutes=20))
    s.state = ShareState.revoked
    db.commit()

    monkeypatch.setattr(
        "app.services.notification.dispatch",
        lambda db, **kw: None,
    )
    monkeypatch.setattr(
        "app.workers.share_expiring.SessionLocal", lambda: db
    )
    r = await share_expiring_24h_warning(None)
    assert r["notified_shares"] == 0

"""bulk-expire's per-item INTERNAL_ERROR must not carry the exception text.

The generic `except Exception` branch put `str(e)[:200]` into the response,
so a database or filesystem error - paths, SQL, driver messages - went
straight into the SPA's bulk toast. The global 500 handler already keeps such
text out of the error envelope; this per-item envelope leaked it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import share as share_svc

PW = "Pass12345678!"
SECRET = "/data/files/2026/09/deadbeef.bin: disk on fire"


@pytest.mark.asyncio
async def test_unexpected_failures_are_reported_without_the_exception_text(
    make_user, db, client, login_as, monkeypatch
):
    # Admin: no connection to the recipient required for the share to exist.
    owner = make_user(email="own@test.local", role=UserRole.admin, password=PW)
    recipient = make_user(email="rec@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=owner,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        subject="doomed",
    )
    db.commit()
    token, _ = await login_as("own@test.local", PW)

    def _boom(*a, **k):
        raise RuntimeError(SECRET)

    from app.routers import shares as shares_router

    monkeypatch.setattr(shares_router.share_svc, "expire_share_now", _boom)

    r = await client.post(
        "/api/shares/bulk-expire",
        json={"share_ids": [share.id]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expired"] == []
    assert len(body["failed"]) == 1
    assert body["failed"][0]["code"] == "INTERNAL_ERROR"
    assert SECRET not in r.text
    assert "disk on fire" not in r.text

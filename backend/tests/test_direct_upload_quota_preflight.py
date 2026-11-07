"""A user already at their quota is refused BEFORE the direct-upload body is
read. /api/uploads/init had this pre-flight; /api/uploads/direct reserved only
after the read, so an over-quota user streamed the whole file (up to the admin
cap) to be told no (audit 2026-11-07)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File
from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import share as share_svc


@pytest.mark.asyncio
async def test_direct_upload_refuses_an_at_quota_user_before_reserving(
    make_user, db, client, login_as, monkeypatch
):
    sender = make_user(email="up@test.local", role=UserRole.admin, password="Pass12345678!")
    sender.quota_bytes = 1000
    db.commit()
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    )
    db.commit()
    token, _ = await login_as("up@test.local", "Pass12345678!")

    from app.routers import uploads as uploads_router

    monkeypatch.setattr(uploads_router.quota_svc, "used_bytes", lambda *, user_id: 1000)

    def _never(*_a, **_kw):
        raise AssertionError("reserve_bytes must not run for an upload refused up front")

    monkeypatch.setattr(uploads_router.quota_svc, "reserve_bytes", _never)

    resp = await client.post(
        "/api/uploads/direct",
        data={"share_id": share.id},
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["code"] == "QUOTA_EXCEEDED"
    assert resp.json()["details"] == {"quota_bytes": 1000}
    assert db.query(File).count() == 0, "no row, no reservation, no bytes"

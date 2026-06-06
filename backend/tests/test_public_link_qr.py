"""Public-link responses carry an inline SVG QR of the public URL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.public_link import PublicLink
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.utils.crypto import sha256_hex


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _seed_share(db, sender, recipient) -> Share:
    share = Share(
        created_by_id=sender.id,
        kind=ShareKind.outbound,
        subject="t",
        message=None,
        expires_at=_now_naive() + timedelta(hours=1),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))
    db.commit()
    return share


@pytest.mark.asyncio
async def test_public_link_create_and_get_include_qr(make_user, db, client, login_as):
    sender = make_user(email="s@test.local", role=UserRole.admin, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share(db, sender, recipient)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    create = await client.post(
        f"/api/shares/{share.id}/public-link",
        json={"notify_on_download": False},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    qr = create.json()["qr_svg"]
    assert qr and "<svg" in qr

    get = await client.get(f"/api/shares/{share.id}/public-link", headers=headers)
    assert get.status_code == 200
    assert "<svg" in (get.json()["qr_svg"] or "")


@pytest.mark.asyncio
async def test_legacy_row_has_null_qr(make_user, db, client, login_as):
    sender = make_user(email="s@test.local", role=UserRole.admin, password="Pass12345678!")
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share(db, sender, recipient)
    db.add(
        PublicLink(
            share_id=share.id,
            token_hash=sha256_hex("legacy-fake-plaintext"),
            token_encrypted=None,
            password_hash=None,
            download_limit=None,
            downloads_remaining=None,
            notify_on_download=False,
            created_by_id=sender.id,
        )
    )
    db.commit()
    token, _ = await login_as("s@test.local", "Pass12345678!")
    resp = await client.get(
        f"/api/shares/{share.id}/public-link",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] is None
    assert body["qr_svg"] is None

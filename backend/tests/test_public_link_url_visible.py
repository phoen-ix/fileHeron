"""GET /api/shares/{id}/public-link returns the URL for owner display.

Bug: the URL was treated like a token — shown ONCE on creation and
hash-only thereafter. Owners who lost the URL had to revoke + recreate
to recover it. New behaviour: encrypted-at-rest token, decrypted for
the owner on the share detail page.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.public_link import PublicLink
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import public_link as public_link_svc
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
async def test_get_public_link_returns_url_after_create(
    make_user, db, client, login_as
):
    sender = make_user(
        email="s@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share(db, sender, recipient)
    token, _ = await login_as("s@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        f"/api/shares/{share.id}/public-link",
        json={"notify_on_download": False},
        headers=headers,
    )
    assert create_resp.status_code == 201
    create_url = create_resp.json()["url"]
    assert create_url.startswith("http")

    # Re-fetching the metadata returns the same URL — no need to
    # revoke + recreate.
    get_resp = await client.get(
        f"/api/shares/{share.id}/public-link",
        headers=headers,
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["url"] == create_url
    assert body["has_password"] is False


@pytest.mark.asyncio
async def test_get_public_link_url_is_null_for_legacy_rows(
    make_user, db, client, login_as
):
    """A row written before the token_encrypted column shipped has
    that column NULL — the GET response surfaces url=None and the
    SPA renders the legacy hint."""
    sender = make_user(
        email="s@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share(db, sender, recipient)

    # Hand-built row mimicking pre-migration state: token_hash present,
    # token_encrypted is NULL.
    legacy = PublicLink(
        share_id=share.id,
        token_hash=sha256_hex("legacy-fake-plaintext"),
        token_encrypted=None,
        password_hash=None,
        download_limit=None,
        downloads_remaining=None,
        notify_on_download=False,
        created_by_id=sender.id,
    )
    db.add(legacy)
    db.commit()

    token, _ = await login_as("s@test.local", "Pass12345678!")
    resp = await client.get(
        f"/api/shares/{share.id}/public-link",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] is None
    assert body["id"] == legacy.id


def test_hash_lookup_still_works_after_encrypted_column(make_user, db):
    """Sanity: get_link_by_token uses the hash index. Adding the
    encrypted column shouldn't perturb that lookup."""
    sender = make_user(email="s@test.local", role=UserRole.admin)
    recipient = make_user(email="r@test.local", role=UserRole.client)
    share = _seed_share(db, sender, recipient)

    created = public_link_svc.create_link(
        db,
        actor=sender,
        share=share,
        password=None,
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()

    looked_up = public_link_svc.get_link_by_token(db, created.plaintext_token)
    assert looked_up is not None
    assert looked_up.id == created.record.id
    # Encrypted column is populated, but the lookup didn't need it.
    assert looked_up.token_encrypted is not None

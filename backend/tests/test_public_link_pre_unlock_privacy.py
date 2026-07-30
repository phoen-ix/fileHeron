"""A locked public link must not disclose what it is protecting.

`/api/public/{token}` computed `unlocked` and reported it, but returned the
subject, the sender's message and the full file list (names, MIME types, sizes)
regardless. So the password gated the bytes while anyone holding the URL could
read the metadata - which is frequently the sensitive part on its own
(audit 2026-07-30).

Expiry, requires_password and downloads_remaining stay visible: the unlock
screen needs them to render.
"""
from __future__ import annotations

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import public_link as public_link_svc


@pytest.fixture
def locked_link(db, make_user, tmp_path):
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    share = Share(
        created_by_id=owner.id,
        kind=ShareKind.outbound,
        state=ShareState.active,
        subject="Q3 layoffs list",
        message="as discussed",
    )
    db.add(share)
    db.commit()
    p = tmp_path / "secret.xlsx"
    p.write_bytes(b"x" * 10)
    db.add(
        File(
            share_id=share.id,
            original_filename="Q3-layoffs-list.xlsx",
            mime_type="application/vnd.ms-excel",
            size_bytes=10,
            storage_path=str(p),
            state=FileState.clean,
            uploaded_by_id=owner.id,
        )
    )
    db.commit()
    created = public_link_svc.create_link(
        db, actor=owner, share=share, password="correct horse battery staple",
        download_limit=None, notify_on_download=False,
    )
    db.commit()
    return created.plaintext_token


@pytest.mark.asyncio
async def test_locked_link_hides_subject_message_and_files(client, locked_link):
    resp = await client.get(f"/api/public/{locked_link}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["requires_password"] is True
    assert body["unlocked"] is False
    # The disclosure itself.
    assert body["subject"] is None
    assert body["message"] is None
    assert body["files"] == []
    # And nothing sensitive leaked anywhere else in the payload.
    assert "layoffs" not in resp.text.lower()


@pytest.mark.asyncio
async def test_locked_link_still_renders_the_unlock_screen(client, locked_link):
    """Control: over-hiding would break the page that asks for the password."""
    body = (await client.get(f"/api/public/{locked_link}")).json()
    assert body["requires_password"] is True
    assert "expires_at" in body
    assert "downloads_remaining" in body


@pytest.mark.asyncio
async def test_link_without_a_password_still_shows_everything(
    client, db, make_user, tmp_path
):
    """Control: a link with no password was never gated and must not become so -
    that is the normal sharing case."""
    owner = make_user(email="owner2@test.local", role=UserRole.employee)
    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound,
        state=ShareState.active, subject="Public deck",
    )
    db.add(share)
    db.commit()
    p = tmp_path / "deck.pdf"
    p.write_bytes(b"y" * 5)
    db.add(
        File(
            share_id=share.id, original_filename="deck.pdf", mime_type="application/pdf",
            size_bytes=5, storage_path=str(p), state=FileState.clean,
            uploaded_by_id=owner.id,
        )
    )
    db.commit()
    created = public_link_svc.create_link(
        db, actor=owner, share=share, password=None,
        download_limit=None, notify_on_download=False,
    )
    db.commit()

    body = (await client.get(f"/api/public/{created.plaintext_token}")).json()
    assert body["subject"] == "Public deck"
    assert [f["original_filename"] for f in body["files"]] == ["deck.pdf"]

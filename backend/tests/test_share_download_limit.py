"""v1.1.0 per-share download limit.

Mirrors the public-link counter for authenticated shares. Tests:
- create with download_limit persists both columns
- create with download_limit=0 → 422 (Pydantic gt=0)
- atomic decrement (try_decrement_share_counter) returns True until exhausted
- end-to-end download: succeeds N times, 410 on N+1
- /download-url pre-flight refuses when remaining=0
- PATCH raises limit → remaining grows by delta
- PATCH lowers limit below used → remaining clamps to 0
- PATCH with download_limit_clear=true → both NULL
- unlimited shares (NULL limit) keep working without decrement
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import share as share_svc


def _future_iso(days: int = 7) -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    ).isoformat()


def _setup_share_with_file(
    make_user, db, monkeypatch, *, download_limit: int | None
) -> tuple:
    """Build a sender + recipient + share + clean file pointing at a real
    on-disk byte string. Mirrors test_download_token.py::_setup_share_with_clean_file."""
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    recipient = make_user(
        email="rec@test.local", role=UserRole.client, password="Pass12345678!"
    )

    storage_dir = tempfile.mkdtemp(prefix="fh-test-storage-")
    monkeypatch.setattr(
        __import__("app.config", fromlist=["settings"]).settings,
        "STORAGE_ROOT",
        storage_dir,
    )

    share = Share(
        kind=ShareKind.outbound,
        state=ShareState.active,
        created_by_id=sender.id,
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(days=1)).replace(
            tzinfo=None
        ),
        download_limit=download_limit,
        downloads_remaining=download_limit,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=recipient.id))

    abs_path = Path(storage_dir) / "f.bin"
    abs_path.write_bytes(b"file bytes for download")

    file_row = File(
        id="00000000-0000-0000-0000-000000000bbb",
        share_id=share.id,
        original_filename="hello.txt",
        mime_type="text/plain",
        size_bytes=23,
        storage_path=str(abs_path),
        state=FileState.clean,
        uploaded_by_id=sender.id,
    )
    db.add(file_row)
    db.commit()
    return sender, recipient, share, file_row


# ---------------------------------------------------------------------------
# Service-level: decrement helper + update_share_limit semantics.
# ---------------------------------------------------------------------------


def test_try_decrement_succeeds_then_exhausts(make_user, db, monkeypatch):
    _, _, share, _ = _setup_share_with_file(make_user, db, monkeypatch, download_limit=3)
    assert share.downloads_remaining == 3
    for _ in range(3):
        assert share_svc.try_decrement_share_counter(db, share=share) is True
    db.refresh(share)
    assert share.downloads_remaining == 0
    # Fourth attempt fails — atomic UPDATE rowcount==0.
    assert share_svc.try_decrement_share_counter(db, share=share) is False


def test_update_share_limit_raises_grows_remaining(make_user, db, monkeypatch):
    sender, _, share, _ = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=3
    )
    # Consume one download so used=1, remaining=2.
    share_svc.try_decrement_share_counter(db, share=share)
    db.refresh(share)
    assert share.downloads_remaining == 2

    share_svc.update_share_limit(
        db, user=sender, share=share, new_limit=10, clear=False
    )
    db.commit()
    db.refresh(share)
    assert share.download_limit == 10
    # used was 1, so remaining = max(0, 10-1) = 9.
    assert share.downloads_remaining == 9


def test_update_share_limit_lower_than_used_clamps(make_user, db, monkeypatch):
    sender, _, share, _ = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=10
    )
    # Consume 5 → used=5, remaining=5.
    for _ in range(5):
        share_svc.try_decrement_share_counter(db, share=share)
    db.refresh(share)
    assert share.downloads_remaining == 5

    # Lower limit to 3 → used=5 > new_limit=3 → remaining clamps to 0.
    share_svc.update_share_limit(
        db, user=sender, share=share, new_limit=3, clear=False
    )
    db.commit()
    db.refresh(share)
    assert share.download_limit == 3
    assert share.downloads_remaining == 0


def test_update_share_limit_clear_sets_both_null(make_user, db, monkeypatch):
    sender, _, share, _ = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=5
    )
    share_svc.update_share_limit(
        db, user=sender, share=share, new_limit=None, clear=True
    )
    db.commit()
    db.refresh(share)
    assert share.download_limit is None
    assert share.downloads_remaining is None


def test_update_share_limit_refuses_on_revoked(make_user, db, monkeypatch):
    sender, _, share, _ = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=5
    )
    share.state = ShareState.revoked
    db.commit()
    from app.middleware.errors import AppError
    with pytest.raises(AppError) as exc:
        share_svc.update_share_limit(
            db, user=sender, share=share, new_limit=10, clear=False
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "SHARE_NOT_ACTIVE"


# ---------------------------------------------------------------------------
# HTTP-level: POST /api/shares with download_limit, /download enforcement.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_share_persists_download_limit(
    make_user, db, client, login_as
):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _future_iso(),
            "subject": "limited",
            "download_limit": 3,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["download_limit"] == 3
    assert body["downloads_remaining"] == 3
    # DB row mirrors the response.
    db.expire_all()
    row = db.query(Share).filter(Share.id == body["id"]).one()
    assert row.download_limit == 3
    assert row.downloads_remaining == 3


@pytest.mark.asyncio
async def test_create_share_zero_limit_rejected(make_user, db, client, login_as):
    make_user(email="hr@test.local", role=UserRole.admin, password="Pass12345678!")
    rec = make_user(email="r@test.local", role=UserRole.client)
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/shares",
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [rec.id], "group_ids": []},
            "expires_at": _future_iso(),
            "download_limit": 0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_download_decrements_and_410_on_exhaust(
    make_user, db, client, login_as, monkeypatch
):
    sender, recipient, share, file_row = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=2
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Two downloads succeed.
    for _ in range(2):
        r = await client.get(f"/api/files/{file_row.id}/download", headers=headers)
        assert r.status_code == 200, r.text

    # Third → 410 SHARE_DOWNLOAD_LIMIT_REACHED.
    r = await client.get(f"/api/files/{file_row.id}/download", headers=headers)
    assert r.status_code == 410, r.text
    assert r.json()["code"] == "SHARE_DOWNLOAD_LIMIT_REACHED"

    # Counter is at 0 — confirms decrement happened.
    db.refresh(share)
    assert share.downloads_remaining == 0


@pytest.mark.asyncio
async def test_download_url_refuses_when_exhausted(
    make_user, db, client, login_as, monkeypatch
):
    sender, recipient, share, file_row = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=1
    )
    # Exhaust via the service layer (skips the URL endpoint).
    share_svc.try_decrement_share_counter(db, share=share)
    db.commit()

    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{file_row.id}/download-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 410
    assert r.json()["code"] == "SHARE_DOWNLOAD_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_unlimited_share_downloads_freely(
    make_user, db, client, login_as, monkeypatch
):
    sender, recipient, share, file_row = _setup_share_with_file(
        make_user, db, monkeypatch, download_limit=None
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # 5 downloads → all succeed. Counter stays None.
    for _ in range(5):
        r = await client.get(f"/api/files/{file_row.id}/download", headers=headers)
        assert r.status_code == 200
    db.refresh(share)
    assert share.download_limit is None
    assert share.downloads_remaining is None

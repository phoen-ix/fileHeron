"""Signed download URL — issuance, verification, end-to-end download."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.middleware.errors import AppError
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import download_token as download_token_svc

# ---- pure unit tests on the signing helpers -------------------------------


def test_issue_then_verify_roundtrip():
    token = download_token_svc.issue("file-abc", user_id=42, ttl_sec=60)
    assert download_token_svc.verify("file-abc", token) == 42


def test_verify_rejects_token_for_different_file():
    token = download_token_svc.issue("file-abc", user_id=42)
    with pytest.raises(AppError) as exc:
        download_token_svc.verify("file-OTHER", token)
    assert exc.value.code == "INVALID_DOWNLOAD_TOKEN"


def test_verify_rejects_expired_token():
    token = download_token_svc.issue("file-abc", user_id=42, ttl_sec=-1)
    with pytest.raises(AppError) as exc:
        download_token_svc.verify("file-abc", token)
    assert exc.value.code == "DOWNLOAD_TOKEN_EXPIRED"


def test_verify_rejects_garbage():
    for bad in ["", "notatoken", "1.2", "x.y.z", "1.notanumber.sig"]:
        with pytest.raises(AppError):
            download_token_svc.verify("file-abc", bad)


def test_verify_rejects_tampered_signature():
    token = download_token_svc.issue("file-abc", user_id=42)
    user_part, exp_part, sig = token.split(".")
    # Flip a bit in the signature (still valid base64url).
    tampered = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    with pytest.raises(AppError) as exc:
        download_token_svc.verify("file-abc", f"{user_part}.{exp_part}.{tampered}")
    assert exc.value.code == "INVALID_DOWNLOAD_TOKEN"


# ---- end-to-end: SPA flow (mint URL → navigate → file streams) ------------


def _setup_share_with_clean_file(make_user, db, monkeypatch):
    """Build a sender + recipient + share + clean file pointing at a
    real on-disk byte string. Returns (sender, recipient, share,
    file_row, on_disk_bytes)."""
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
    )
    db.add(share)
    db.flush()

    db.add(
        ShareRecipient(share_id=share.id, recipient_user_id=recipient.id)
    )

    abs_path = Path(storage_dir) / "f.bin"
    abs_path.write_bytes(b"hello, downloaded world")

    file_row = File(
        id="00000000-0000-0000-0000-000000000aaa",
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
    return sender, recipient, share, file_row, abs_path


@pytest.mark.asyncio
async def test_get_download_url_then_download(
    make_user, db, client, login_as, monkeypatch
):
    sender, recipient, share, file_row, abs_path = (
        _setup_share_with_clean_file(make_user, db, monkeypatch)
    )

    # Sender mints a signed URL via bearer.
    token, _ = await login_as("hr@test.local", "Pass12345678!")
    resp = await client.get(
        f"/api/files/{file_row.id}/download-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    assert url.startswith(f"/api/files/{file_row.id}/download?dt=")

    # Browser navigates — no Authorization header on this hop.
    resp = await client.get(url)
    assert resp.status_code == 200, resp.text
    assert resp.content == b"hello, downloaded world"


@pytest.mark.asyncio
async def test_download_with_token_for_wrong_file_rejected(
    make_user, db, client, login_as, monkeypatch
):
    """Token issued for file A must not work for file B."""
    sender, _rec, _share, file_row, _ = _setup_share_with_clean_file(
        make_user, db, monkeypatch
    )
    other_token = download_token_svc.issue(
        "00000000-0000-0000-0000-000000000bbb", user_id=sender.id
    )
    resp = await client.get(
        f"/api/files/{file_row.id}/download?dt={other_token}",
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_DOWNLOAD_TOKEN"


@pytest.mark.asyncio
async def test_download_url_refuses_unauthorized(
    make_user, db, client, login_as, monkeypatch
):
    """Bystander (not on the share) can't even mint a URL."""
    _sender, _rec, _share, file_row, _ = _setup_share_with_clean_file(
        make_user, db, monkeypatch
    )
    make_user(
        email="bystander@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )
    token, _ = await login_as("bystander@test.local", "Pass12345678!")
    resp = await client.get(
        f"/api/files/{file_row.id}/download-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_download_url_ttl_honors_setting_and_clamps(
    make_user, db, client, login_as, monkeypatch
):
    """The signed-URL TTL is admin-tunable (downloads.signed_url_ttl_sec) so a
    browser can resume an interrupted download within the window; the value is
    clamped to the registry bounds (30s floor)."""
    import time

    from app.services import settings as settings_svc

    _sender, _r, _s, file_row, _ = _setup_share_with_clean_file(
        make_user, db, monkeypatch
    )
    token_auth, _ = await login_as("hr@test.local", "Pass12345678!")
    hdr = {"Authorization": f"Bearer {token_auth}"}

    def _ttl_of(url: str, at: int) -> int:
        dt = url.split("dt=", 1)[1]
        return int(dt.split(".")[1]) - at

    # Default (env 900s).
    now = int(time.time())
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=hdr)
    assert 880 <= _ttl_of(r.json()["url"], now) <= 905

    # Admin override to 60s.
    settings_svc.set_value(
        db, key=settings_svc.Keys.DOWNLOAD_SIGNED_URL_TTL_SEC, value="60", actor=None
    )
    db.commit()
    now = int(time.time())
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=hdr)
    assert 50 <= _ttl_of(r.json()["url"], now) <= 65

    # Below the 30s floor → clamped to 30.
    settings_svc.set_value(
        db, key=settings_svc.Keys.DOWNLOAD_SIGNED_URL_TTL_SEC, value="5", actor=None
    )
    db.commit()
    now = int(time.time())
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=hdr)
    assert 25 <= _ttl_of(r.json()["url"], now) <= 35


@pytest.mark.asyncio
async def test_existing_bearer_path_still_works(
    make_user, db, client, login_as, monkeypatch
):
    """API/curl callers can still pass the bearer directly to /download
    without going through /download-url. (Used by scripted clients.)"""
    sender, _rec, _share, file_row, _ = _setup_share_with_clean_file(
        make_user, db, monkeypatch
    )
    token, _ = await login_as("hr@test.local", "Pass12345678!")
    resp = await client.get(
        f"/api/files/{file_row.id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"hello, downloaded world"

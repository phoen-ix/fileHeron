"""In-browser preview (v1.23.0).

Covers the preview service allowlist, the authed preview mint + inline serve
(safe Content-Type + nosniff/CSP), the AV/state + previewable + global-toggle
gates, the budget-bypass (preview never decrements but a spent budget refuses),
the public preview unlock gate, and the admin on/off toggle + audit.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import download_token as download_token_svc
from app.services import preview as preview_svc
from app.services import settings as settings_svc

# ---------------------------------------------------------------------------
# Pure preview-policy unit tests (the security allowlist).
# ---------------------------------------------------------------------------


def test_preview_kind_allowlist():
    assert preview_svc.preview_kind("image/png") == "image"
    assert preview_svc.preview_kind("IMAGE/JPEG") == "image"
    assert preview_svc.preview_kind("application/pdf") == "pdf"
    assert preview_svc.preview_kind("text/plain; charset=utf-8") == "text"
    assert preview_svc.preview_kind("text/markdown") == "text"
    # text/html IS text-kind (rendered as source, never executed)...
    assert preview_svc.preview_kind("text/html") == "text"
    # ...but SVG and arbitrary binaries are NOT previewable.
    assert preview_svc.preview_kind("image/svg+xml") is None
    assert preview_svc.preview_kind("application/zip") is None
    assert preview_svc.preview_kind("application/octet-stream") is None
    assert preview_svc.preview_kind(None) is None
    assert preview_svc.preview_kind("") is None


def test_safe_content_type_never_renders_html():
    # text of any flavour is pinned to text/plain so it shows as source.
    assert preview_svc.safe_content_type("text/html") == "text/plain; charset=utf-8"
    assert preview_svc.safe_content_type("text/csv") == "text/plain; charset=utf-8"
    assert preview_svc.safe_content_type("image/png") == "image/png"
    assert preview_svc.safe_content_type("application/pdf") == "application/pdf"


def test_security_headers_present():
    h = preview_svc.SECURITY_HEADERS
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in h["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# Shared setup — sender + recipient + active share + one file.
# ---------------------------------------------------------------------------


def _setup(
    make_user,
    db,
    monkeypatch,
    *,
    mime: str = "text/plain",
    state: FileState = FileState.clean,
    download_limit: int | None = None,
    body: bytes = b"hello preview bytes",
) -> tuple:
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    recipient = make_user(
        email="rec@test.local", role=UserRole.client, password="Pass12345678!"
    )
    storage_dir = tempfile.mkdtemp(prefix="fh-test-preview-")
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
    abs_path.write_bytes(body)
    file_row = File(
        id="00000000-0000-0000-0000-0000000prev1",
        share_id=share.id,
        original_filename="report.txt",
        mime_type=mime,
        size_bytes=len(body),
        storage_path=str(abs_path),
        state=state,
        uploaded_by_id=sender.id,
    )
    db.add(file_row)
    db.commit()
    return sender, recipient, share, file_row


# ---------------------------------------------------------------------------
# Authed preview: mint + inline serve.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_mint_then_inline_serve(make_user, db, client, login_as, monkeypatch):
    _, _, _, file_row = _setup(make_user, db, monkeypatch, mime="text/plain")
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.get(f"/api/files/{file_row.id}/preview-url", headers=headers)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert "/preview?dt=" in url

    r2 = await client.get(url)
    assert r2.status_code == 200, r2.text
    assert r2.content == b"hello preview bytes"
    assert r2.headers["content-disposition"].startswith("inline")
    assert r2.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in r2.headers
    assert r2.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_preview_unsupported_type_415(make_user, db, client, login_as, monkeypatch):
    _, _, _, file_row = _setup(make_user, db, monkeypatch, mime="application/zip")
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{file_row.id}/preview-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 415
    assert r.json()["code"] == "PREVIEW_UNSUPPORTED"


@pytest.mark.asyncio
async def test_preview_svg_refused(make_user, db, client, login_as, monkeypatch):
    """SVG can carry script — must never be inline-previewable."""
    _, _, _, file_row = _setup(make_user, db, monkeypatch, mime="image/svg+xml")
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{file_row.id}/preview-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_preview_ready_unscanned_425(make_user, db, client, login_as, monkeypatch):
    _, _, _, file_row = _setup(
        make_user, db, monkeypatch, mime="text/plain", state=FileState.ready_unscanned
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    r = await client.get(
        f"/api/files/{file_row.id}/preview-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 425
    assert r.json()["code"] == "SCAN_IN_PROGRESS"


@pytest.mark.asyncio
async def test_preview_disabled_403_on_mint_and_serve(
    make_user, db, client, login_as, monkeypatch
):
    sender, recipient, _, file_row = _setup(make_user, db, monkeypatch, mime="text/plain")
    settings_svc.set_value(
        db, key=settings_svc.Keys.FILE_PREVIEW_ENABLED, value="false", actor=sender
    )
    db.commit()
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.get(f"/api/files/{file_row.id}/preview-url", headers=headers)
    assert r.status_code == 403
    assert r.json()["code"] == "PREVIEW_DISABLED"

    # Even with a directly-minted valid token the serve endpoint refuses.
    dt = download_token_svc.issue(file_row.id, recipient.id, ttl_sec=300)
    r2 = await client.get(f"/api/files/{file_row.id}/preview?dt={dt}")
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_preview_does_not_consume_budget_but_respects_exhaustion(
    make_user, db, client, login_as, monkeypatch
):
    from app.services import share as share_svc

    _, _, share, file_row = _setup(
        make_user, db, monkeypatch, mime="text/plain", download_limit=1
    )
    token, _ = await login_as("rec@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Preview twice — budget untouched, no download_log rows.
    for _ in range(2):
        r = await client.get(f"/api/files/{file_row.id}/preview-url", headers=headers)
        assert r.status_code == 200, r.text
        r2 = await client.get(r.json()["url"])
        assert r2.status_code == 200
    db.refresh(share)
    assert share.downloads_remaining == 1
    assert db.query(DownloadLog).filter(DownloadLog.file_id == file_row.id).count() == 0

    # Once the budget is fully spent, preview is refused too.
    share_svc.try_decrement_share_counter(db, share=share)
    db.commit()
    r = await client.get(f"/api/files/{file_row.id}/preview-url", headers=headers)
    assert r.status_code == 410
    assert r.json()["code"] == "SHARE_DOWNLOAD_LIMIT_REACHED"


# ---------------------------------------------------------------------------
# Public (anonymous) preview.
# ---------------------------------------------------------------------------


def _public_link(db, *, share, sender, password=None, download_limit=None):
    from app.services import public_link as public_link_svc

    created = public_link_svc.create_link(
        db,
        actor=sender,
        share=share,
        password=password,
        download_limit=download_limit,
        notify_on_download=False,
    )
    db.commit()
    return created.plaintext_token


@pytest.mark.asyncio
async def test_public_landing_exposes_preview_flag(make_user, db, client, monkeypatch):
    sender, _, share, _ = _setup(make_user, db, monkeypatch, mime="text/plain")
    token = _public_link(db, share=share, sender=sender)
    r = await client.get(f"/api/public/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["preview_enabled"] is True


@pytest.mark.asyncio
async def test_public_preview_serves_inline_without_decrement(
    make_user, db, client, monkeypatch
):
    sender, _, share, file_row = _setup(
        make_user, db, monkeypatch, mime="text/plain", download_limit=2
    )
    token = _public_link(db, share=share, sender=sender, download_limit=2)
    db.refresh(share)

    from app.models.public_link import PublicLink

    link = db.query(PublicLink).filter(PublicLink.share_id == share.id).one()
    before = link.downloads_remaining

    r = await client.get(f"/api/public/{token}/files/{file_row.id}/preview")
    assert r.status_code == 200, r.text
    assert r.content == b"hello preview bytes"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"

    db.refresh(link)
    assert link.downloads_remaining == before  # preview didn't consume a download
    assert db.query(DownloadLog).filter(DownloadLog.file_id == file_row.id).count() == 0


@pytest.mark.asyncio
async def test_public_preview_requires_unlock(make_user, db, client, monkeypatch):
    sender, _, share, file_row = _setup(make_user, db, monkeypatch, mime="text/plain")
    token = _public_link(db, share=share, sender=sender, password="correct horse")
    # No unlock cookie → 401.
    r = await client.get(f"/api/public/{token}/files/{file_row.id}/preview")
    assert r.status_code == 401
    assert r.json()["code"] == "UNLOCK_REQUIRED"


# ---------------------------------------------------------------------------
# Admin toggle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_toggle_get_put_audit_and_me(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Default is on.
    r = await client.get("/api/admin/settings/file-preview", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    # Turn it off.
    r = await client.put(
        "/api/admin/settings/file-preview", json={"enabled": False}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    r = await client.get("/api/admin/settings/file-preview", headers=headers)
    assert r.json()["enabled"] is False

    # /me reflects the flag.
    r = await client.get("/api/account/me", headers=headers)
    assert r.json()["file_preview_enabled"] is False

    # Audit row written.
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.file_preview_toggled.value)
        .count()
        >= 1
    )

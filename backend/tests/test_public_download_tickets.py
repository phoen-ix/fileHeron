"""Only a click on the public page pays for a download.

On 2026-11-20 a recipient's Microsoft 365 rendered the mailed `/d/<token>` page
and fetched both download URLs it linked to, 44 s after the mail went out. Each
was a plain GET that charged the link: the two-download budget was gone before
she saw the mail, and the owner was told she had downloaded. Mail link scanners
fetch every URL a mailed page links to, so the page now links to no download: a
click POSTs `/download-ticket` and only then navigates, and the charging path
refuses a request without a valid ticket for that link and target.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.notification import Notification
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import public_link as public_link_svc
from tests._public_link_helpers import public_download_url

DATA = b"0123456789" * 32


class _FakeRedis:
    """Enough of Redis for the paid mark."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def zadd(self, *a, **k):
        return 1

    def zrem(self, *a, **k):
        return 1


@pytest.fixture
def link(db, make_user, tmp_path, monkeypatch):
    """A two-download link over one clean file; the owner wants a notice per
    download - the notice the scanner's fetch sent."""
    from app.services import transfer_activity

    fake = _FakeRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "payload.bin"
    path.write_bytes(DATA)
    f = File(
        id="00000000-0000-0000-0000-000000ticket", share_id=sh.id,
        original_filename="payload.bin", mime_type="application/octet-stream",
        size_bytes=len(DATA), storage_path=str(path),
        state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    created = public_link_svc.create_link(
        db, share=sh, actor=owner, password=None, download_limit=2, notify_on_download=True
    )
    db.commit()
    return f, sh, created.record, created.plaintext_token, owner


def _charged_anything(db, link_row, owner) -> list[str]:
    db.refresh(link_row)
    found = []
    if link_row.downloads_remaining != 2:
        found.append(f"downloads_remaining={link_row.downloads_remaining}")
    if db.query(DownloadLog).count():
        found.append("download_log")
    charged = {
        AuditEventType.public_link_consumed,
        AuditEventType.file_downloaded,
        AuditEventType.share_downloaded,
    }
    if db.query(AuditLog).filter(AuditLog.event_type.in_(charged)).count():
        found.append("audit")
    if db.query(Notification).filter(Notification.user_id == owner.id).count():
        found.append("owner notice")
    return found


@pytest.mark.asyncio
async def test_a_plain_get_is_refused_and_charges_nothing(client, db, link):
    """Exactly what the scanner sent: the two URLs, no ticket, no Referer."""
    f, _, row, token, owner = link
    for url in (f"/api/public/{token}/files/{f.id}/download", f"/api/public/{token}/download-zip"):
        r = await client.get(url)
        assert r.status_code == 403, (url, r.text)
        assert r.json()["code"] == "DOWNLOAD_TICKET_REQUIRED"
    assert _charged_anything(db, row, owner) == []


@pytest.mark.asyncio
async def test_the_click_mints_for_free_and_the_url_pays_once(client, db, link):
    f, _, row, token, owner = link
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": f.id})
    assert r.status_code == 200, r.text
    file_url = r.json()["url"]
    assert file_url.startswith(f"/api/public/{token}/files/{f.id}/download?t=")
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": None})
    zip_url = r.json()["url"]
    assert zip_url.startswith(f"/api/public/{token}/download-zip?t=")
    assert _charged_anything(db, row, owner) == [], "minting must not charge"

    got = await client.get(file_url)
    assert (got.status_code, got.content) == (200, DATA)
    db.refresh(row)
    assert row.downloads_remaining == 1
    assert (await client.get(zip_url)).status_code == 200
    db.refresh(row)
    assert row.downloads_remaining == 0


@pytest.mark.asyncio
async def test_a_ticket_opens_only_its_own_target(client, db, link):
    f, _, _, token, _ = link
    file_ticket = public_download_url(db, token, f.id).split("?t=", 1)[1]
    zip_ticket = public_download_url(db, token).split("?t=", 1)[1]
    r = await client.get(f"/api/public/{token}/download-zip?t={file_ticket}")
    assert r.status_code == 403
    r = await client.get(f"/api/public/{token}/files/{f.id}/download?t={zip_ticket}")
    assert r.status_code == 403


@pytest.mark.parametrize("bad", ["", "abc", "1.x", "99999999999.forged"])
@pytest.mark.asyncio
async def test_a_malformed_or_forged_ticket_is_refused(client, db, link, bad):
    f, _, row, token, owner = link
    r = await client.get(f"/api/public/{token}/files/{f.id}/download?t={bad}")
    assert r.status_code == 403
    assert _charged_anything(db, row, owner) == []


@pytest.mark.asyncio
async def test_an_expired_ticket_is_refused(client, db, link):
    f, _, row, token, _ = link
    expired = public_link_svc.issue_download_ticket(row.id, f.id, -1)
    r = await client.get(f"/api/public/{token}/files/{f.id}/download?t={expired}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_recreated_link_refuses_the_old_links_tickets(client, db, link):
    f, sh, row, token, owner = link
    old_ticket = public_download_url(db, token, f.id).split("?t=", 1)[1]
    public_link_svc.revoke(db, actor=owner, link=row)
    db.commit()
    new = public_link_svc.create_link(
        db, share=sh, actor=owner, password=None, download_limit=2, notify_on_download=False
    )
    db.commit()
    r = await client.get(
        f"/api/public/{new.plaintext_token}/files/{f.id}/download?t={old_ticket}"
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_desktop_probe_and_a_paid_resume_need_no_ticket(client, db, link):
    """Only a request that would be CHARGED needs a ticket, so a long download
    that drops after its ticket expired still resumes."""
    f, _, row, token, _ = link
    bare = f"/api/public/{token}/files/{f.id}/download"
    r = await client.get(bare, headers={"Range": "bytes=1-1"})
    assert r.status_code == 206
    db.refresh(row)
    assert row.downloads_remaining == 2

    assert (await client.get(public_download_url(db, token, f.id))).status_code == 200
    r = await client.get(bare, headers={"Range": "bytes=10-"})
    assert (r.status_code, r.content) == (206, DATA[10:])
    db.refresh(row)
    assert row.downloads_remaining == 1


@pytest.mark.asyncio
async def test_the_mint_says_why_on_the_page(client, db, link):
    """The refusals the old `<a href>` could only show as a failed download."""
    f, _, row, token, _ = link
    f.state = FileState.ready_unscanned
    db.commit()
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": f.id})
    assert (r.status_code, r.json()["code"]) == (425, "SCAN_IN_PROGRESS")
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": None})
    assert (r.status_code, r.json()["code"]) == (400, "NO_DOWNLOADABLE_FILES")

    row.downloads_remaining = 0
    db.commit()
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": f.id})
    assert (r.status_code, r.json()["code"]) == (410, "PUBLIC_LINK_EXHAUSTED")


@pytest.mark.asyncio
async def test_the_mint_needs_the_unlock_first(client, db, make_user, tmp_path):
    owner = make_user(email="pw-owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "p.bin"
    path.write_bytes(DATA)
    f = File(
        id="00000000-0000-0000-0000-00000000tkpw", share_id=sh.id,
        original_filename="p.bin", mime_type="application/octet-stream",
        size_bytes=len(DATA), storage_path=str(path),
        state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    created = public_link_svc.create_link(
        db, share=sh, actor=owner, password="Correct-Horse-9", download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    token = created.plaintext_token
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": f.id})
    assert (r.status_code, r.json()["code"]) == (401, "UNLOCK_REQUIRED")

    r = await client.post(f"/api/public/{token}/unlock", json={"password": "Correct-Horse-9"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/public/{token}/download-ticket", json={"file_id": f.id})
    assert r.status_code == 200, r.text
    got = await client.get(r.json()["url"])
    assert (got.status_code, got.content) == (200, DATA)

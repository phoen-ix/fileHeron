"""Three ways the download budget and the download record could still be
walked past, from audit #2.

1. `is_metadata_probe` bounded the LENGTH of a free ranged read but not its
   OFFSET, so `bytes=0-0`, `bytes=1-1`, `bytes=2-2` ... reconstructed a whole
   file for free. On the ANONYMOUS public-link route that meant an unlimited,
   unlogged, unaudited, un-notified extraction while `downloads_remaining`
   never moved. The justification in the docstring - "one authenticated,
   rate-limited round trip per byte" - was true of the authenticated route and
   false of the one that is reachable by anyone holding a URL.

2. The authenticated bulk-ZIP corroborated a resume with "a download_log row
   for this user and files[0] inside the credit window". That is evidence of a
   FILE download, not of an archive transfer in progress: a recipient who spent
   the share's last download on one member could then send `Range: bytes=1-`
   at the ZIP and receive the complete archive the plain request had just
   answered 410 to. The public route was already keyed on link + ETag; this one
   was not keyed on anything the caller had actually paid for.

3. The same route recorded NOTHING for a `pending_approval` share - no
   download_log row, no audit entry - while the single-file route was fixed to
   record exactly that in v2.6.0. An approver could take every file in a share
   they are not a recipient of, repeatedly, and leave no trace for the sender
   or an investigator.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import settings as settings_svc

PW = "Pass12345678!"
PAYLOAD = b"".join(bytes([i % 251]) for i in range(600))


class _FakeRedis:
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
def redis_stub(monkeypatch):
    from app import redis_client
    from app.services import transfer_activity

    fake = _FakeRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


def _add_file(db, share, owner, tmp_path, name, data, idx):
    path = tmp_path / name
    path.write_bytes(data)
    f = File(
        id=f"00000000-0000-0000-0000-0000000zip{idx:02d}",
        share_id=share.id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=len(data),
        storage_path=str(path),
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    return f


# --- (1) the probe exemption cannot be aimed --------------------------------


@pytest.fixture
def public_one_download(db, make_user, tmp_path):
    from app.services import public_link as public_link_svc

    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = _add_file(db, sh, owner, tmp_path, "contract.pdf", PAYLOAD, 0)
    created = public_link_svc.create_link(
        db, share=sh, actor=owner, password=None, download_limit=1, notify_on_download=False
    )
    db.commit()
    return owner, sh, f, created.record, created.plaintext_token


@pytest.mark.asyncio
async def test_byte_by_byte_ranges_cannot_extract_a_file_for_free(
    client, db, public_one_download, redis_stub
):
    """The headline: anonymous, unlimited, UNMETERED extraction.

    Reconstructing the file is not itself the defect - one honest download
    obtains the same bytes. The defect is doing it without the budget moving,
    without a download_log row and without the owner notification, so the owner
    still sees a link with its one download intact. The walk must cost exactly
    what a download costs."""
    owner, sh, f, link, token = public_one_download
    url = f"/api/public/{token}/files/{f.id}/download"

    collected = bytearray()
    for i in range(len(PAYLOAD)):
        r = await client.get(url, headers={"Range": f"bytes={i}-{i}"})
        if r.status_code not in (200, 206):
            break
        collected.extend(r.content)

    db.refresh(link)
    assert link.downloads_remaining == 0, (
        "the whole file left through single-byte ranges and the budget never "
        "moved - the owner still sees an uncollected link"
    )
    assert db.query(DownloadLog).count() >= 1, (
        "nothing in download_log records that the file was taken"
    )


@pytest.mark.asyncio
async def test_the_desktop_client_size_probe_is_still_free(
    client, db, public_one_download, redis_stub
):
    """The regression this exemption exists for. `bytes=1-1` is what every
    shipped client sends to learn the total before it segments the transfer;
    charging it made a download_limit=1 share undownloadable from the client
    while a browser still worked (v2.6.0)."""
    owner, sh, f, link, token = public_one_download
    url = f"/api/public/{token}/files/{f.id}/download"

    for _ in range(5):
        r = await client.get(url, headers={"Range": "bytes=1-1"})
        assert r.status_code == 206, r.text
    db.refresh(link)
    assert link.downloads_remaining == 1
    assert db.query(DownloadLog).count() == 0

    r = await client.get(url)
    assert r.status_code == 200
    db.refresh(link)
    assert link.downloads_remaining == 0


# --- (2) + (3) the authenticated bulk ZIP -----------------------------------


ZIP_MEMBERS = [("first.bin", b"1" * 900), ("second.bin", b"2" * 1500)]


@pytest.fixture
def budgeted_share(db, make_user, tmp_path):
    owner = make_user(email="zowner@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="zrec@test.local", role=UserRole.employee, password=PW)
    sh = Share(
        created_by_id=owner.id,
        kind=ShareKind.outbound,
        state=ShareState.active,
        download_limit=1,
        downloads_remaining=1,
    )
    db.add(sh)
    db.flush()
    db.add(ShareRecipient(share_id=sh.id, recipient_user_id=rec.id))
    files = [
        _add_file(db, sh, owner, tmp_path, name, data, i)
        for i, (name, data) in enumerate(ZIP_MEMBERS)
    ]
    db.commit()
    return owner, rec, sh, files


def _dt(target_id: str, user_id: int) -> str:
    from app.services import download_token as download_token_svc

    return download_token_svc.issue(target_id, user_id, ttl_sec=900)


@pytest.mark.asyncio
async def test_a_ranged_zip_cannot_bypass_a_spent_share_budget(
    client, db, budgeted_share, redis_stub
):
    owner, rec, sh, files = budgeted_share

    # Spend the budget the honest way, on one member.
    r = await client.get(f"/api/files/{files[0].id}/download?dt={_dt(files[0].id, rec.id)}")
    assert r.status_code == 200, r.text
    db.refresh(sh)
    assert sh.downloads_remaining == 0

    zip_url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, rec.id)}"
    plain = await client.get(zip_url)
    assert plain.status_code == 410, "the control: the budget really is spent"

    ranged = await client.get(zip_url, headers={"Range": "bytes=1-"})
    assert ranged.status_code == 410, (
        "one header turned the 410 into the complete archive - and nothing was "
        "decremented, logged or audited for it"
    )


@pytest.mark.asyncio
async def test_a_real_zip_resume_still_works_for_whoever_paid(
    client, db, budgeted_share, redis_stub
):
    """The property the corroboration exists to preserve: an interrupted
    multi-GB archive must be resumable without paying twice."""
    owner, rec, sh, files = budgeted_share
    zip_url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, rec.id)}"

    full = await client.get(zip_url)
    assert full.status_code == 200, full.text
    db.refresh(sh)
    assert sh.downloads_remaining == 0

    cut = len(full.content) - 200
    resumed = await client.get(zip_url, headers={"Range": f"bytes={cut}-"})
    assert resumed.status_code == 206, resumed.text
    blob = full.content[:cut] + resumed.content
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.testzip() is None
        for name, data in ZIP_MEMBERS:
            assert zf.read(name) == data
    db.refresh(sh)
    assert sh.downloads_remaining == 0, "a corroborated resume must not re-charge"


@pytest.mark.asyncio
async def test_a_file_added_after_payment_is_not_free(
    client, db, budgeted_share, redis_stub, tmp_path
):
    """The archive identity is per member list. Paying for one archive must not
    buy a different, larger one."""
    owner, rec, sh, files = budgeted_share
    zip_url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, rec.id)}"
    assert (await client.get(zip_url)).status_code == 200
    sh.downloads_remaining = 0
    db.commit()

    _add_file(db, sh, owner, tmp_path, "secret.bin", b"s" * 300, 9)
    db.commit()

    ranged = await client.get(zip_url, headers={"Range": "bytes=1-"})
    assert ranged.status_code == 410, (
        "the added member came out free on the strength of the earlier archive"
    )


@pytest.mark.asyncio
async def test_an_approver_taking_a_pending_share_by_zip_leaves_a_record(
    client, db, make_user, login_as, tmp_path, redis_stub
):
    """The single-file route records a review download; the archive route -
    which hands over every file at once - recorded nothing at all."""
    k = settings_svc.Keys
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    owner = make_user(email="powner@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="prec@test.local", role=UserRole.employee, password=PW)
    for key, value in (
        (k.SHARE_APPROVAL_ENABLED, "true"),
        (k.SHARE_APPROVAL_APPROVER_MODE, "admins_only"),
        (k.SHARE_APPROVAL_SCOPE, "outbound"),
        (k.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW, "true"),
        (k.SHARE_APPROVAL_APPROVER_USERS, json.dumps([admin.id])),
    ):
        settings_svc.set_value(db, key=key, value=value, actor=None)

    sh = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.pending_approval
    )
    db.add(sh)
    db.flush()
    db.add(ShareRecipient(share_id=sh.id, recipient_user_id=rec.id))
    for i, (name, data) in enumerate(ZIP_MEMBERS):
        _add_file(db, sh, owner, tmp_path, name, data, i)
    db.commit()

    before_logs = db.query(DownloadLog).count()
    r = await client.get(f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, admin.id)}")
    assert r.status_code == 200, r.text
    assert len(r.content) > sum(len(d) for _n, d in ZIP_MEMBERS)

    assert db.query(DownloadLog).count() == before_logs + len(ZIP_MEMBERS), (
        "an approver took every file in a share they are not a recipient of "
        "and no download_log row records it"
    )
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.share_downloaded)
        .all()
    )
    assert len(rows) == 1, "no audit trail for a non-recipient taking the whole share"
    assert rows[0].extra.get("review") is True
    assert rows[0].actor_user_id == admin.id


@pytest.mark.asyncio
async def test_a_zip_resume_survives_a_redis_restart(client, db, budgeted_share, redis_stub):
    """The evidence must be durable as well as fast.

    Replacing the download_log check with a Redis mark closed the bypass and
    introduced a new failure: a restart - which the v2.5.0 host step performs,
    and which any host reboot does - erased the proof, so a legitimate resume
    was re-charged and, on a spent budget, answered 410 (audit #2 cross-check).
    The desktop client can pause a download and resume it the next day."""
    owner, rec, sh, files = budgeted_share
    zip_url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, rec.id)}"

    full = await client.get(zip_url)
    assert full.status_code == 200, full.text
    db.refresh(sh)
    assert sh.downloads_remaining == 0

    redis_stub.store.clear()  # the restart

    cut = len(full.content) - 200
    resumed = await client.get(zip_url, headers={"Range": f"bytes={cut}-"})
    assert resumed.status_code == 206, (
        "a paid resume was refused because the Redis mark was gone"
    )
    db.refresh(sh)
    assert sh.downloads_remaining == 0, "and it must not have been charged again"


@pytest.mark.asyncio
async def test_the_durable_evidence_is_archive_specific(
    client, db, budgeted_share, redis_stub, tmp_path
):
    """Its predecessor accepted a download_log row for ONE member as evidence
    that an archive transfer was in progress, which is what made the bypass
    possible. Adding a file changes the archive, and the old payment must not
    corroborate the new one."""
    owner, rec, sh, files = budgeted_share
    zip_url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, rec.id)}"
    assert (await client.get(zip_url)).status_code == 200
    sh.downloads_remaining = 0
    db.commit()
    redis_stub.store.clear()

    _add_file(db, sh, owner, tmp_path, "late.bin", b"L" * 200, 8)
    db.commit()

    ranged = await client.get(zip_url, headers={"Range": "bytes=1-"})
    assert ranged.status_code == 410

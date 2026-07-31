"""A public link's budget must not be spendable by someone else's action.

Found by the cross-check pass over the v2.7.3 residual specs - all five agents
walked past it, and it is more serious than any of them.

v2.6.0 made the free-continuation exemption require evidence. The evidence chosen
on the anonymous path was `transfer_activity.was_download_recent(file_id)`: "this
instance started serving that file recently". That answers the question the
MAINTENANCE drain asks. It does not answer the question the BUDGET asks, which is
"has this caller already paid for this file" - and the two got the same key.

Three consequences, all reachable without touching the public link:

1. The share OWNER previewing their own file in a browser writes
   `fh:transfer:recent:{file_id}` (routers/files.py passes `count=True,
   file_id=...`). Every holder of the public link then gets unlimited free
   copies for the life of the mark - no counter movement, no download_log row,
   no audit entry, no owner notification.
2. The same across the ZIP routes, and it crosses the auth boundary: the
   authenticated ZIP and the public ZIP compute an identical
   `zip:{share_id}:{etag}` key from the same reproducible archive identity, so
   an authenticated ZIP download corroborates an anonymous one.
3. The window is not bounded. `mark_download_recent` sets the key with no `nx`,
   and a free continuation still reaches `serve_response(count=True, ...)`,
   which re-marks it. One paid download plus one request every 30 minutes is
   indefinite. The comment shipped in v2.6.0 says the opposite - "Bounded,
   unlike unlimited-forever" - which makes this the same failure the whole wave
   has been chasing, in a comment written while chasing it.

The fix is to mark where the PAYMENT happens rather than where bytes are served,
and to namespace the mark per principal so one principal's activity cannot
corroborate another's.
"""
from __future__ import annotations

import pytest

from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole

PAYLOAD = b"payload-" * 500  # 4000 bytes


class _FakeRedis:
    """Real enough to hold marks, so the fail-open path cannot mask the bug."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.writes: list[str] = []

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.writes.append(key)
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


@pytest.fixture
def owned_public_share(db, make_user, tmp_path):
    """A public link with a one-download budget, plus its signed-in owner."""
    from app.services import public_link as public_link_svc

    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "doc.txt"
    path.write_bytes(PAYLOAD)
    f = File(
        id="00000000-0000-0000-0000-00000000prv9",
        share_id=sh.id,
        original_filename="doc.txt",
        mime_type="text/plain",
        size_bytes=len(PAYLOAD),
        storage_path=str(path),
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    created = public_link_svc.create_link(
        db,
        share=sh,
        actor=owner,
        password=None,
        download_limit=1,
        notify_on_download=False,
    )
    db.commit()
    return owner, sh, f, created.record, created.plaintext_token


def _dt(file_id: str, user_id: int) -> str:
    from app.services import download_token as download_token_svc

    return download_token_svc.issue(file_id, user_id, ttl_sec=900)


# --- (1) an owner action must not corroborate an anonymous one --------------


@pytest.mark.asyncio
async def test_an_owner_preview_does_not_buy_free_public_downloads(
    client, db, owned_public_share, redis_stub
):
    """The headline. The owner looks at their own file in the browser; every
    holder of the link then downloads it for free, indefinitely."""
    owner, sh, f, link, token = owned_public_share

    preview = await client.get(f"/api/files/{f.id}/preview?dt={_dt(f.id, owner.id)}")
    assert preview.status_code == 200, preview.text

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code in (200, 206, 410), r.text

    db.refresh(link)
    assert link.downloads_remaining == 0, (
        "an authenticated preview by the owner corroborated an anonymous "
        "ranged download; the public budget was spent by nobody"
    )


@pytest.mark.asyncio
async def test_an_owner_download_does_not_buy_free_public_downloads(
    client, db, owned_public_share, redis_stub
):
    """Same shape via the authenticated download rather than the preview."""
    owner, sh, f, link, token = owned_public_share

    got = await client.get(f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}")
    assert got.status_code == 200, got.text

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code in (200, 206, 410)
    db.refresh(link)
    assert link.downloads_remaining == 0


# --- (3) the window must actually be bounded --------------------------------


@pytest.mark.asyncio
async def test_the_free_window_cannot_be_refreshed_indefinitely(
    client, db, owned_public_share, redis_stub
):
    """v2.6.0 shipped a comment calling this window "Bounded, unlike
    unlimited-forever". It is only bounded if a free continuation does not
    refresh the mark - and it did, because the mark is written where bytes are
    served and a free continuation still serves bytes.

    One paid download plus one request per window is indefinite."""
    owner, sh, f, link, token = owned_public_share

    paid = await client.get(f"/api/public/{token}/files/{f.id}/download")
    assert paid.status_code == 200, paid.text
    db.refresh(link)
    assert link.downloads_remaining == 0

    paid_writes = [k for k in redis_stub.writes if k.startswith("fh:transfer:paid:")]
    assert len(paid_writes) == 1, "the payment should mark exactly once"

    # Continuations inside the window are free - that is the point of the
    # exemption. What must NOT happen is that they re-write the mark, because a
    # re-write restarts its TTL and the window becomes self-sustaining: one
    # payment plus one request per window is indefinite.
    for _ in range(3):
        r = await client.get(
            f"/api/public/{token}/files/{f.id}/download",
            headers={"Range": "bytes=1-"},
        )
        assert r.status_code in (200, 206), r.text

    after = [k for k in redis_stub.writes if k.startswith("fh:transfer:paid:")]
    assert after == paid_writes, (
        f"a free continuation re-wrote the payment mark ({len(after)} writes for "
        "one payment); its TTL restarts each time, so the free window renews "
        "itself indefinitely"
    )


# --- what must not regress --------------------------------------------------


@pytest.mark.asyncio
async def test_a_genuine_resume_by_the_payer_is_still_free(
    client, db, owned_public_share, redis_stub
):
    """The whole point of the exemption. The anonymous recipient pays once and
    finishes the transfer."""
    owner, sh, f, link, token = owned_public_share

    first = await client.get(f"/api/public/{token}/files/{f.id}/download")
    assert first.status_code == 200
    db.refresh(link)
    assert link.downloads_remaining == 0

    resumed = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert resumed.status_code in (200, 206), resumed.text
    db.refresh(link)
    assert link.downloads_remaining == 0
    assert db.query(DownloadLog).filter(DownloadLog.file_id == f.id).count() == 1


@pytest.mark.asyncio
async def test_an_uncorroborated_range_is_still_charged(
    client, db, owned_public_share, redis_stub
):
    """flow-publiclink-7 must stay closed."""
    owner, sh, f, link, token = owned_public_share

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code in (200, 206)
    db.refresh(link)
    assert link.downloads_remaining == 0


# --- (2) the ZIP routes must not corroborate each other ---------------------


@pytest.mark.asyncio
async def test_an_authenticated_zip_does_not_buy_free_public_zips(
    client, db, owned_public_share, redis_stub
):
    """The auth boundary crossing. Both ZIP routes derive their key from the
    same reproducible archive identity - `zip:{share_id}:{etag}`, where the ETag
    is deterministic by design so resumes work - so under the serving mark, the
    owner downloading their own archive authorised unlimited anonymous ones.

    Nothing about the public link is touched to trigger it."""
    owner, sh, f, link, token = owned_public_share

    authed = await client.get(
        f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, owner.id)}"
    )
    assert authed.status_code == 200, authed.text

    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=200-"}
    )
    assert r.status_code in (200, 206, 410), r.text
    db.refresh(link)
    assert link.downloads_remaining == 0, (
        "an authenticated ZIP download corroborated an anonymous one; the "
        "public budget was spent by the owner's own action"
    )


@pytest.mark.asyncio
async def test_a_public_zip_resume_by_the_payer_is_still_free(
    client, db, owned_public_share, redis_stub
):
    """What must not regress: the anonymous recipient pays once and resumes."""
    owner, sh, f, link, token = owned_public_share

    first = await client.get(f"/api/public/{token}/download-zip")
    assert first.status_code == 200, first.text
    db.refresh(link)
    assert link.downloads_remaining == 0

    resumed = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=200-"}
    )
    assert resumed.status_code in (200, 206), resumed.text
    db.refresh(link)
    assert link.downloads_remaining == 0

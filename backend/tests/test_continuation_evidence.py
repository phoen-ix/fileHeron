"""A `Range:` header is a claim, not proof.

`utils/http_range.is_partial_continuation` answers exactly one question - does
the range start above byte 0 - and three separate exemptions were granted on the
strength of it. v2.5.0 bound the maintenance one. These are the other two:

flow-publiclink-7  the public single-file download skipped BOTH the exhausted
                   check and the counter for any `Range: bytes=N-`. A link
                   holder could re-download every file an unlimited number of
                   times with `downloads_remaining` never moving, no
                   download_log row, no audit entry and no owner notification -
                   including after the link was exhausted, where the only thing
                   they could not fetch was byte 0.

(authenticated sibling)  the same bypass on the per-share budget in
                   `routers/files.py`, covered in test_share_download_limit.py.

The two paths use deliberately different evidence:

- **public**: a short Redis mark keyed on the FILE (`transfer_activity.
  was_download_recent`, 30 min). Public downloads are browser-driven, so a
  native resume happens in seconds. Keyed on the file rather than the client so
  a phone changing networks mid-download keeps its continuation, and fails OPEN
  when Redis is down - a refused resume is worse than a missed bypass.
- **authenticated**: a durable `download_log` row for (file, user) inside
  `downloads.resume_credit_hours` (24h). The desktop client can pause a download
  and resume it the next day, which a 30-minute window would charge twice.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole


class _FakeRedis:
    """Enough of Redis for the recency mark."""

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
def public_file(db, make_user, tmp_path, monkeypatch):
    """A public link with a 1-download budget over one clean file."""
    from app.services import public_link as public_link_svc

    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "payload.bin"
    path.write_bytes(b"0123456789" * 32)
    f = File(
        id="00000000-0000-0000-0000-00000000cont", share_id=sh.id,
        original_filename="payload.bin", mime_type="application/octet-stream",
        size_bytes=path.stat().st_size, storage_path=str(path),
        state=FileState.clean, uploaded_by_id=owner.id,
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
    return f, sh, created.record, created.plaintext_token


# --- flow-publiclink-7 ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_uncorroborated_range_is_charged(client, db, public_file, monkeypatch):
    """The bypass: `Range: bytes=1-` on a fresh connection was served free."""
    from app.services import transfer_activity

    f, sh, link, token = public_file
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: _FakeRedis())

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code in (200, 206), r.text
    db.refresh(link)
    assert link.downloads_remaining == 0, (
        "a range continuation with nothing to continue was served free"
    )


@pytest.mark.asyncio
async def test_an_uncorroborated_range_cannot_drain_an_exhausted_link(
    client, db, public_file, monkeypatch
):
    """The sharper half: the exhausted CHECK was skipped too, so a spent link
    still handed over everything but byte 0 - repeatedly, invisibly."""
    from app.services import transfer_activity

    f, sh, link, token = public_file
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: _FakeRedis())
    link.downloads_remaining = 0
    db.commit()

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code == 410, r.text
    assert r.json()["code"] == "PUBLIC_LINK_EXHAUSTED"


@pytest.mark.asyncio
async def test_a_corroborated_continuation_is_free(
    client, db, public_file, monkeypatch
):
    """What the exemption is actually for: the byte-0 request pays, and the
    continuation ranges of that same download do not pay again."""
    from app.services import transfer_activity

    f, sh, link, token = public_file
    fake = _FakeRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)

    first = await client.get(f"/api/public/{token}/files/{f.id}/download")
    assert first.status_code == 200, first.text
    db.refresh(link)
    assert link.downloads_remaining == 0

    # The full request marked the file as being served; the continuation of it
    # must finish even though the counter is now spent.
    second = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert second.status_code in (200, 206), second.text
    db.refresh(link)
    assert link.downloads_remaining == 0, "the continuation spent another unit"


@pytest.mark.asyncio
async def test_redis_down_charges_rather_than_giving_the_file_away(
    client, db, public_file, monkeypatch
):
    """Fail CLOSED on the PAYMENT mark.

    This asserted fail-open until audit #2, borrowing the posture of the
    SERVING mark - where a wrong answer costs a paused download. Here a wrong
    answer costs the budget itself: for the whole duration of a Redis outage, a
    public link with `downloads_remaining = 0` served the file (and, on the ZIP
    route, the complete archive) to anyone sending `Range: bytes=1-`,
    repeatedly, with no counter movement, no download_log row and nothing in the
    owner's history.

    The cost of the other direction is that a genuine resume during an outage
    pays a second download. That is recoverable; unlimited free extraction is
    not."""
    from app.services import transfer_activity

    f, sh, link, token = public_file

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(transfer_activity, "get_redis", _boom)
    link.downloads_remaining = 0
    db.commit()

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code == 410, r.text
    assert r.json()["code"] == "PUBLIC_LINK_EXHAUSTED"


@pytest.mark.asyncio
async def test_redis_down_still_serves_a_link_with_budget_left(
    client, db, public_file, monkeypatch
):
    """The control on the other side: failing closed must charge, not refuse.
    A link with downloads left keeps working through an outage."""
    from app.services import transfer_activity

    f, sh, link, token = public_file

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(transfer_activity, "get_redis", _boom)
    link.downloads_remaining = 2
    db.commit()

    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code in (200, 206), r.text
    db.refresh(link)
    assert link.downloads_remaining == 1


@pytest.mark.asyncio
async def test_a_full_request_still_pays(client, db, public_file, monkeypatch):
    """Control: the ordinary path must keep charging."""
    from app.services import transfer_activity

    f, sh, link, token = public_file
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: _FakeRedis())

    r = await client.get(f"/api/public/{token}/files/{f.id}/download")
    assert r.status_code == 200, r.text
    db.refresh(link)
    assert link.downloads_remaining == 0


@pytest.mark.asyncio
async def test_the_download_is_still_logged_once(client, db, public_file, monkeypatch):
    """A continuation must not re-log either, or one download reads as N in the
    owner's history."""
    from app.models.download_log import DownloadLog
    from app.services import transfer_activity

    f, sh, link, token = public_file
    fake = _FakeRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)

    await client.get(f"/api/public/{token}/files/{f.id}/download")
    await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert db.query(DownloadLog).filter(DownloadLog.file_id == f.id).count() == 1


# --- the shape of the guard -------------------------------------------------


def test_both_public_decisions_use_the_same_corroborated_boolean():
    """The exhausted check and the counter must not disagree: one of them
    honouring the bare header would reopen half the hole."""
    from app.routers import public as public_router

    src = inspect.getsource(public_router.public_download)
    # `was_download_paid`, not `was_download_recent`. The serving mark answers
    # "is a transfer in flight" and was the wrong evidence for a budget
    # decision; the payment mark is principal-keyed and written only where the
    # counter moves. This line used to assert the string "was_download_recent",
    # which is present in the function's HISTORICAL COMMENT - so rewording that
    # comment turned the suite red while any change to the actual evidence
    # helper went unnoticed (audit #2).
    body = src.split('"""', 2)[-1]
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "was_download_paid" in code
    assert "was_download_recent" not in code
    assert "allow_exhausted_continuation=is_continuation" in src
    # The counter/log block is gated on the corroborated boolean. It may carry
    # additional exemptions (the size probe, v2.6.1) but `is_continuation` must
    # be one of its terms.
    assert "if not is_continuation" in src
    # The bare header test must not be consulted for either decision.
    assert "allow_exhausted_continuation=is_partial_continuation" not in src
    assert "if not is_partial_continuation" not in src


def test_the_authed_path_uses_durable_evidence():
    """A 30-minute Redis mark would charge the desktop client's overnight
    resume a second time."""
    from app.routers import files as files_router

    src = inspect.getsource(files_router.download_file)
    assert "has_recent_counted_download" in src
    assert "DOWNLOAD_RESUME_CREDIT_HOURS" in src


def test_the_credit_window_is_admin_tunable():
    from app.services import settings_registry as sr

    keys = {t.key for t in sr.TUNABLES}
    assert sr.K.DOWNLOAD_RESUME_CREDIT_HOURS in keys


def test_the_evidence_lookup_is_scoped_to_one_user_and_file(db, make_user, tmp_path):
    from app.models.download_log import DownloadLog
    from app.services import file as file_svc

    owner = make_user(email="o@test.local", role=UserRole.employee)
    other = make_user(email="p@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(
        id="00000000-0000-0000-0000-0000000000ev", share_id=sh.id,
        original_filename="a.bin", mime_type="application/octet-stream",
        size_bytes=1, storage_path=str(tmp_path / "a.bin"),
        state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    db.add(DownloadLog(file_id=f.id, share_id=sh.id, accessed_by_user_id=owner.id))
    db.commit()

    assert file_svc.has_recent_counted_download(
        db, file_id=f.id, user_id=owner.id, within_hours=24
    )
    assert not file_svc.has_recent_counted_download(
        db, file_id=f.id, user_id=other.id, within_hours=24
    )


def test_an_old_download_stops_buying_continuations(db, make_user, tmp_path):
    """The credit is a window, not a licence."""
    from datetime import timedelta

    from app.models.download_log import DownloadLog
    from app.services import file as file_svc
    from app.utils.timeutil import utc_now

    owner = make_user(email="o@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(
        id="00000000-0000-0000-0000-0000000000ew", share_id=sh.id,
        original_filename="a.bin", mime_type="application/octet-stream",
        size_bytes=1, storage_path=str(tmp_path / "a.bin"),
        state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    row = DownloadLog(file_id=f.id, share_id=sh.id, accessed_by_user_id=owner.id)
    db.add(row)
    db.flush()
    db.query(DownloadLog).filter(DownloadLog.id == row.id).update(
        {"accessed_at": utc_now() - timedelta(hours=48)}
    )
    db.commit()

    assert not file_svc.has_recent_counted_download(
        db, file_id=f.id, user_id=owner.id, within_hours=24
    )
    assert file_svc.has_recent_counted_download(
        db, file_id=f.id, user_id=owner.id, within_hours=72
    )

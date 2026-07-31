"""The bulk-ZIP routes honour `Range` - and charge for it honestly.

`flow-publiclink-5` (audit 2026-07-30): a public ZIP was charged before the
first byte and could not serve a partial response. A 9 GB archive that died at
90% was therefore unrecoverable - the budget was already spent, every retry
restarted at byte 0, and once the budget ran out the retries got 410 forever.
The old code was not careless about it: it charged unconditionally *because*
honouring `is_partial_continuation` here would have been a free-download bypass
(the header is a claim anyone can make). Resuming safely needs the archive to be
seekable AND the continuation to be corroborated; v2.6.0 has both.

`tests/test_zip_resume.py` proves the seek is byte-exact. This file is about the
routes: status codes, headers, `If-Range`, and who pays.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole


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
    """One store shared by transfer_activity's recency marks and the CRC cache,
    so a full download really does warm what a resume reads."""
    from app import redis_client
    from app.services import transfer_activity

    fake = _FakeRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


MEMBERS = [
    ("first.bin", b"1" * 900),
    ("second.bin", b"2" * 1500),
    ("third.bin", b"3" * 400),
]


def _make_share(db, owner, tmp_path, *, files=MEMBERS):
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    for i, (name, data) in enumerate(files):
        path = tmp_path / name
        path.write_bytes(data)
        db.add(
            File(
                id=f"00000000-0000-0000-0000-0000000000{i:02d}",
                share_id=sh.id,
                original_filename=name,
                mime_type="application/octet-stream",
                size_bytes=len(data),
                storage_path=str(path),
                state=FileState.clean,
                uploaded_by_id=owner.id,
            )
        )
    db.flush()
    return sh


@pytest.fixture
def public_zip(db, make_user, tmp_path):
    """A public link with a 1-download budget over a three-member archive."""
    from app.services import public_link as public_link_svc

    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = _make_share(db, owner, tmp_path)
    created = public_link_svc.create_link(
        db,
        share=sh,
        actor=owner,
        password=None,
        download_limit=1,
        notify_on_download=False,
    )
    db.commit()
    return sh, created.record, created.plaintext_token


# --- the archive is resumable -----------------------------------------------


@pytest.mark.asyncio
async def test_a_range_gets_a_real_206(client, db, public_zip, redis_stub):
    sh, link, token = public_zip
    full = await client.get(f"/api/public/{token}/download-zip")
    assert full.status_code == 200, full.text
    assert full.headers["accept-ranges"] == "bytes"
    total = int(full.headers["content-length"])
    assert len(full.content) == total

    part = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": f"bytes=500-{total - 1}"}
    )
    assert part.status_code == 206, part.text
    assert part.headers["content-range"] == f"bytes 500-{total - 1}/{total}"
    assert int(part.headers["content-length"]) == total - 500
    assert part.content == full.content[500:]


@pytest.mark.asyncio
async def test_an_interrupted_download_reassembles_into_a_valid_archive(
    client, db, public_zip, redis_stub
):
    """The whole point: what the client already has, plus what it resumes, is a
    ZIP that opens and whose members are intact."""
    sh, link, token = public_zip
    full = await client.get(f"/api/public/{token}/download-zip")
    total = len(full.content)
    cut = total - 200  # somewhere in the central directory

    resumed = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": f"bytes={cut}-"}
    )
    assert resumed.status_code == 206
    blob = full.content[:cut] + resumed.content
    assert blob == full.content
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.testzip() is None
        for name, data in MEMBERS:
            assert zf.read(name) == data


@pytest.mark.asyncio
async def test_a_range_past_the_end_is_416_with_the_length(
    client, db, public_zip, redis_stub
):
    sh, link, token = public_zip
    full = await client.get(f"/api/public/{token}/download-zip")
    total = len(full.content)
    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": f"bytes={total + 5}-"}
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{total}"


@pytest.mark.asyncio
async def test_a_multi_range_request_falls_back_to_the_whole_archive(
    client, db, public_zip, redis_stub
):
    """Nothing here serves multipart/byteranges; returning the full 200 is the
    behaviour RFC 9110 allows and the only one that cannot mislead a client."""
    sh, link, token = public_zip
    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=0-10, 20-30"}
    )
    assert r.status_code == 200


# --- If-Range ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_etag_is_stable_across_requests(client, db, public_zip, redis_stub):
    sh, link, token = public_zip
    a = await client.get(f"/api/public/{token}/download-zip")
    link.downloads_remaining = 5
    db.commit()
    b = await client.get(f"/api/public/{token}/download-zip")
    assert a.headers["etag"] == b.headers["etag"]
    assert a.content == b.content


@pytest.mark.asyncio
async def test_a_quarantined_member_changes_the_etag_and_restarts_the_transfer(
    client, db, public_zip, redis_stub
):
    """The archive a client is resuming no longer exists. Splicing the new one
    onto the old prefix would hand back a corrupt file that still opens; the
    `If-Range` miss makes it a clean restart instead."""
    sh, link, token = public_zip
    link.downloads_remaining = 5
    db.commit()
    first = await client.get(f"/api/public/{token}/download-zip")
    etag = first.headers["etag"]
    total = len(first.content)

    victim = db.query(File).filter(File.share_id == sh.id).order_by(File.id).first()
    victim.state = FileState.infected
    db.commit()

    resumed = await client.get(
        f"/api/public/{token}/download-zip",
        headers={"Range": f"bytes={total - 100}-", "If-Range": etag},
    )
    assert resumed.status_code == 200, "a stale If-Range must not be resumed"
    assert resumed.headers["etag"] != etag


@pytest.mark.asyncio
async def test_a_matching_if_range_is_honoured(client, db, public_zip, redis_stub):
    sh, link, token = public_zip
    link.downloads_remaining = 5
    db.commit()
    first = await client.get(f"/api/public/{token}/download-zip")
    etag = first.headers["etag"]
    r = await client.get(
        f"/api/public/{token}/download-zip",
        headers={"Range": "bytes=100-", "If-Range": etag},
    )
    assert r.status_code == 206
    assert r.content == first.content[100:]


# --- who pays ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_corroborated_resume_is_free(client, db, public_zip, redis_stub):
    """One download, one unit - even though it took two requests."""
    sh, link, token = public_zip
    full = await client.get(f"/api/public/{token}/download-zip")
    assert full.status_code == 200
    db.refresh(link)
    assert link.downloads_remaining == 0

    resumed = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=800-"}
    )
    assert resumed.status_code == 206, resumed.text
    db.refresh(link)
    assert link.downloads_remaining == 0


@pytest.mark.asyncio
async def test_an_uncorroborated_range_pays_like_any_other_download(
    client, db, public_zip, redis_stub
):
    """`Range: bytes=1-` with no archive in flight is not a resume, it is a
    download that happens to skip a byte. Honouring the header alone here is
    exactly the bypass the old always-charge rule existed to prevent."""
    sh, link, token = public_zip
    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=800-"}
    )
    assert r.status_code == 206
    db.refresh(link)
    assert link.downloads_remaining == 0


@pytest.mark.asyncio
async def test_an_uncorroborated_range_cannot_drain_a_spent_link(
    client, db, public_zip, redis_stub
):
    sh, link, token = public_zip
    link.downloads_remaining = 0
    db.commit()
    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=800-"}
    )
    assert r.status_code == 410, r.text
    assert r.json()["code"] == "PUBLIC_LINK_EXHAUSTED"


@pytest.mark.asyncio
async def test_a_resume_of_a_different_archive_is_not_free(
    client, db, public_zip, redis_stub, tmp_path
):
    """The evidence is keyed on the archive's ETag, so the mark left by
    downloading share A cannot buy a free ranged read of share B - nor of A
    after its member list changed."""
    sh, link, token = public_zip
    link.downloads_remaining = 5
    db.commit()
    await client.get(f"/api/public/{token}/download-zip")
    db.refresh(link)
    before = link.downloads_remaining

    extra = tmp_path / "fourth.bin"
    extra.write_bytes(b"4" * 100)
    db.add(
        File(
            id="00000000-0000-0000-0000-0000000000ff",
            share_id=sh.id,
            original_filename="fourth.bin",
            mime_type="application/octet-stream",
            size_bytes=100,
            storage_path=str(extra),
            state=FileState.clean,
            uploaded_by_id=sh.created_by_id,
        )
    )
    db.commit()

    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=800-"}
    )
    assert r.status_code == 206
    db.refresh(link)
    assert link.downloads_remaining == before - 1


@pytest.mark.asyncio
async def test_a_corroborated_resume_is_not_logged_again(
    client, db, public_zip, redis_stub
):
    from app.models.download_log import DownloadLog

    sh, link, token = public_zip
    await client.get(f"/api/public/{token}/download-zip")
    n = db.query(DownloadLog).filter(DownloadLog.share_id == sh.id).count()
    await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=800-"}
    )
    assert db.query(DownloadLog).filter(DownloadLog.share_id == sh.id).count() == n


@pytest.mark.asyncio
async def test_a_range_starting_at_zero_is_a_fresh_download_and_pays(
    client, db, public_zip, redis_stub
):
    """`bytes=0-` is not a continuation of anything."""
    sh, link, token = public_zip
    link.downloads_remaining = 2
    db.commit()
    await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=0-100"}
    )
    db.refresh(link)
    assert link.downloads_remaining == 1


# --- reproducibility at the route level --------------------------------------


@pytest.mark.asyncio
async def test_two_downloads_of_one_share_are_byte_identical(
    client, db, public_zip, redis_stub
):
    """Without this the two halves of a resumed download belong to different
    archives. It held only by luck before: the DOS timestamp came from
    `time.time()`, so any two generations more than two seconds apart differed."""
    sh, link, token = public_zip
    link.downloads_remaining = 5
    db.commit()
    a = await client.get(f"/api/public/{token}/download-zip")
    b = await client.get(f"/api/public/{token}/download-zip")
    assert a.content == b.content


def test_the_member_order_is_total(db, make_user, tmp_path):
    """`created_at` alone ties for files uploaded in the same second, and the
    database may break the tie either way between two queries - which would
    reorder members mid-resume."""
    import inspect

    from app.services import file as file_svc

    src = inspect.getsource(file_svc.downloadable_files)
    assert "File.id.asc()" in src

    owner = make_user(email="o@test.local", role=UserRole.employee)
    sh = _make_share(db, owner, tmp_path)
    db.commit()
    ids = [f.id for f in file_svc.downloadable_files(db, sh.id)]
    assert ids == sorted(ids)


# --- the authenticated route -------------------------------------------------


@pytest.fixture
def authed_zip(db, make_user, tmp_path):
    owner = make_user(email="owner2@test.local", role=UserRole.employee)
    sh = _make_share(db, owner, tmp_path)
    sh.download_limit = 1
    sh.downloads_remaining = 1
    db.commit()
    return owner, sh


def _dt(share_id: str, user_id: int) -> str:
    from app.services import download_token as download_token_svc

    return download_token_svc.issue(share_id, user_id, ttl_sec=900)


@pytest.mark.asyncio
async def test_the_authed_zip_resumes_too(client, db, authed_zip, redis_stub):
    owner, sh = authed_zip
    url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, owner.id)}"
    full = await client.get(url)
    assert full.status_code == 200, full.text
    total = len(full.content)

    part = await client.get(url, headers={"Range": "bytes=700-"})
    assert part.status_code == 206
    assert part.content == full.content[700:]
    assert part.headers["content-range"] == f"bytes 700-{total - 1}/{total}"


@pytest.mark.asyncio
async def test_the_authed_resume_is_free_after_a_paid_download(
    client, db, authed_zip, redis_stub
):
    owner, sh = authed_zip
    url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, owner.id)}"
    await client.get(url)
    db.refresh(sh)
    assert sh.downloads_remaining == 0

    r = await client.get(url, headers={"Range": "bytes=700-"})
    assert r.status_code == 206, r.text
    db.refresh(sh)
    assert sh.downloads_remaining == 0


@pytest.mark.asyncio
async def test_the_authed_range_pays_without_a_prior_download(
    client, db, authed_zip, redis_stub
):
    owner, sh = authed_zip
    url = f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, owner.id)}"
    r = await client.get(url, headers={"Range": "bytes=700-"})
    assert r.status_code == 206
    db.refresh(sh)
    assert sh.downloads_remaining == 0, (
        "a range with no download behind it was served free"
    )
    # It is served, and it is charged - which is the point. From here on the
    # user HAS a logged download, so their further ranges are genuine
    # continuations of it and correctly free; see the sibling test for the
    # stranger who has none.


@pytest.mark.asyncio
async def test_the_authed_credit_belongs_to_the_user_who_paid(
    client, db, authed_zip, make_user, redis_stub
):
    """A stranger's ranged request must not ride on someone else's download -
    the durable evidence is scoped to (file, user)."""
    owner, sh = authed_zip
    other = make_user(email="other@test.local", role=UserRole.admin)
    await client.get(f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, owner.id)}")
    db.refresh(sh)
    assert sh.downloads_remaining == 0

    r = await client.get(
        f"/api/files/{sh.id}/download-zip?dt={_dt(sh.id, other.id)}",
        headers={"Range": "bytes=700-"},
    )
    assert r.status_code == 410
    assert r.json()["code"] == "SHARE_DOWNLOAD_LIMIT_REACHED"


# --- the parser --------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "total", "expected"),
    [
        (None, 100, None),
        ("", 100, None),
        ("items=0-10", 100, None),  # not bytes
        ("bytes=abc-", 100, None),
        ("bytes=", 100, None),
        ("bytes=0-10, 20-30", 100, None),  # multi-range: serve the whole thing
        ("bytes=0-", 100, (0, 99)),
        ("bytes=10-", 100, (10, 99)),
        ("bytes=10-20", 100, (10, 20)),
        ("bytes=10-500", 100, (10, 99)),  # clipped to the resource
        ("bytes=-20", 100, (80, 99)),  # suffix
        ("bytes=-500", 100, (0, 99)),  # suffix longer than the resource
        (" bytes = 5 - 9 ", 100, (5, 9)),  # whitespace is legal
        ("bytes=99-99", 100, (99, 99)),
    ],
)
def test_the_range_parser_resolves_what_it_should(header, total, expected):
    from app.utils.http_range import parse_single_range

    got = parse_single_range(header, total)
    assert (None if got is None else (got.start, got.end)) == expected


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=500-600", "bytes=-0"])
def test_the_range_parser_refuses_what_is_outside_the_resource(header):
    """These must become a 416, not a 200: handing back the whole resource to a
    client that asked for byte 100 of a 100-byte file writes the archive over
    itself at the wrong offset."""
    from app.utils.http_range import UnsatisfiableRangeError, parse_single_range

    with pytest.raises(UnsatisfiableRangeError):
        parse_single_range(header, 100)


# --- degradation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_expensive_resume_degrades_to_the_full_archive(
    client, db, public_zip, redis_stub, monkeypatch
):
    """Never corrupt: when the CRCs a resume needs are not cached and re-reading
    them would cost too much, the whole archive is returned with a 200. That is
    always a valid answer to a Range request; a guessed CRC is not."""
    from app.services import zip_stream

    monkeypatch.setattr(zip_stream, "MAX_RESUME_REREAD_BYTES", 0)
    sh, link, token = public_zip
    link.downloads_remaining = 5
    db.commit()
    full = await client.get(f"/api/public/{token}/download-zip")
    redis_stub.store.clear()  # cold CRC cache

    r = await client.get(
        f"/api/public/{token}/download-zip", headers={"Range": "bytes=2000-"}
    )
    assert r.status_code == 200
    assert "content-range" not in r.headers
    assert r.content == full.content


@pytest.mark.asyncio
async def test_a_dead_redis_still_serves_the_archive(client, db, public_zip, monkeypatch):
    from app import redis_client
    from app.services import transfer_activity

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(transfer_activity, "get_redis", _boom)
    monkeypatch.setattr(redis_client, "get_redis", _boom)

    sh, link, token = public_zip
    r = await client.get(f"/api/public/{token}/download-zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.testzip() is None

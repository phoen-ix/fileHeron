"""The desktop client's range probe must not be charged as a download.

REGRESSION, introduced by v2.6.0 (commit 0039919) and caught by the residual
sweep the same day.

`client/src/fileheron_client/api/download_resumable.py::_probe` opens EVERY
download with one tiny `Range: bytes=1-1` request, to learn the total size and
whether the server honours ranges. Its docstring states the property it relies
on, verbatim:

    Probes at ``bytes=1-1`` (not ``0-0``): the backend counts a range that
    starts at byte 0 as a full download [...] A start > 0 is treated as an
    uncounted continuation.

v2.6.0 deleted exactly that property. A continuation is now free only when a
`download_log` row already exists for (this file, this user), and a first-ever
download has none - so the probe is charged, and then the real transfer is
charged again. On a share with `download_limit=1` the probe spends the only
unit and the actual download gets 410: the share becomes undownloadable from
the desktop client, while remaining downloadable from a browser.

This is the audit's own signature failure - a docstring asserting a property the
code no longer has - committed by the remediation itself. The fix has to keep
BOTH invariants at once: a fabricated `Range: bytes=1-` from a stranger is still
charged (that is `flow-publiclink-7`, and it must not regress), while a
size probe that transfers no meaningful payload is not.
"""
from __future__ import annotations

import pytest

from app.models.download_log import DownloadLog
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole

PAYLOAD = b"0123456789" * 400  # 4000 bytes


@pytest.fixture
def budgeted_share(db, make_user, tmp_path):
    """A share with a one-download budget over one clean file."""
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    sh = Share(
        created_by_id=owner.id,
        kind=ShareKind.outbound,
        state=ShareState.active,
        download_limit=1,
        downloads_remaining=1,
    )
    db.add(sh)
    db.flush()
    path = tmp_path / "payload.bin"
    path.write_bytes(PAYLOAD)
    f = File(
        id="00000000-0000-0000-0000-0000000probe",
        share_id=sh.id,
        original_filename="payload.bin",
        mime_type="application/octet-stream",
        size_bytes=len(PAYLOAD),
        storage_path=str(path),
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    db.commit()
    return owner, sh, f


def _dt(file_id: str, user_id: int) -> str:
    from app.services import download_token as download_token_svc

    return download_token_svc.issue(file_id, user_id, ttl_sec=900)


# --- the regression ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_client_probe_does_not_spend_the_budget(
    client, db, budgeted_share
):
    """The exact request the shipped client-v1.1.0 .exe sends first."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    r = await client.get(url, headers={"Range": "bytes=1-1"})
    assert r.status_code in (200, 206), r.text

    db.refresh(sh)
    assert sh.downloads_remaining == 1, (
        "the size probe was charged as a download; a download_limit=1 share is "
        "now undownloadable from the desktop client"
    )


@pytest.mark.asyncio
async def test_probe_then_download_costs_exactly_one(client, db, budgeted_share):
    """The whole client sequence: probe, then fetch. One download, one unit."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    probe = await client.get(url, headers={"Range": "bytes=1-1"})
    assert probe.status_code in (200, 206), probe.text

    real = await client.get(url)
    assert real.status_code == 200, real.text
    assert real.content == PAYLOAD

    db.refresh(sh)
    assert sh.downloads_remaining == 0
    assert (
        db.query(DownloadLog).filter(DownloadLog.file_id == f.id).count() == 1
    ), "one download was recorded twice in the owner's history"


@pytest.mark.asyncio
async def test_the_probe_is_not_logged_either(client, db, budgeted_share):
    """A probe that wrote a download_log row would also make every subsequent
    fabricated range look corroborated - it would hand out the credit it was
    exempted from paying for."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    await client.get(url, headers={"Range": "bytes=1-1"})
    assert db.query(DownloadLog).filter(DownloadLog.file_id == f.id).count() == 0


# --- what must NOT regress --------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_ranged_download_is_still_charged(client, db, budgeted_share):
    """flow-publiclink-7's authenticated sibling. `Range: bytes=1-` asks for the
    whole file minus one byte - that is a download, not a probe, and exempting
    it is the unlimited-free-download bypass v2.6.0 closed."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    r = await client.get(url, headers={"Range": "bytes=1-"})
    assert r.status_code in (200, 206), r.text

    db.refresh(sh)
    assert sh.downloads_remaining == 0, "the budget bypass is back"


@pytest.mark.parametrize("spec", ["bytes=1-2", "bytes=1-1024", "bytes=0-1"])
@pytest.mark.asyncio
async def test_anything_wider_than_one_byte_is_charged(
    client, db, budgeted_share, spec
):
    """The exemption's WIDTH is its entire safety argument, so it is pinned
    behaviourally rather than left to a constant nobody re-derives.

    One byte means extracting a file costs one authenticated, rate-limited
    request per byte. At a kilobyte it costs a thousandth of that, and the
    exemption stops being a probe allowance and starts being a way to take a
    file without spending a download. Two bytes is already not a size probe -
    a probe needs exactly one, to read Content-Range."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    r = await client.get(url, headers={"Range": spec})
    assert r.status_code in (200, 206), r.text

    db.refresh(sh)
    assert sh.downloads_remaining == 0, (
        f"{spec} was exempted; the probe allowance is wider than one byte"
    )


def test_the_probe_allowance_is_one_byte():
    """Belt and braces on the constant itself, with the reason attached."""
    from app.utils import http_range

    assert http_range.PROBE_MAX_BYTES == 1


@pytest.mark.asyncio
async def test_a_large_range_is_charged_even_though_it_starts_late(
    client, db, budgeted_share
):
    """The exemption must key on how much is being taken, not on how far in it
    starts - otherwise `Range: bytes=3999-` on a 4000-byte file is free and
    `bytes=1-1` is not, which is backwards for anything but the last byte."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    r = await client.get(url, headers={"Range": "bytes=100-3999"})
    assert r.status_code in (200, 206), r.text

    db.refresh(sh)
    assert sh.downloads_remaining == 0


@pytest.mark.asyncio
async def test_a_genuine_resume_after_a_paid_download_is_still_free(
    client, db, budgeted_share
):
    """The v2.6.0 behaviour that must survive the fix."""
    owner, sh, f = budgeted_share
    sh.download_limit = 2
    sh.downloads_remaining = 2
    db.commit()
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    await client.get(url)
    db.refresh(sh)
    assert sh.downloads_remaining == 1

    r = await client.get(url, headers={"Range": "bytes=2000-"})
    assert r.status_code in (200, 206)
    db.refresh(sh)
    assert sh.downloads_remaining == 1, "the corroborated resume was charged"


@pytest.mark.asyncio
async def test_many_probes_cannot_drain_the_file(client, db, budgeted_share):
    """A probe exemption is a hole if it can be AIMED. This test used to walk
    the offset - `bytes=1-1`, `bytes=2-2`, ... - assert every one was free, and
    conclude the exemption was safe because each response was small. That is the
    defect written down as an invariant: repeat it `size_bytes` times and the
    whole file is out, and on the anonymous public-link route it left the budget
    intact, no download_log row and no owner notification (audit #2).

    The real invariant is that the exemption covers exactly one offset - the one
    every shipped client probes - so repeating it yields that same byte forever,
    and moving off it costs a download like any other read."""
    owner, sh, f = budgeted_share
    url = f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}"

    for _ in range(20):
        r = await client.get(url, headers={"Range": "bytes=1-1"})
        assert r.status_code == 206
        assert len(r.content) == 1
    db.refresh(sh)
    assert sh.downloads_remaining == 1

    moved = await client.get(url, headers={"Range": "bytes=2-2"})
    assert moved.status_code in (200, 206)
    db.refresh(sh)
    assert sh.downloads_remaining == 0, (
        "a range at any offset other than the probe offset is a read of the "
        "file and pays for one"
    )


@pytest.mark.asyncio
async def test_a_one_byte_file_is_not_probe_able(client, db, make_user, tmp_path):
    """For a file this small the "probe" IS the file, so exempting it would hand
    the whole thing over for free. The guard is `total <= PROBE_MAX_BYTES`."""
    owner = make_user(email="tiny@test.local", role=UserRole.employee)
    sh = Share(
        created_by_id=owner.id,
        kind=ShareKind.outbound,
        state=ShareState.active,
        download_limit=1,
        downloads_remaining=1,
    )
    db.add(sh)
    db.flush()
    path = tmp_path / "one.bin"
    path.write_bytes(b"X")
    f = File(
        id="00000000-0000-0000-0000-0000000000t1",
        share_id=sh.id,
        original_filename="one.bin",
        mime_type="application/octet-stream",
        size_bytes=1,
        storage_path=str(path),
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    db.commit()

    r = await client.get(
        f"/api/files/{f.id}/download?dt={_dt(f.id, owner.id)}",
        headers={"Range": "bytes=0-0"},
    )
    assert r.status_code in (200, 206), r.text
    db.refresh(sh)
    assert sh.downloads_remaining == 0, "the whole file was taken as a free probe"


# --- the public path gets the same treatment --------------------------------


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
def public_budgeted(db, make_user, tmp_path, monkeypatch):
    from app.services import public_link as public_link_svc
    from app.services import transfer_activity

    monkeypatch.setattr(transfer_activity, "get_redis", lambda: _FakeRedis())
    owner = make_user(email="powner@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "pub.bin"
    path.write_bytes(PAYLOAD)
    f = File(
        id="00000000-0000-0000-0000-0000000000p1",
        share_id=sh.id,
        original_filename="pub.bin",
        mime_type="application/octet-stream",
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
    return f, created.record, created.plaintext_token


@pytest.mark.asyncio
async def test_a_public_probe_does_not_spend_the_link(client, db, public_budgeted):
    """The rule has to hold on both paths, or a client pointed at a public link
    hits the same wall the authenticated one just stopped hitting."""
    f, link, token = public_budgeted
    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-1"}
    )
    assert r.status_code in (200, 206), r.text
    db.refresh(link)
    assert link.downloads_remaining == 1


@pytest.mark.asyncio
async def test_a_public_range_download_is_still_charged(client, db, public_budgeted):
    """And the bypass this all started from stays closed on the public path."""
    f, link, token = public_budgeted
    r = await client.get(
        f"/api/public/{token}/files/{f.id}/download", headers={"Range": "bytes=1-"}
    )
    assert r.status_code in (200, 206), r.text
    db.refresh(link)
    assert link.downloads_remaining == 0


# --- the docstring that started it ------------------------------------------


def test_the_client_docstring_matches_the_server():
    """The audit's signature failure was a comment asserting a property the code
    does not have. This one asserted it, in the client, about the server."""
    from pathlib import Path

    src = Path("/repo/client/src/fileheron_client/api/download_resumable.py")
    if not src.exists():  # running outside the staged container layout
        src = (
            Path(__file__).resolve().parents[2]
            / "client/src/fileheron_client/api/download_resumable.py"
        )
    text = src.read_text()
    assert "A start > 0 is treated as an uncounted continuation." not in text, (
        "the client still documents the pre-v2.6.0 rule"
    )

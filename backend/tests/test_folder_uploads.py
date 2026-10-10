"""Folder uploads (v2.26.0): a file keeps the folder it was dropped from.

`original_filename` stays a bare leaf - the ZIP's `safe_arcname`, the desktop
client's `safe_join` and every Content-Disposition depend on that - and the
folder lives in `files.relative_dir`, written only through
utils/paths.py::safe_relative_dir. These pin the sanitiser, the two upload
routes, the order files are listed in, the archive layout, and the side paths
(quarantine, the admin browser, config-public) that had to learn about it.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import file as file_svc
from app.services import public_link as public_link_svc
from app.services import share as share_svc
from app.services.quarantine import _safe_filename
from app.services.zip_stream import ArcNames, build_zip_stream, safe_arcname
from app.utils.paths import MAX_DEPTH, MAX_SEGMENT, safe_relative_dir

PW = "Pass12345678!"


# --- the sanitiser ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("Photos/2026", "Photos/2026"),
        ("Photos\\2026\\Summer", "Photos/2026/Summer"),  # a Windows client
        ("/Photos/2026/", "Photos/2026"),  # Uppy's dropped-folder form
        ("Photos//./2026", "Photos/2026"),
        ("  Photos  / 2026 ", "Photos/2026"),
        ("Pho\x00tos/\x1f2026\x7f", "Photos/2026"),
        ("./.", None),
        # Refused outright: the file lands at the top level, not somewhere else.
        ("../etc", None),
        ("Photos/../../etc", None),
        ("Photos/../2026", None),
        ("Photos/.../x", None),  # Windows strips trailing dots: `...` is `..`
        ("C:/Users/x", None),
        ("c:\\Windows", None),
        ("/C:/x", None),
    ],
)
def test_the_sanitiser(raw, expected):
    assert safe_relative_dir(raw, width=1024) == expected


def test_a_segment_is_clipped_before_it_is_checked():
    """`..` + 253 spaces + `x` clips to a bare `..` - which must be seen and
    refused, not stored as a parent-directory hop."""
    assert safe_relative_dir(".." + " " * 253 + "x", width=1024) is None


def test_depth_and_width_keep_the_structure_rather_than_refusing():
    deep = "/".join(f"d{i}" for i in range(MAX_DEPTH + 10))
    assert safe_relative_dir(deep, width=4096) == "/".join(f"d{i}" for i in range(MAX_DEPTH))
    long_seg = "x" * (MAX_SEGMENT + 50)
    assert safe_relative_dir(long_seg, width=4096) == "x" * MAX_SEGMENT
    # Too wide: trailing folders are dropped, never cut mid-name.
    assert safe_relative_dir("aaaa/bbbb/cccc", width=9) == "aaaa/bbbb"
    assert safe_relative_dir("aaaa/bbbb/cccc", width=3) is None


# --- the upload routes --------------------------------------------------------


def _future() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


@pytest.fixture
def sender_and_share(make_user, db):
    sender = make_user(email="up@test.local", role=UserRole.admin, password=PW)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
    )
    db.commit()
    return sender, share


@pytest.mark.asyncio
async def test_a_direct_upload_keeps_its_folder(sender_and_share, db, client, login_as):
    _sender, share = sender_and_share
    token, _ = await login_as("up@test.local", PW)
    resp = await client.post(
        "/api/uploads/direct",
        data={"share_id": share.id, "relative_dir": "Reports\\2026"},
        files={"file": ("q3.pdf", b"%PDF-1.4", "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    row = db.query(File).filter(File.id == resp.json()["file_id"]).one()
    assert (row.relative_dir, row.original_filename) == ("Reports/2026", "q3.pdf")


@pytest.mark.asyncio
async def test_a_resumable_upload_keeps_its_folder_and_a_traversal_lands_on_top(
    sender_and_share, db, client, login_as
):
    _sender, share = sender_and_share
    token, _ = await login_as("up@test.local", PW)
    auth = {"Authorization": f"Bearer {token}"}
    ok = await client.post(
        "/api/uploads/init",
        json={"share_id": share.id, "filename": "a.jpg", "size_bytes": 10, "relative_dir": "/Photos/Summer/"},
        headers=auth,
    )
    bad = await client.post(
        "/api/uploads/init",
        json={"share_id": share.id, "filename": "b.jpg", "size_bytes": 10, "relative_dir": "../../etc"},
        headers=auth,
    )
    assert ok.status_code == 200 and bad.status_code == 200, (ok.text, bad.text)
    rows = {f.original_filename: f.relative_dir for f in db.query(File).all()}
    assert rows == {"a.jpg": "Photos/Summer", "b.jpg": None}


@pytest.mark.asyncio
async def test_without_a_folder_nothing_changes(sender_and_share, db, client, login_as):
    """Every client that predates folder uploads sends no field at all."""
    _sender, share = sender_and_share
    token, _ = await login_as("up@test.local", PW)
    resp = await client.post(
        "/api/uploads/direct",
        data={"share_id": share.id},
        files={"file": ("x/y/plain.txt", b"x", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    row = db.query(File).one()
    assert (row.relative_dir, row.original_filename) == (None, "plain.txt")


# --- listing order + the responses --------------------------------------------


def _add(db, share, owner, name, folder=None, *, fid):
    f = File(
        id=f"00000000-0000-0000-0000-{fid:012d}",
        share_id=share.id,
        original_filename=name,
        relative_dir=folder,
        mime_type="text/plain",
        size_bytes=1,
        storage_path=None,
        state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return f


def _seed_tree(db, share, owner):
    # Inserted out of order on purpose: the listing must not depend on it.
    _add(db, share, owner, "b.txt", "Docs", fid=1)
    _add(db, share, owner, "z.txt", None, fid=2)
    _add(db, share, owner, "A.txt", "docs/Inner", fid=3)
    _add(db, share, owner, "a.txt", None, fid=4)
    _add(db, share, owner, "a.txt", "Docs", fid=5)


_EXPECTED = [(None, "a.txt"), (None, "z.txt"), ("Docs", "a.txt"), ("Docs", "b.txt"), ("docs/Inner", "A.txt")]


@pytest.mark.asyncio
async def test_the_share_lists_top_level_first_then_by_folder_and_name(
    sender_and_share, db, client, login_as
):
    sender, share = sender_and_share
    _seed_tree(db, share, sender)
    token, _ = await login_as("up@test.local", PW)
    resp = await client.get(f"/api/shares/{share.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    got = [(f["relative_dir"], f["original_filename"]) for f in resp.json()["files"]]
    assert got == _EXPECTED


@pytest.mark.asyncio
async def test_the_public_link_page_lists_the_same_tree(sender_and_share, db, client):
    sender, share = sender_and_share
    _seed_tree(db, share, sender)
    created = public_link_svc.create_link(
        db, actor=sender, share=share, password=None, download_limit=None, notify_on_download=False
    )
    db.commit()
    resp = await client.get(f"/api/public/{created.plaintext_token}")
    assert resp.status_code == 200, resp.text
    got = [(f["relative_dir"], f["original_filename"]) for f in resp.json()["files"]]
    assert got == _EXPECTED


@pytest.mark.asyncio
async def test_the_admin_file_browser_shows_and_searches_the_folder(
    sender_and_share, make_user, db, client, login_as
):
    sender, share = sender_and_share
    _add(db, share, sender, "q3.pdf", "Finance/Quarterly", fid=7)
    _add(db, share, sender, "other.pdf", None, fid=8)
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    resp = await client.get(
        "/api/admin/files", params={"q": "quarterly"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [(i["filename"], i["relative_dir"]) for i in items] == [("q3.pdf", "Finance/Quarterly")]


@pytest.mark.asyncio
async def test_config_public_advertises_folder_paths(client):
    resp = await client.get("/api/config-public")
    assert resp.status_code == 200
    assert resp.json()["folder_paths"] is True


# --- the archive --------------------------------------------------------------


def test_members_keep_their_folder():
    taken = ArcNames()
    assert safe_arcname("a.jpg", taken, folder="Photos/2026") == "Photos/2026/a.jpg"
    assert safe_arcname("a.jpg", taken) == "a.jpg"


def test_duplicates_are_found_case_insensitively_across_the_path():
    taken = ArcNames()
    assert safe_arcname("Report.pdf", taken, folder="Docs") == "Docs/Report.pdf"
    # Same folder in another case is the same folder, as it is on Windows.
    assert safe_arcname("report.pdf", taken, folder="docs") == "Docs/report (1).pdf"
    assert safe_arcname("REPORT.PDF", taken) == "REPORT.PDF"
    assert safe_arcname("report.pdf", taken) == "report (1).pdf"


def test_a_file_never_takes_a_folders_name_and_the_other_way_round():
    taken = ArcNames()
    assert safe_arcname("x.txt", taken, folder="Notes") == "Notes/x.txt"
    assert safe_arcname("notes", taken) == "notes (1)"  # a file named like the folder
    taken2 = ArcNames()
    assert safe_arcname("Notes", taken2) == "Notes"
    assert safe_arcname("x.txt", taken2, folder="notes") == "notes (1)/x.txt"
    # ... and every later member of that folder follows it.
    assert safe_arcname("y.txt", taken2, folder="Notes") == "notes (1)/y.txt"


def test_a_folder_that_tries_to_leave_the_archive_is_dropped():
    taken = ArcNames()
    assert safe_arcname("evil.txt", taken, folder="../../etc") == "evil.txt"
    assert safe_arcname("x.txt", taken, folder="C:/Windows") == "x.txt"


def test_the_archive_carries_the_tree(tmp_path):
    def mk(name, folder, data):
        p = tmp_path / f"{len(list(tmp_path.iterdir()))}.bin"
        p.write_bytes(data)
        return File(original_filename=name, relative_dir=folder, storage_path=str(p), size_bytes=len(data))

    files = [mk("a.txt", "Docs/2026", b"one"), mk("a.txt", None, b"two"), mk("A.TXT", "docs/2026", b"three")]
    zs = build_zip_stream(files)
    declared = len(zs)
    buf = b"".join(zs)
    assert declared == len(buf)
    zf = zipfile.ZipFile(io.BytesIO(buf))
    assert zf.namelist() == ["Docs/2026/a.txt", "a.txt", "Docs/2026/A (1).TXT"]
    assert zf.testzip() is None
    assert zf.read("Docs/2026/A (1).TXT") == b"three"


# --- side paths ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["..", ".", "...", ""])
def test_the_quarantine_name_is_never_a_directory_hop(name):
    assert _safe_filename(name) == "untitled.bin"


def test_display_order_is_total():
    class F:
        def __init__(self, fid, name, folder):
            self.id, self.original_filename, self.relative_dir = fid, name, folder

    rows = [F("2", "a", None), F("1", "a", None), F("3", "a", "x")]
    assert [f.id for f in file_svc.display_order(rows)] == ["1", "2", "3"]

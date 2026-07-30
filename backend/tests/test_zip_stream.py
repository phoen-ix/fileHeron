"""Streaming ZIP builder: arcname sanitization + sized round-trip."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.models.file import File
from app.services.zip_stream import build_zip_stream, safe_arcname


def test_safe_arcname_strips_path_and_dedupes():
    taken: set[str] = set()
    assert safe_arcname("../../etc/passwd", taken) == "passwd"
    assert safe_arcname("a/b/c/report.pdf", taken) == "report.pdf"
    # collision on the same basename → suffixed, not overwritten
    assert safe_arcname("x/report.pdf", taken) == "report (1).pdf"
    assert safe_arcname("report.pdf", taken) == "report (2).pdf"
    # empty / null-only falls back
    assert safe_arcname("\x00", taken) == "file"


def _mk(tmp_path, name: str, data: bytes) -> File:
    p = tmp_path / name
    p.write_bytes(data)
    return File(original_filename=name, storage_path=str(p), size_bytes=len(data))


def test_build_zip_stream_sized_roundtrip(tmp_path):
    files = [
        _mk(tmp_path, "a.txt", b"hello world" * 1000),
        _mk(tmp_path, "b.bin", b"\x00\x01\x02" * 5000),
    ]
    zs = build_zip_stream(files)

    declared_len = len(zs)  # what we'd send as Content-Length
    buf = bytearray()
    for chunk in zs:
        buf += chunk

    # Exact length match is the whole point - enables a real Content-Length.
    assert declared_len == len(buf)

    zf = zipfile.ZipFile(io.BytesIO(bytes(buf)))
    assert zf.namelist() == ["a.txt", "b.bin"]
    assert zf.testzip() is None  # every entry's CRC verifies
    assert zf.read("a.txt") == b"hello world" * 1000
    # STORED, not deflated
    assert all(zi.compress_type == zipfile.ZIP_STORED for zi in zf.infolist())


def test_build_zip_stream_sanitizes_member_names(tmp_path):
    files = [_mk(tmp_path, "evil.txt", b"x")]
    files[0].original_filename = "../../../../etc/passwd"
    zs = build_zip_stream(files)
    list(zs)  # drain
    # rebuild to read names (a sized ZipStream is single-use once drained)
    zs2 = build_zip_stream(files)
    buf = b"".join(zs2)
    assert zipfile.ZipFile(io.BytesIO(buf)).namelist() == ["passwd"]


# --- zip_writer replacement coverage (audit 2026-07-30) ---------------------
#
# zipstream-ng (LGPL-3.0-only) was replaced by services/zip_writer.py so the
# MIT-licensed backend has no copyleft runtime dependency. These pin the
# properties the download path depends on. The load-bearing one is
# declared-length == streamed-length: Content-Length is promised to the browser
# before a byte is sent, so any drift hangs or truncates every bulk download.


def _zs(members: dict[str, bytes]):
    from app.services.zip_writer import SizedZipStream

    zs = SizedZipStream()
    for name, blob in members.items():
        zs.add_stream((lambda b=blob: io.BytesIO(b)), name, len(blob))
    return zs


def _roundtrip(members: dict[str, bytes]):
    zs = _zs(members)
    declared = len(zs)
    buf = b"".join(zs)
    assert declared == len(buf), "declared Content-Length != streamed bytes"
    zf = zipfile.ZipFile(io.BytesIO(buf))
    assert zf.testzip() is None
    return zf


def test_zip_writer_empty_member():
    zf = _roundtrip({"empty.dat": b"", "x.txt": b"x"})
    assert zf.read("empty.dat") == b""


def test_zip_writer_unicode_names_roundtrip():
    zf = _roundtrip({"grüße.txt": "föö".encode(), "日本語.bin": b"\x00\x01"})
    assert zf.read("grüße.txt") == "föö".encode()
    assert set(zf.namelist()) == {"grüße.txt", "日本語.bin"}


def test_zip_writer_many_members():
    members = {f"file-{i:03d}.txt": f"content {i}".encode() for i in range(120)}
    zf = _roundtrip(members)
    assert len(zf.infolist()) == 120
    assert zf.read("file-119.txt") == b"content 119"


def test_zip_writer_no_members():
    """A share whose files were all filtered out still has to produce a valid,
    correctly-sized (empty) archive rather than a malformed one."""
    zf = _roundtrip({})
    assert zf.namelist() == []


def test_zip_writer_declared_length_survives_duplicate_names(tmp_path):
    """safe_arcname de-dupes, and the length must be computed from the FINAL
    names - a rename after sizing would silently break Content-Length."""
    (tmp_path / "sub").mkdir()
    files = [
        _mk(tmp_path, "dup.txt", b"a" * 10),
        _mk(tmp_path / "sub", "dup.txt", b"b" * 20),
    ]
    zs = build_zip_stream(files)
    declared = len(zs)
    buf = b"".join(zs)
    assert declared == len(buf)
    zf = zipfile.ZipFile(io.BytesIO(buf))
    assert sorted(zf.namelist()) == ["dup (1).txt", "dup.txt"]


def test_zip_writer_raises_when_a_member_is_short():
    """If a source yields fewer bytes than declared, Content-Length is already
    committed - fail loudly rather than hand back a complete-looking archive
    whose contents are not what was promised."""
    from app.services.zip_writer import SizedZipStream, ZipSizeMismatchError

    zs = SizedZipStream()
    zs.add_stream(lambda: io.BytesIO(b"short"), "a.txt", 999)
    with pytest.raises(ZipSizeMismatchError):
        b"".join(zs)


def test_zip_writer_is_zip64_so_large_members_are_representable():
    """Members are 30 GB-capable by design; zip64 must be on unconditionally,
    not decided by a size threshold."""
    zf = _roundtrip({"small.txt": b"x"})
    # extract_version 45 == the zip64-capable version marker
    assert all(zi.extract_version >= 45 for zi in zf.infolist())

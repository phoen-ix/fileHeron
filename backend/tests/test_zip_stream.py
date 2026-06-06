"""Streaming ZIP builder: arcname sanitization + sized round-trip."""
from __future__ import annotations

import io
import zipfile

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

    # Exact length match is the whole point — enables a real Content-Length.
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

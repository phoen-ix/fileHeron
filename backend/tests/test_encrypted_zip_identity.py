"""A ZIP over encrypted members is byte-identical to the plaintext one.

The archive is resumable, so its bytes are load-bearing: an If-Range resume
across a change would splice two archives. Encrypted members go in through
add_stream with the row's plaintext size and a seekable decrypting reader, so
the archive - and its signature, length, CRCs - must not change when a share's
files are encrypted. Asserted on the PRODUCED BYTES, never on LAYOUT_VERSION.
"""
from __future__ import annotations

import io
import os
import zipfile
import zlib

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services.zip_stream import build_zip_stream
from app.utils import file_crypto
from tests._encryption_helpers import encrypt_row, store_plain

C = file_crypto.CHUNK_SIZE
CONTENTS = [os.urandom(2 * C + 333), b"", os.urandom(4097), os.urandom(C)]


class _Crcs:
    def __init__(self):
        self.d: dict[str, int] = {}

    def get(self, key):
        return self.d.get(key)

    def put(self, key, crc):
        self.d[key] = crc


@pytest.fixture
def share_files(db, make_user):
    u = make_user(email="z@test.local", role=UserRole.employee)
    sh = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    rows = []
    for i, data in enumerate(CONTENTS):
        f = File(share_id=sh.id, original_filename=f"m{i}.bin", size_bytes=len(data),
                 uploaded_by_id=u.id, state=FileState.clean, storage_path=store_plain(data))
        db.add(f)
        rows.append(f)
    db.commit()
    return rows


def _archive(rows, crcs=None):
    zs = build_zip_stream(rows, mtime=1_700_000_000, crc_cache=crcs)
    return zs, b"".join(zs)


def test_encrypting_the_members_changes_no_byte(db, share_files):
    zs_plain, plain = _archive(share_files)
    for f in share_files[::2]:  # a mix: members 0 and 2 encrypted
        encrypt_row(db, f)
    zs_mixed, mixed = _archive(share_files)
    for f in share_files[1::2]:
        encrypt_row(db, f)
    zs_enc, enc = _archive(share_files)

    assert mixed == plain and enc == plain
    assert zs_enc.signature() == zs_mixed.signature() == zs_plain.signature()
    assert len(zs_enc) == len(enc) == len(plain)
    with zipfile.ZipFile(io.BytesIO(enc)) as z:
        for info, data in zip(z.infolist(), CONTENTS, strict=True):
            assert z.read(info) == data
            assert zlib.crc32(data) == info.CRC


def test_a_resume_inside_any_member_matches(db, share_files):
    _zs, full = _archive(share_files)
    for f in share_files:
        encrypt_row(db, f)
    zs, _ = _archive(share_files)
    for offset in [1, 100, C - 1, C + 5, 2 * C + 300, len(full) - 200, len(full) - 1]:
        assert b"".join(zs.iter_from(offset)) == full[offset:], offset


def test_with_crcs_cached_a_resume_costs_nothing_and_still_matches(db, share_files):
    for f in share_files:
        encrypt_row(db, f)
    crcs = _Crcs()
    zs, full = _archive(share_files, crcs)  # a full transfer caches every CRC
    assert set(crcs.d) == {f.id for f in share_files}
    zs, _ = _archive(share_files, crcs)
    for offset in [C + 5, 2 * C + 300, len(full) - 10]:
        assert zs.resume_cost(offset) == 0, offset
        assert b"".join(zs.iter_from(offset)) == full[offset:], offset

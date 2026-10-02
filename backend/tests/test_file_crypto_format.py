"""utils/file_crypto.py - the v1 at-rest format.

Every reader is checked against a plaintext oracle, and every way of damaging a
file - a flipped bit, a truncation (mid-chunk or on a chunk boundary), reordered
chunks, a chunk from another file, the wrong key - must fail authentication
rather than return wrong bytes.
"""
from __future__ import annotations

import io
import os
import random
import threading
import zlib

import pytest

from app.utils import file_crypto as fc

C = fc.CHUNK_SIZE
SIZES = [0, 1, C - 1, C, C + 1, 3 * C + 7]


def _opener(blob: bytes, calls: list | None = None):
    def _open(a: int, b: int):
        if calls is not None:
            calls.append((a, b))
        return io.BytesIO(blob[a : b + 1])
    return _open


def _encrypt(data: bytes, key: bytes | None = None) -> tuple[bytes, bytes]:
    key = key or fc.new_data_key()
    return fc.encrypt_bytes(data, key), key


@pytest.fixture(scope="module")
def plain() -> bytes:
    return os.urandom(3 * C + 7)


@pytest.mark.parametrize("size", SIZES)
def test_round_trip_and_size_formula(plain, size):
    data = plain[:size]
    ct, key = _encrypt(data)
    assert len(ct) == fc.ciphertext_size(size) == fc.HEADER_SIZE + size + 16 * fc.chunk_count(size)
    assert ct.startswith(fc.HEADER)
    assert b"".join(fc.iter_plaintext(_opener(ct), key, size)) == data
    assert fc.DecryptingReader(_opener(ct), key, size).read() == data


def test_the_header_is_checked_and_constant(plain):
    ct, _key = _encrypt(plain[:10])
    assert ct[: fc.HEADER_SIZE] == fc.HEADER == bytes.fromhex("4648456e630001140000000000000000")
    fc.verify_header(_opener(ct))
    with pytest.raises(fc.UnsupportedFormatError):
        fc.verify_header(_opener(b"XXXXX" + ct[5:]))


@pytest.mark.parametrize("where", ["body", "tag", "second chunk"])
def test_a_flipped_bit_fails(plain, where):
    data = plain[: 2 * C + 5]
    ct, key = _encrypt(data)
    pos = {"body": fc.HEADER_SIZE + 3, "tag": fc.chunk_offset(1) - 1, "second chunk": fc.chunk_offset(1) + 9}[where]
    bad = bytearray(ct)
    bad[pos] ^= 0x01
    with pytest.raises(fc.FileIntegrityError):
        b"".join(fc.iter_plaintext(_opener(bytes(bad)), key, len(data)))


def test_truncation_fails_mid_chunk_and_on_a_boundary(plain):
    data = plain[: 2 * C + 5]
    ct, key = _encrypt(data)
    for cut in (len(ct) - 1, fc.chunk_offset(2)):
        with pytest.raises(fc.FileIntegrityError):
            b"".join(fc.iter_plaintext(_opener(ct[:cut]), key, len(data)))


def test_dropping_the_final_chunks_fails_on_the_final_flag(plain):
    """Claim a 3-chunk file is 2 chunks long: chunk 1 was sealed as NOT final,
    so it fails where the reader now expects the last one."""
    data = plain[: 2 * C + 5]
    ct, key = _encrypt(data)
    with pytest.raises(fc.FileIntegrityError):
        b"".join(fc.iter_plaintext(_opener(ct[: fc.chunk_offset(2)]), key, 2 * C))


def test_reordered_chunks_fail(plain):
    data = plain[: 2 * C + 5]
    ct, key = _encrypt(data)
    c0 = ct[fc.chunk_offset(0) : fc.chunk_offset(1)]
    c1 = ct[fc.chunk_offset(1) : fc.chunk_offset(2)]
    swapped = fc.HEADER + c1 + c0 + ct[fc.chunk_offset(2) :]
    with pytest.raises(fc.FileIntegrityError):
        b"".join(fc.iter_plaintext(_opener(swapped), key, len(data)))


def test_a_chunk_from_another_file_or_the_wrong_key_fails(plain):
    data = plain[: C + 5]
    ct_a, key_a = _encrypt(data)
    ct_b, _key_b = _encrypt(data)
    spliced = ct_a[: fc.chunk_offset(1)] + ct_b[fc.chunk_offset(1) :]
    with pytest.raises(fc.FileIntegrityError):
        b"".join(fc.iter_plaintext(_opener(spliced), key_a, len(data)))
    with pytest.raises(fc.FileIntegrityError):
        b"".join(fc.iter_plaintext(_opener(ct_a), fc.new_data_key(), len(data)))


def test_every_key_is_fresh():
    assert len({fc.new_data_key() for _ in range(50)}) == 50


def test_ranges_decrypt_only_their_window(plain):
    size = len(plain)
    ct, key = _encrypt(plain)
    rnd = random.Random(7)
    edges = [0, 1, C - 1, C, C + 1, 2 * C, size - 1]
    cases = [(a, b) for a in edges for b in edges if a <= b] + [
        tuple(sorted(rnd.sample(range(size), 2))) for _ in range(40)
    ]
    for start, end in cases:
        calls: list = []
        got = b"".join(fc.iter_plaintext(_opener(ct, calls), key, size, start, end))
        assert got == plain[start : end + 1], (start, end)
        ct_start, ct_end, _first = fc.ciphertext_window(start, end, size)
        assert calls == [(ct_start, ct_end)], "one opener call, exactly the window"


def test_the_seekable_reader_matches_a_plaintext_oracle(plain):
    ct, key = _encrypt(plain)
    reader = fc.DecryptingReader(_opener(ct), key, len(plain))
    oracle = io.BytesIO(plain)
    rnd = random.Random(11)
    for _ in range(300):
        op = rnd.random()
        if op < 0.4:
            off = rnd.randrange(0, len(plain) + 10)
            whence = rnd.choice([0, 1, 2])
            off = {0: off, 1: rnd.randrange(-oracle.tell(), 20), 2: -rnd.randrange(0, len(plain))}[whence]
            assert reader.seek(off, whence) == oracle.seek(off, whence)
        else:
            n = rnd.choice([1, 7, 4096, C - 1, C + 3, -1])
            assert reader.read(n) == oracle.read(n)
        assert reader.tell() == oracle.tell()


def test_the_reader_is_lazy_until_read(plain):
    ct, key = _encrypt(plain[:10])
    calls: list = []
    reader = fc.DecryptingReader(_opener(ct, calls), key, 10)
    assert reader.seekable() and reader.readable()
    reader.seek(5)
    assert calls == [], "zip_writer only asks seekable(); that must cost nothing"
    assert reader.read() == plain[5:10]
    assert len(calls) == 1


def test_the_encrypting_reader_streams_and_describes_its_input(plain):
    data = plain[: 2 * C + 5]
    key = fc.new_data_key()
    reader = fc.EncryptingReader(io.BytesIO(data), key, len(data))
    out = bytearray()
    while chunk := reader.read(65536):
        out += chunk
    assert bytes(out) == fc.encrypt_bytes(data, key)
    assert reader.crc32 == zlib.crc32(data) and reader.plaintext_read == len(data)


def test_a_source_of_the_wrong_size_is_refused(plain):
    key = fc.new_data_key()
    with pytest.raises(fc.FileIntegrityError, match="shorter"):
        fc.EncryptingReader(io.BytesIO(plain[:10]), key, 11).read()
    with pytest.raises(fc.FileIntegrityError, match="longer"):
        fc.EncryptingReader(io.BytesIO(plain[:12]), key, 11).read()


def test_cancel_stops_between_chunks(plain):
    cancel = threading.Event()
    reader = fc.EncryptingReader(io.BytesIO(plain), fc.new_data_key(), len(plain), cancel=cancel)
    reader.read(fc.HEADER_SIZE + 10)
    cancel.set()
    with pytest.raises(fc.FileCryptoError, match="cancelled"):
        reader.read()


def test_an_empty_file_still_authenticates():
    ct, key = _encrypt(b"")
    assert list(fc.iter_plaintext(_opener(ct), key, 0)) == []
    with pytest.raises(fc.FileIntegrityError):
        list(fc.iter_plaintext(_opener(ct[:-1] + bytes([ct[-1] ^ 1])), key, 0))

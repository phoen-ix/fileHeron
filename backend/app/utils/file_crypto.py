"""Chunked authenticated encryption for stored files - format v1.

Encryption at rest is opt-in (services/file_encryption.py). A file is split
into 1 MiB plaintext chunks, each sealed with AES-256-GCM under a per-file data
key, behind a constant 16-byte header:

    header  = b"FHEnc\\0" | version (1) | log2(chunk size) (20) | 8 reserved
    chunk i = AES-GCM(key, nonce=i as 12 big-endian bytes,
                      aad=header | i as 8 big-endian bytes | final-flag byte)

Why this shape:
- Random access. Range requests, the desktop client's 16 MiB segments and ZIP
  resume all need byte N without decrypting bytes 0..N-1; chunk i of a P-byte
  file starts at a computable ciphertext offset. Fernet (used for every other
  secret here) encrypts a whole message at once and cannot seek.
- A counter nonce is safe because a key is NEVER reused: every encryption -
  first, retry, re-encryption - draws a fresh key (`new_data_key`).
- The chunk index and final flag in the AAD make reordering, dropping and
  truncating chunks fail authentication; a chunk from another file fails under
  that file's own key. The plaintext size is not in the file: it is the row's
  `size_bytes`, and every reader checks the ciphertext against it.

Pure: no database, no storage backend, no policy. The readers take an
`opener(ct_start, ct_end_incl) -> stream` so the same code serves local files
and S3 ranged reads.
"""
from __future__ import annotations

import contextlib
import os
import threading
import zlib
from collections.abc import Callable, Iterator
from typing import BinaryIO, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = 1
CHUNK_LOG2 = 20
CHUNK_SIZE = 1 << CHUNK_LOG2
TAG_SIZE = 16
KEY_SIZE = 32
HEADER = b"FHEnc\x00" + bytes([FORMAT_VERSION, CHUNK_LOG2]) + bytes(8)
HEADER_SIZE = len(HEADER)


class FileCryptoError(Exception):
    """An encrypted file could not be produced or read."""


class FileIntegrityError(FileCryptoError):
    """The ciphertext does not authenticate, or does not match the recorded
    size: tampered with, truncated, extended, or the wrong key."""


class UnsupportedFormatError(FileCryptoError):
    """The file does not start with a header this code knows."""


class EncryptionCancelledError(FileCryptoError):
    """The caller asked the encryption to stop (worker shutdown). Not a fault
    of the file: the lanes leave it to the next run instead of giving up."""


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


Opener = Callable[[int, int], BinaryIO]


def new_data_key() -> bytes:
    """A fresh 256-bit key. One per encryption - the counter nonce relies on it."""
    return os.urandom(KEY_SIZE)


def chunk_count(size: int) -> int:
    """Chunks for a `size`-byte plaintext. An empty file is one empty chunk, so
    it still carries a tag and cannot be confused with a missing one."""
    if size < 0:
        raise ValueError("size must not be negative")
    return max(1, -(-size // CHUNK_SIZE))


def ciphertext_size(size: int) -> int:
    return HEADER_SIZE + size + TAG_SIZE * chunk_count(size)


def chunk_offset(i: int) -> int:
    """Ciphertext offset where chunk `i` begins."""
    return HEADER_SIZE + i * (CHUNK_SIZE + TAG_SIZE)


def _plain_len(i: int, size: int) -> int:
    return max(0, min(CHUNK_SIZE, size - i * CHUNK_SIZE))


def ciphertext_window(start: int, end_incl: int, size: int) -> tuple[int, int, int]:
    """The ciphertext bytes holding plaintext `[start, end_incl]`: whole chunks,
    as `(ct_start, ct_end_incl, first_chunk)`."""
    if not 0 <= start <= end_incl < size:
        raise ValueError(f"range {start}-{end_incl} is outside a {size}-byte file")
    first, last = start // CHUNK_SIZE, end_incl // CHUNK_SIZE
    return chunk_offset(first), chunk_offset(last) + _plain_len(last, size) + TAG_SIZE - 1, first


def _nonce(i: int) -> bytes:
    return i.to_bytes(12, "big")


def _aad(i: int, final: bool) -> bytes:
    return HEADER + i.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


def _seal(aes: AESGCM, i: int, n: int, data: bytes) -> bytes:
    return aes.encrypt(_nonce(i), data, _aad(i, i == n - 1))


def _unseal(aes: AESGCM, i: int, n: int, blob: bytes) -> bytes:
    try:
        return aes.decrypt(_nonce(i), blob, _aad(i, i == n - 1))
    except InvalidTag as e:
        raise FileIntegrityError(f"chunk {i} failed authentication") from e


def _read_exactly(src: _Readable, n: int) -> bytes:
    """Up to `n` bytes, looping over short reads (an S3 body may return less
    than asked). Shorter only at end of stream."""
    if n <= 0:
        return b""
    parts: list[bytes] = []
    got = 0
    while got < n:
        part = src.read(n - got)
        if not part:
            break
        parts.append(part)
        got += len(part)
    return b"".join(parts)


class EncryptingReader:
    """Reads as the ciphertext of `src`: header, then each sealed chunk.

    Streams one chunk at a time - a 30 GB file never sits in memory - so it can
    be handed to `shutil.copyfileobj` or S3's `upload_fileobj`. It insists that
    `src` holds exactly `size` bytes (it reads one byte past the end to be
    sure): encrypting a file that is not the size its row records would make
    every later read fail. `crc32` and `plaintext_read` describe the plaintext
    it consumed. `cancel` stops it between chunks.
    """

    def __init__(
        self,
        src: _Readable,
        dek: bytes,
        size: int,
        *,
        cancel: threading.Event | None = None,
    ) -> None:
        self._src = src
        self._aes = AESGCM(dek)
        self._size = size
        self._n = chunk_count(size)
        self._next = 0
        self._tail_checked = False
        self._buf = bytearray(HEADER)
        self._cancel = cancel
        self.crc32 = 0
        self.plaintext_read = 0

    def readable(self) -> bool:
        return True

    def _exhausted(self) -> bool:
        return self._next >= self._n and self._tail_checked

    def _fill(self) -> None:
        if self._next >= self._n:
            if _read_exactly(self._src, 1):
                raise FileIntegrityError("the source is longer than its recorded size")
            self._tail_checked = True
            return
        if self._cancel is not None and self._cancel.is_set():
            raise EncryptionCancelledError("encryption cancelled")
        want = _plain_len(self._next, self._size)
        data = _read_exactly(self._src, want)
        if len(data) != want:
            raise FileIntegrityError("the source is shorter than its recorded size")
        self.crc32 = zlib.crc32(data, self.crc32)
        self.plaintext_read += len(data)
        self._buf += _seal(self._aes, self._next, self._n, data)
        self._next += 1

    def read(self, size: int = -1) -> bytes:
        while (size < 0 or len(self._buf) < size) and not self._exhausted():
            self._fill()
        if size < 0:
            out = bytes(self._buf)
            self._buf.clear()
            return out
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out


def encrypt_bytes(data: bytes, dek: bytes) -> bytes:
    """The whole ciphertext of a small in-memory plaintext (tests, attachments)."""
    import io

    return EncryptingReader(io.BytesIO(data), dek, len(data)).read()


def iter_plaintext(
    opener: Opener,
    dek: bytes,
    size: int,
    start: int = 0,
    end_incl: int | None = None,
) -> Iterator[bytes]:
    """Yield the plaintext `[start, end_incl]` (default: all of it), opening
    exactly the ciphertext window that holds it with ONE `opener` call. A
    synchronous generator: Starlette runs it in a worker thread, as it does
    every other streamed body here."""
    aes = AESGCM(dek)
    n = chunk_count(size)
    if size == 0:
        # Nothing to yield, but an empty file still has a chunk to authenticate.
        with contextlib.closing(opener(HEADER_SIZE, HEADER_SIZE + TAG_SIZE - 1)) as src:
            _unseal(aes, 0, n, _read_exactly(src, TAG_SIZE))
        return
    end = size - 1 if end_incl is None else end_incl
    ct_start, ct_end, first = ciphertext_window(start, end, size)
    last = end // CHUNK_SIZE
    with contextlib.closing(opener(ct_start, ct_end)) as src:
        for i in range(first, last + 1):
            plen = _plain_len(i, size)
            blob = _read_exactly(src, plen + TAG_SIZE)
            if len(blob) != plen + TAG_SIZE:
                raise FileIntegrityError(f"the ciphertext ends inside chunk {i}")
            data = _unseal(aes, i, n, blob)
            lo = start - i * CHUNK_SIZE if i == first else 0
            hi = end - i * CHUNK_SIZE + 1 if i == last else plen
            yield data[lo:hi]


def verify_header(opener: Opener) -> None:
    """Raise unless the stored file starts with the v1 header."""
    with contextlib.closing(opener(0, HEADER_SIZE - 1)) as src:
        got = _read_exactly(src, HEADER_SIZE)
    if got != HEADER:
        raise UnsupportedFormatError("not a file:Heron v1 encrypted file")


class DecryptingReader:
    """A seekable, readable view of the plaintext of an encrypted file.

    Lazy: nothing is opened until the first read, because zip_writer opens a
    member's reader only to ask `seekable()` - on S3 an eager reader would cost
    a GetObject per member per resume probe. A seek just moves the position;
    the next read opens the ciphertext at the chunk that holds it and then reads
    on sequentially from that one stream.
    """

    def __init__(self, opener: Opener, dek: bytes, size: int) -> None:
        self._opener = opener
        self._aes = AESGCM(dek)
        self._size = size
        self._n = chunk_count(size)
        self._pos = 0
        self._src: BinaryIO | None = None
        self._src_next: int | None = None  # chunk the open stream delivers next
        self._cur_index: int | None = None
        self._cur = b""
        self.closed = False

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            new = offset
        elif whence == 1:
            new = self._pos + offset
        elif whence == 2:
            new = self._size + offset
        else:
            raise ValueError(f"invalid whence {whence}")
        if new < 0:
            raise ValueError("negative seek position")
        self._pos = new
        return new

    def _close_src(self) -> None:
        if self._src is not None:
            with contextlib.suppress(Exception):
                self._src.close()
        self._src = None
        self._src_next = None

    def _chunk(self, i: int) -> bytes:
        if self._cur_index == i:
            return self._cur
        if self._src is None or self._src_next != i:
            self._close_src()
            self._src = self._opener(chunk_offset(i), ciphertext_size(self._size) - 1)
            self._src_next = i
        plen = _plain_len(i, self._size)
        blob = _read_exactly(self._src, plen + TAG_SIZE)
        if len(blob) != plen + TAG_SIZE:
            raise FileIntegrityError(f"the ciphertext ends inside chunk {i}")
        self._cur = _unseal(self._aes, i, self._n, blob)
        self._cur_index = i
        self._src_next = i + 1
        return self._cur

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("read from a closed reader")
        if self._pos >= self._size:
            return b""
        end = self._size if size is None or size < 0 else min(self._size, self._pos + size)
        out = bytearray()
        while self._pos < end:
            i = self._pos // CHUNK_SIZE
            data = self._chunk(i)
            off = self._pos - i * CHUNK_SIZE
            take = min(len(data) - off, end - self._pos)
            out += data[off : off + take]
            self._pos += take
        return bytes(out)

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def close(self) -> None:
        self._close_src()
        self.closed = True

    def __enter__(self) -> DecryptingReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

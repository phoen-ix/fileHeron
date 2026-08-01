"""Streaming ZIP writer with an exact, pre-computable archive length.

Replaces `zipstream-ng`, which is LGPL-3.0-only and was a module-scope runtime
import of this MIT-licensed backend, shipping inside every published GHCR image
(audit 2026-07-30). Same reasoning that removed GPL tkcalendar from the desktop
client; LGPL would have permitted use with prominent notice, but the project's
rule is permissive-only dependencies.

WHY AN EXACT LENGTH IS POSSIBLE
-------------------------------
ZIP normally needs each member's CRC-32 in the *local file header*, which
precedes the data - forcing a full read of every file before a single byte can
be sent. That is unusable for 30 GB uploads.

The escape hatch is the general-purpose **data-descriptor flag** (bit 3): the
local header carries zeroes, the real CRC and sizes follow the data in a
fixed-size trailer. Because entries are STORED (no compression), every field's
length is known from the file names and sizes alone, so `len(stream)` is exact
before anything is read. `Content-Length` is therefore real, which is what lets
the browser show true progress.

WHY THE ARCHIVE IS SEEKABLE
---------------------------
The same arithmetic that makes the length exact makes any byte offset
addressable: block boundaries are a prefix sum over name lengths and member
sizes, so `iter_from(n)` finds its starting block in O(entries) with no
generate-and-discard. That is what lets a 9 GB archive whose transfer died at
90% be *resumed* rather than restarted (audit 2026-07-30, flow-publiclink-5).

Two properties the resume depends on, and which are therefore load-bearing:

- **Reproducibility.** Two generations of the same member list must be
  byte-identical, or the second half of a resumed download belongs to a
  different archive than the first. Hence the DOS timestamp comes from the
  caller (the share's creation time) rather than the clock, and is rendered in
  UTC rather than the container's local time.
- **CRC availability.** A member's CRC lands in its data descriptor and again in
  the central directory, both of which are normally produced as a side effect of
  reading the member. Resuming past a member means its CRC is needed without
  reading it, so an optional `crc_cache` is consulted; on a miss the member is
  re-read to recompute it. `resume_cost()` reports how many bytes that would
  take so a caller can decline and serve the full archive instead. There is no
  path that emits a guessed CRC - a resume is either exact or refused.

ZIP64 IS UNCONDITIONAL
----------------------
Members up to 30 GB and archives past 4 GB are this product's normal case, not
an edge case, and the descriptor's own width depends on whether zip64 is in use.
Deciding per-entry would make the length depend on a threshold check; always
emitting zip64 keeps the arithmetic trivially correct. Modern tooling - including
Python's own `zipfile`, which the tests validate against - reads it fine.

Layout per member:
    local header (30) + name + zip64 extra (20) + data + data descriptor (24)
then, once:
    central directory: (46 + name + zip64 extra (28)) per member
    zip64 EOCD (56) + zip64 EOCD locator (20) + EOCD (22)
"""
from __future__ import annotations

import hashlib
import logging
import struct
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

logger = logging.getLogger("fileheron.zip_writer")

_SIG_LOCAL = 0x04034B50
_SIG_DESCRIPTOR = 0x08074B50
_SIG_CENTRAL = 0x02014B50
_SIG_EOCD64 = 0x06064B50
_SIG_EOCD64_LOC = 0x07064B50
_SIG_EOCD = 0x06054B50

_FLAG_DATA_DESCRIPTOR = 0x0008
_FLAG_UTF8 = 0x0800
_METHOD_STORED = 0
_VERSION_ZIP64 = 45  # 4.5 - the version that understands zip64
_U32_MAX = 0xFFFFFFFF
_U16_MAX = 0xFFFF

# Fixed-size pieces, so the arithmetic below reads as the spec does.
_LOCAL_HEADER = 30
_LOCAL_ZIP64_EXTRA = 20  # 4 header + 8 uncompressed + 8 compressed
_DATA_DESCRIPTOR = 24  # 4 sig + 4 crc + 8 compressed + 8 uncompressed
_CENTRAL_HEADER = 46
_CENTRAL_ZIP64_EXTRA = 28  # 4 header + 8 uncompressed + 8 compressed + 8 offset
_EOCD64 = 56
_EOCD64_LOCATOR = 20
_EOCD = 22

_READ_CHUNK = 1024 * 256

# The MS-DOS epoch. The default when no caller timestamp is supplied: a constant
# beats `time.time()` because it makes the archive reproducible, and the mtime
# of a member inside a share archive carries no information anyone uses.
_DOS_EPOCH = 315532800.0  # 1980-01-01T00:00:00Z

# Bumped when a change alters the produced bytes. It is mixed into `signature()`
# so an in-flight resume against an archive built by the previous version misses
# its `If-Range` and restarts cleanly instead of splicing two layouts together.
LAYOUT_VERSION = 1


class CrcCache(Protocol):
    """Somewhere to remember a member's CRC-32 between requests."""

    def get(self, key: str) -> int | None: ...

    def put(self, key: str, crc: int) -> None: ...


def _dos_datetime(ts: float) -> tuple[int, int]:
    """(dos_time, dos_date). ZIP stores MS-DOS timestamps: 2-second resolution,
    epoch 1980.

    Rendered in **UTC**, not local time: the same share must produce the same
    bytes whatever `TZ` the container happens to have, or a resume splices two
    archives that disagree in every timestamp field."""
    t = time.gmtime(ts)
    year = max(t.tm_year, 1980)
    dos_date = ((year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    dos_time = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    return dos_time, dos_date


@dataclass
class _Entry:
    arcname: str
    size: int
    open_fn: Callable[[], BinaryIO]
    name_bytes: bytes
    # Stable identity of the member's BYTES (a file id), for the CRC cache. A
    # file's content never changes once it is `clean`, so the cached CRC cannot
    # go stale. None disables caching for this member.
    cache_key: str | None = None
    # Filled in while streaming, then used to build the central directory.
    crc: int = 0
    offset: int = 0


class ZipSizeMismatchError(RuntimeError):
    """A member produced fewer bytes than it declared.

    Raised mid-stream rather than papered over: the archive length was already
    promised in Content-Length, so silently substituting different content would
    hand the client a complete-looking download whose bytes are not the file
    they asked for. Failing the transfer is the honest outcome.
    """


class SizedZipStream:
    """Build a STORED zip64 archive whose byte length is known before streaming.

    Use `add_path` / `add_stream` to register members, `len(zs)` for the exact
    Content-Length, and iterate to stream. Sources are opened lazily during
    iteration - registering 500 members does not hold 500 file descriptors.

    `iter_from(offset)` streams the same archive starting at an arbitrary byte,
    which is what makes a ranged resume possible; see the module docstring.
    """

    def __init__(
        self, *, mtime: float | None = None, crc_cache: CrcCache | None = None
    ) -> None:
        self._entries: list[_Entry] = []
        self._mtime = _DOS_EPOCH if mtime is None else mtime
        self._crc_cache = crc_cache

    # -- registration -------------------------------------------------------

    def add_path(self, path: str | Path, arcname: str, *, cache_key: str | None = None) -> None:
        p = Path(path)
        size = p.stat().st_size
        self.add_stream(lambda: p.open("rb"), arcname, size, cache_key=cache_key)

    def add_stream(
        self,
        open_fn: Callable[[], BinaryIO],
        arcname: str,
        size: int,
        *,
        cache_key: str | None = None,
    ) -> None:
        """`open_fn` is called at streaming time and must return a fresh binary
        reader positioned at 0. `size` must be exact - it is what the declared
        Content-Length is built from."""
        if size < 0:
            raise ValueError("size must be non-negative")
        self._entries.append(
            _Entry(
                arcname=arcname,
                size=size,
                open_fn=open_fn,
                name_bytes=arcname.encode("utf-8"),
                cache_key=cache_key,
            )
        )

    # -- exact length + identity --------------------------------------------

    def __len__(self) -> int:
        total = 0
        for e in self._entries:
            n = len(e.name_bytes)
            total += _LOCAL_HEADER + n + _LOCAL_ZIP64_EXTRA
            total += e.size
            total += _DATA_DESCRIPTOR
            total += _CENTRAL_HEADER + n + _CENTRAL_ZIP64_EXTRA
        total += _EOCD64 + _EOCD64_LOCATOR + _EOCD
        return total

    def signature(self) -> str:
        """A hex digest over everything that determines the archive's bytes.

        Used as a strong ETag. A member added, removed, renamed, resized or
        reordered changes it, and so does a change to the layout itself - which
        is exactly what an `If-Range` needs to detect, since resuming across such
        a change would produce a corrupt file rather than a failed download."""
        h = hashlib.sha256()
        h.update(f"v{LAYOUT_VERSION}|{int(self._mtime)}|".encode())
        for e in self._entries:
            h.update(f"{len(e.name_bytes)}:".encode())
            h.update(e.name_bytes)
            h.update(f"|{e.size}|".encode())
        return h.hexdigest()[:32]

    # -- layout -------------------------------------------------------------

    def _positions(self) -> list[tuple[int, int, int]]:
        """(header_start, data_start, descriptor_start) per entry."""
        out: list[tuple[int, int, int]] = []
        pos = 0
        for e in self._entries:
            head = _LOCAL_HEADER + len(e.name_bytes) + _LOCAL_ZIP64_EXTRA
            out.append((pos, pos + head, pos + head + e.size))
            pos += head + e.size + _DATA_DESCRIPTOR
        return out

    def resume_cost(self, offset: int) -> int:
        """Bytes that would be read from storage but never sent, to start
        streaming at `offset`.

        Two sources. Every member ending before `offset` still needs its CRC for
        the central directory - cached ones are free, the rest must be re-read
        whole. And the member straddling `offset` is read from byte 0 for the
        same reason, so its prefix is read and discarded.

        A caller that finds the answer too expensive should serve the full
        archive with a 200 rather than pay it: a slow resume is worse than a
        restart, and a wrong CRC is worse than both."""
        if offset <= 0:
            return 0
        cost = 0
        for e, (_h, d_start, desc_start) in zip(
            self._entries, self._positions(), strict=True
        ):
            if offset >= desc_start + _DATA_DESCRIPTOR:
                if self._cached_crc(e) is None:
                    cost += e.size
                continue
            if offset > d_start and self._cached_crc(e) is None:
                # With the CRC cached, `iter_from` seeks past this prefix
                # instead of reading it (see the note there), so it costs
                # nothing. Counting it anyway is what declined every resume of
                # a large archive.
                cost += min(offset, desc_start) - d_start
            break
        return cost

    def _cached_crc(self, e: _Entry) -> int | None:
        if self._crc_cache is None or e.cache_key is None:
            return None
        try:
            return self._crc_cache.get(e.cache_key)
        except Exception:  # pragma: no cover - a cache must never break a download
            logger.warning("zip crc cache get failed for %r", e.cache_key, exc_info=True)
            return None

    def _remember_crc(self, e: _Entry, crc: int) -> None:
        if self._crc_cache is None or e.cache_key is None:
            return
        try:
            self._crc_cache.put(e.cache_key, crc)
        except Exception:  # pragma: no cover
            logger.warning("zip crc cache put failed for %r", e.cache_key, exc_info=True)

    def _crc_of(self, e: _Entry) -> int:
        """The member's CRC without emitting it: cache, else re-read it."""
        cached = self._cached_crc(e)
        if cached is not None:
            return cached
        crc = 0
        read = 0
        fh = e.open_fn()
        try:
            while read < e.size:
                chunk = fh.read(min(_READ_CHUNK, e.size - read))
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
                read += len(chunk)
        finally:
            try:
                fh.close()
            except Exception:  # pragma: no cover
                pass
        if read != e.size:
            raise ZipSizeMismatchError(
                f"{e.arcname}: declared {e.size} bytes, produced {read}"
            )
        crc &= 0xFFFFFFFF
        self._remember_crc(e, crc)
        return crc

    # -- block builders -----------------------------------------------------

    @property
    def _flags(self) -> int:
        return _FLAG_DATA_DESCRIPTOR | _FLAG_UTF8

    def _local_header(self, e: _Entry) -> bytes:
        dos_time, dos_date = _dos_datetime(self._mtime)
        header = struct.pack(
            "<IHHHHHIIIHH",
            _SIG_LOCAL,
            _VERSION_ZIP64,
            self._flags,
            _METHOD_STORED,
            dos_time,
            dos_date,
            0,  # crc - in the descriptor
            _U32_MAX,  # compressed size - see zip64 extra
            _U32_MAX,  # uncompressed size - see zip64 extra
            len(e.name_bytes),
            _LOCAL_ZIP64_EXTRA,
        )
        # Zip64 extended information. Sizes are unknown here (bit 3 is set),
        # so they are zero and the descriptor carries the truth.
        extra = struct.pack("<HHQQ", 0x0001, 16, 0, 0)
        return header + e.name_bytes + extra

    def _descriptor(self, e: _Entry, crc: int) -> bytes:
        return struct.pack("<IIQQ", _SIG_DESCRIPTOR, crc, e.size, e.size)

    def _central(self, e: _Entry, crc: int, offset: int) -> bytes:
        dos_time, dos_date = _dos_datetime(self._mtime)
        extra = struct.pack("<HHQQQ", 0x0001, 24, e.size, e.size, offset)
        central = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            _SIG_CENTRAL,
            _VERSION_ZIP64,  # version made by
            _VERSION_ZIP64,  # version needed
            self._flags,
            _METHOD_STORED,
            dos_time,
            dos_date,
            crc,
            _U32_MAX,  # compressed size -> zip64 extra
            _U32_MAX,  # uncompressed size -> zip64 extra
            len(e.name_bytes),
            len(extra),
            0,  # comment length
            0,  # disk number start
            0,  # internal attrs
            0o100644 << 16,  # external attrs: regular file, rw-r--r--
            _U32_MAX,  # local header offset -> zip64 extra
        )
        return central + e.name_bytes + extra

    def _end_records(self, cd_offset: int, cd_size: int) -> bytes:
        count = len(self._entries)
        return (
            struct.pack(
                "<IQHHIIQQQQ",
                _SIG_EOCD64,
                44,  # size of the remainder of this record
                _VERSION_ZIP64,
                _VERSION_ZIP64,
                0,  # this disk
                0,  # disk with central directory
                count,
                count,
                cd_size,
                cd_offset,
            )
            + struct.pack("<IIQI", _SIG_EOCD64_LOC, 0, cd_offset + cd_size, 1)
            + struct.pack(
                "<IHHHHIIH",
                _SIG_EOCD,
                _U16_MAX,
                _U16_MAX,
                _U16_MAX,
                _U16_MAX,
                _U32_MAX,
                _U32_MAX,
                0,
            )
        )

    # -- streaming ----------------------------------------------------------

    def __iter__(self) -> Iterator[bytes]:
        return self.iter_from(0)

    def iter_from(self, offset: int = 0, length: int | None = None) -> Iterator[bytes]:
        """Stream the archive from byte `offset`, at most `length` bytes.

        The full stream is `iter_from(0)` - one code path, so every existing
        archive test also exercises the seek arithmetic.

        Blocks are produced in archive order and clipped to the window: a block
        that ends before `offset` is skipped, the one straddling it is sliced,
        and the generator stops the moment `length` is satisfied. Member data is
        the only part read from storage; every other block is a few dozen bytes
        built in memory."""
        total = len(self)
        if offset < 0 or offset > total:
            raise ValueError(f"offset {offset} outside archive of {total} bytes")
        remaining = total - offset if length is None else min(length, total - offset)
        if remaining <= 0:
            return

        positions = self._positions()
        crcs: list[int] = []
        cd_offset = (positions[-1][2] + _DATA_DESCRIPTOR) if positions else 0

        for e, (h_start, d_start, desc_start) in zip(
            self._entries, positions, strict=True
        ):
            e.offset = h_start
            if offset >= desc_start + _DATA_DESCRIPTOR:
                # Wholly behind the window - but the central directory still
                # needs this member's CRC, so pay for it now (cache, else
                # re-read). `resume_cost()` is the caller's advance warning.
                crcs.append(self._crc_of(e))
                continue

            block = self._local_header(e)
            lo = max(0, offset - h_start)
            if lo < len(block):
                piece = block[lo : lo + remaining]
                remaining -= len(piece)
                yield piece
                if remaining <= 0:
                    return

            # The CRC covers the whole member, so the data has to be read from
            # byte 0 when the CRC is not already known - the skipped prefix is
            # read and discarded.
            #
            # When it IS known (cached from an earlier full transfer) and the
            # source is seekable, skip straight to the window. Without this a
            # resume of the archives this feature exists for was always
            # declined: a single 9 GB member costs its whole 8.1 GiB prefix,
            # over any sane re-read ceiling, so the client that asked for byte
            # 7.7e9 got a 200 and the whole 9 GB from zero - restarting forever
            # on a flaky link, and on the public route paying a budget unit each
            # time once the paid window had lapsed (audit #2).
            cached = self._cached_crc(e)
            data_skip = max(0, min(offset, desc_start) - d_start)
            crc = 0
            read = 0
            fh = e.open_fn()
            seeked = False
            if cached is not None and data_skip > 0:
                try:
                    if fh.seekable():
                        fh.seek(data_skip)
                        seeked = True
                        crc = cached
                        read = data_skip
                except Exception:  # pragma: no cover - fall back to a full read
                    logger.warning("zip resume: seek failed for %r", e.arcname, exc_info=True)
                    seeked = False
                    crc = 0
                    read = 0
            try:
                while read < e.size:
                    chunk = fh.read(min(_READ_CHUNK, e.size - read))
                    if not chunk:
                        break
                    if not seeked:
                        crc = zlib.crc32(chunk, crc)
                    start = d_start + read
                    read += len(chunk)
                    lo = max(0, offset - start)
                    if lo >= len(chunk):
                        continue
                    piece = chunk[lo : lo + remaining]
                    remaining -= len(piece)
                    yield piece
                    if remaining <= 0:
                        # The window closed mid-member. The CRC is incomplete
                        # and must NOT be cached or emitted - and it is not
                        # needed, because nothing after this point is sent.
                        return
            finally:
                try:
                    fh.close()
                except Exception:  # pragma: no cover - close-on-error is best effort
                    pass

            if read != e.size:
                # Cannot be recovered from: Content-Length is already committed.
                logger.error(
                    "zip member %r declared %d bytes but produced %d",
                    e.arcname, e.size, read,
                )
                raise ZipSizeMismatchError(
                    f"{e.arcname}: declared {e.size} bytes, produced {read}"
                )
            crc &= 0xFFFFFFFF
            if not seeked:
                self._remember_crc(e, crc)
            crcs.append(crc)

            block = self._descriptor(e, crc)
            lo = max(0, offset - desc_start)
            if lo < len(block):
                piece = block[lo : lo + remaining]
                remaining -= len(piece)
                yield piece
                if remaining <= 0:
                    return

        cd_size = sum(
            _CENTRAL_HEADER + len(e.name_bytes) + _CENTRAL_ZIP64_EXTRA
            for e in self._entries
        )

        pos = cd_offset
        for e, crc in zip(self._entries, crcs, strict=True):
            block = self._central(e, crc, e.offset)
            lo = max(0, offset - pos)
            pos += len(block)
            if lo >= len(block):
                continue
            piece = block[lo : lo + remaining]
            remaining -= len(piece)
            yield piece
            if remaining <= 0:
                return

        block = self._end_records(cd_offset, cd_size)
        lo = max(0, offset - pos)
        if lo < len(block):
            yield block[lo : lo + remaining]

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
the browser show true progress and lets a download be Range-resumed.

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

import logging
import struct
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

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


def _dos_datetime(ts: float) -> tuple[int, int]:
    """(dos_time, dos_date). ZIP stores MS-DOS timestamps: 2-second resolution,
    epoch 1980."""
    t = time.localtime(ts)
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
    """

    def __init__(self) -> None:
        self._entries: list[_Entry] = []
        self._mtime = time.time()

    # -- registration -------------------------------------------------------

    def add_path(self, path: str | Path, arcname: str) -> None:
        p = Path(path)
        size = p.stat().st_size
        self.add_stream(lambda: p.open("rb"), arcname, size)

    def add_stream(
        self, open_fn: Callable[[], BinaryIO], arcname: str, size: int
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
            )
        )

    # -- exact length -------------------------------------------------------

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

    # -- streaming ----------------------------------------------------------

    def __iter__(self) -> Iterator[bytes]:
        offset = 0
        dos_time, dos_date = _dos_datetime(self._mtime)
        flags = _FLAG_DATA_DESCRIPTOR | _FLAG_UTF8

        for e in self._entries:
            e.offset = offset

            header = struct.pack(
                "<IHHHHHIIIHH",
                _SIG_LOCAL,
                _VERSION_ZIP64,
                flags,
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
            yield header + e.name_bytes + extra
            offset += _LOCAL_HEADER + len(e.name_bytes) + _LOCAL_ZIP64_EXTRA

            crc = 0
            written = 0
            fh = e.open_fn()
            try:
                while written < e.size:
                    chunk = fh.read(min(_READ_CHUNK, e.size - written))
                    if not chunk:
                        break
                    crc = zlib.crc32(chunk, crc)
                    written += len(chunk)
                    yield chunk
            finally:
                try:
                    fh.close()
                except Exception:  # pragma: no cover - close-on-error is best effort
                    pass

            if written != e.size:
                # Cannot be recovered from: Content-Length is already committed.
                logger.error(
                    "zip member %r declared %d bytes but produced %d",
                    e.arcname, e.size, written,
                )
                raise ZipSizeMismatchError(
                    f"{e.arcname}: declared {e.size} bytes, produced {written}"
                )

            e.crc = crc & 0xFFFFFFFF
            yield struct.pack(
                "<IIQQ", _SIG_DESCRIPTOR, e.crc, e.size, e.size
            )
            offset += e.size + _DATA_DESCRIPTOR

        # -- central directory ---------------------------------------------
        cd_offset = offset
        cd_size = 0
        for e in self._entries:
            extra = struct.pack(
                "<HHQQQ", 0x0001, 24, e.size, e.size, e.offset
            )
            central = struct.pack(
                "<IHHHHHHIIIHHHHHII",
                _SIG_CENTRAL,
                _VERSION_ZIP64,  # version made by
                _VERSION_ZIP64,  # version needed
                flags,
                _METHOD_STORED,
                dos_time,
                dos_date,
                e.crc,
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
            yield central + e.name_bytes + extra
            cd_size += _CENTRAL_HEADER + len(e.name_bytes) + len(extra)

        count = len(self._entries)
        yield struct.pack(
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
        yield struct.pack(
            "<IIQI", _SIG_EOCD64_LOC, 0, cd_offset + cd_size, 1
        )
        yield struct.pack(
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

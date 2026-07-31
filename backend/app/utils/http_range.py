"""HTTP `Range` helpers: detect a continuation, and parse a single range.

A parallel/segmented download (or a resume) fetches byte ranges that do not
start at 0. The byte-0 request (or a non-ranged full request) already counted
the download against the share / public-link budget and wrote the DownloadLog,
so these continuation ranges must NOT re-decrement the counter or re-log -
otherwise one parallel download would count as N and could trip the limit.

**`is_partial_continuation` is a claim, not proof.** It answers exactly one
question - does the range start above byte 0 - which any client can assert. It
used to be the whole basis for three exemptions, and `Range: bytes=1-` on a
fresh connection therefore bought unlimited free downloads (audit 2026-07-30).
Every caller now pairs it with independent evidence that a download of this
thing really is in flight: `transfer_activity.was_download_recent()` on the
anonymous paths, a recent `download_log` row on the authenticated ones. Do not
reintroduce a bare `if is_partial_continuation(request)` around a counter, a log
write or a state check.
"""
from __future__ import annotations

from typing import NamedTuple

from starlette.requests import Request


def is_partial_continuation(request: Request) -> bool:
    """True for a ranged GET whose first byte offset is > 0."""
    rng = request.headers.get("range")
    if not rng:
        return False
    spec = rng.strip().lower()
    if not spec.startswith("bytes="):
        return False
    first = spec[len("bytes="):].split(",", 1)[0].strip()
    start = first.split("-", 1)[0].strip()
    return start.isdigit() and int(start) > 0


class ByteRange(NamedTuple):
    start: int
    end: int  # inclusive, RFC 9110 style

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class UnsatisfiableRangeError(Exception):
    """The header was well-formed but asks for bytes outside the resource."""


def parse_single_range(header: str | None, total: int) -> ByteRange | None:
    """Resolve a `Range` header against a resource of `total` bytes.

    Returns None when there is nothing to honour - no header, a unit other than
    bytes, a syntactically broken spec, or **more than one range**. Multi-range
    responses need a multipart/byteranges body; nothing in this product asks for
    one, and a caller that returns the full 200 instead is always correct
    (RFC 9110 6.5.4: an unsatisfiable-to-the-server Range may simply be ignored).

    Raises `UnsatisfiableRangeError` when the spec is valid but starts past the end,
    which the caller must answer with 416 rather than 200 - returning the whole
    resource to a client that asked for byte 10 of a 5-byte file would corrupt a
    resume.
    """
    if not header:
        return None
    spec = header.strip()
    if "=" not in spec:
        return None
    unit, _, rest = spec.partition("=")
    if unit.strip().lower() != "bytes":
        return None
    parts = rest.split(",")
    if len(parts) != 1:
        return None
    first, _, last = parts[0].strip().partition("-")
    first, last = first.strip(), last.strip()

    if not first:
        # Suffix range: the LAST n bytes. `bytes=-0` is unsatisfiable.
        if not last.isdigit():
            return None
        n = int(last)
        if n == 0:
            raise UnsatisfiableRangeError
        start = max(0, total - n)
        return ByteRange(start, total - 1) if total else None
    if not first.isdigit():
        return None
    start = int(first)
    if start >= total:
        raise UnsatisfiableRangeError
    if not last:
        return ByteRange(start, total - 1)
    if not last.isdigit():
        return None
    end = min(int(last), total - 1)
    if end < start:
        raise UnsatisfiableRangeError
    return ByteRange(start, end)

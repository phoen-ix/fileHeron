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
Every caller now pairs it with independent evidence. Which evidence depends on
what the exemption protects, and they are not interchangeable:

- a **budget** asks "has this principal already PAID for this thing" -
  `transfer_activity.was_download_paid(...)`, keyed on the payer, written only
  where the counter moves. Using the serving mark here let one principal's
  activity buy another's free downloads.
- an **audit trail** asks "have I already recorded this" - same mark, opposite
  bias: when in doubt, write the row. A duplicate entry is noise; a missing one
  defeats the control.
- the **maintenance drain** asks "did this instance serve bytes for this
  recently" - `was_download_recent()`, which is serving-based and correct for
  that and for nothing else.
- the **authenticated** paths use a recent `download_log` row: durable and
  user-scoped by construction, so an overnight resume still works.

Do not reintroduce a bare `if is_partial_continuation(request)` around a
counter, a log write, or any decision that is not re-checked downstream. (The
one bare use that remains is deliberate and marked as such: the
`assert_link_usable` pre-check in `routers/public.py` lets an exhausted link
proceed far enough to compute the archive identity its real corroboration is
keyed on, and the authoritative decision is taken a few lines later.)
"""
from __future__ import annotations

from typing import NamedTuple

from starlette.requests import Request


def _is_number(text: str) -> bool:
    """ASCII digits only.

    `str.isdigit()` is True for characters like the latin-1 superscript two, and
    `int()` then raises ValueError - straight out of the route, as an unhandled
    500 with an error_log row and a `notify_admin_error` enqueue per request. So
    `Range: bytes=\xb2-` let an unauthenticated caller holding any public-link
    token manufacture 5xx alerts at will and flood the error log, on every
    download, preview and ZIP route (audit #2).
    """
    return text.isascii() and text.isdigit()


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
    # `_is_number`, not `.isdigit()`. This function runs BEFORE
    # `parse_single_range` on the download routes, so hardening only that one
    # left the 500 reachable: `.isdigit()` is True for a latin-1 superscript and
    # `int()` then raises straight out of the route (audit #2 cross-check).
    return _is_number(start) and int(start) > 0


# A range this small is a client asking "how big is this, and do you do ranges?",
# not a client taking the file. One byte, deliberately: the desktop client's
# `_probe` sends exactly `bytes=1-1`, and every extra byte of slack multiplies
# how cheaply the exemption could be used to extract content without paying.
PROBE_MAX_BYTES = 1
# The one offset a probe may read. Pinned, not merely bounded in length: a probe
# that could be aimed anywhere is a free byte-at-a-time read of the whole
# resource - which on the ANONYMOUS public-link route reconstructed a file in
# full while the download budget never moved and nothing was logged or notified
# (audit #2). Every shipped client sends exactly `bytes=1-1`
# (client/src/fileheron_client/api/download_resumable.py::_probe).
PROBE_OFFSET = 1


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
        if not _is_number(last):
            return None
        n = int(last)
        if n == 0:
            raise UnsatisfiableRangeError
        start = max(0, total - n)
        return ByteRange(start, total - 1) if total else None
    if not _is_number(first):
        return None
    start = int(first)
    if start >= total:
        raise UnsatisfiableRangeError
    if not last:
        return ByteRange(start, total - 1)
    if not _is_number(last):
        return None
    end = min(int(last), total - 1)
    if end < start:
        raise UnsatisfiableRangeError
    return ByteRange(start, end)


def is_metadata_probe(header: str | None, total: int) -> bool:
    """True for a ranged GET that asks for at most `PROBE_MAX_BYTES` of a
    larger resource - a size/range-support probe, not a download.

    The desktop client opens EVERY download with `Range: bytes=1-1` to learn the
    total size and whether the server honours ranges, then segments the real
    transfer. Before v2.6.0 that request rode the "any range above byte 0 is a
    continuation" exemption; v2.6.0 correctly removed that exemption and
    incorrectly took the probe with it, so a first download was charged twice
    and a `download_limit=1` share became undownloadable from the client while
    still working in a browser.

    Charging on how much is being TAKEN rather than on where it starts is what
    separates the two cases. `bytes=1-` asks for the whole file minus one byte
    and is a download; `bytes=1-1` asks for one byte.

    The exemption is pinned to ONE offset, `PROBE_OFFSET`. Bounding only the
    LENGTH left `bytes=0-0, 1-1, 2-2, ...` free, which reconstructed the whole
    file - and this route is reached anonymously through a public link, with no
    authentication and no rate limit, so the justification that once stood here
    ("one authenticated, rate-limited round trip per byte") did not hold where
    it mattered most. With the offset pinned, the exemption yields exactly one
    byte the caller learns nothing from (audit #2).

    `total <= PROBE_OFFSET` returns False: for a resource that small the
    "probe" would be the whole thing.
    """
    if total <= PROBE_OFFSET:
        return False
    try:
        rng = parse_single_range(header, total)
    except UnsatisfiableRangeError:
        return False
    return (
        rng is not None
        and rng.start == PROBE_OFFSET
        and rng.length <= PROBE_MAX_BYTES
    )

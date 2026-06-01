"""Detect a 'partial continuation' download request.

A parallel/segmented download (or a resume) fetches byte ranges that do not
start at 0. The byte-0 request (or a non-ranged full request) already counted
the download against the share / public-link budget and wrote the DownloadLog,
so these continuation ranges must NOT re-decrement the counter or re-log —
otherwise one parallel download would count as N and could trip the limit.

Trusted-admin model: a client could in principle send only ``bytes=1-`` to dodge
the counter entirely; that's acceptable here (the file is only useful in full,
and the deployment operates in a trusted-admin context).
"""
from __future__ import annotations

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

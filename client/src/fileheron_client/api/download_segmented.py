"""Segment helpers for the resumable downloader.

``_split`` computes the parallel byte-range plan and ``_fetch_segment`` fetches
one range into its offset in a pre-allocated ``.part`` file. The orchestration
(and the single-stream fallback) lives in ``download_resumable``.

Requires backend >= v1.5.2, which counts a download once (the byte-0 segment)
so the continuation ranges don't each consume the share's download budget.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .client import ApiClient
from .files import DownloadCancelled, DownloadPaused

CHUNK = 1024 * 1024  # 1 MiB - fewer iterations → less per-chunk overhead/GIL churn
SEGMENT_THRESHOLD = 16 * 1024 * 1024  # below this, single stream isn't worth it
SEGMENT_SIZE = 16 * 1024 * 1024       # bytes per segment (bounds segment count)
MAX_CONNECTIONS = 8
MAX_RETRIES = 3
BACKOFF_SECONDS = (1, 4, 12)


def _split(total: int, seg: int) -> list[tuple[int, int]]:
    """Inclusive (start, end) byte ranges covering [0, total)."""
    out: list[tuple[int, int]] = []
    start = 0
    while start < total:
        end = min(start + seg, total) - 1
        out.append((start, end))
        start = end + 1
    return out


def _parse_content_range(value: Optional[str]) -> Optional[tuple]:
    """`bytes 100-199/1234` -> (100, 199). None when absent or unparseable -
    the caller treats that as "cannot verify" rather than "wrong", since a
    well-behaved 206 always carries it and a malformed one is already caught by
    the byte-count check."""
    if not value:
        return None
    try:
        spec = value.strip().split(" ", 1)[1].split("/", 1)[0]
        lo, hi = spec.split("-", 1)
        return (int(lo), int(hi))
    except (IndexError, ValueError):
        return None


def _fetch_segment(
    api: ApiClient,
    url: str,
    headers: dict,
    part: Path,
    start: int,
    end: int,
    bump: Callable[[int], None],
    cancel: Optional[threading.Event] = None,
    pause: Optional[threading.Event] = None,
) -> None:
    # Read the Authorization header FRESH each attempt rather than trusting the
    # snapshot the caller passed in: a segmented transfer of a large file
    # routinely outlives the 15-minute access token, and the old behaviour was
    # to keep presenting the dead one until the retries ran out.
    rng = {**headers, **api.auth_header(), "Range": f"bytes={start}-{end}"}
    for attempt in range(MAX_RETRIES):
        written = 0
        try:
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled
            if pause is not None and pause.is_set():
                raise DownloadPaused
            with api._http.stream(
                "GET", url, headers=rng, follow_redirects=True
            ) as resp:
                # 206 only. A 200 means the peer ignored `Range` and is sending
                # the WHOLE file - and every worker would then write a full copy
                # at its own offset, producing a corrupt (and oversized) result
                # that still reported success. `_probe` normally keeps us off
                # this path, but an intermediary that honours a 1-byte range and
                # not a 16 MiB one gets here, and silent corruption is the worst
                # possible failure mode for a file-transfer tool.
                if resp.status_code == 401:
                    # The access token expired mid-transfer. Refresh once and
                    # retry this segment; a dead session raises
                    # SessionExpiredError, which the UI turns into a re-login
                    # prompt instead of a generic transfer failure.
                    resp.read()
                    rng.update(api.refresh_bearer_header())
                    continue
                if resp.status_code != 206:
                    resp.read()
                    raise OSError(
                        f"segment {start}-{end}: expected 206, got HTTP {resp.status_code}"
                    )
                # Trust the header, then verify it: a 206 whose Content-Range
                # does not describe the span we asked for would splice the wrong
                # bytes into the middle of the file.
                got = _parse_content_range(resp.headers.get("Content-Range"))
                if got is not None and got != (start, end):
                    resp.read()
                    raise OSError(
                        f"segment {start}-{end}: server answered range {got[0]}-{got[1]}"
                    )
                with open(part, "r+b") as f:
                    f.seek(start)
                    for chunk in resp.iter_bytes(CHUNK):
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled
                        if pause is not None and pause.is_set():
                            raise DownloadPaused
                        f.write(chunk)
                        written += len(chunk)
                        bump(len(chunk))
            expected = end - start + 1
            if written != expected:
                # A short segment leaves a hole of stale bytes in the middle of
                # the .part file. Nothing downstream would notice: the size is
                # right (it was pre-allocated) and there is no digest check.
                # Raise so the retry loop re-fetches this span.
                raise OSError(
                    f"segment {start}-{end}: got {written} bytes, expected {expected}"
                )
            return
        except (DownloadCancelled, DownloadPaused):
            raise  # never retry a cancel/pause
        except Exception:
            if written:
                bump(-written)  # this attempt's bytes will be re-fetched
            if attempt + 1 >= MAX_RETRIES:
                raise
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

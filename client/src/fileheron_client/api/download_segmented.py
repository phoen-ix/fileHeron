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
    rng = {**headers, "Range": f"bytes={start}-{end}"}
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
                if resp.status_code not in (206, 200):
                    resp.read()
                    raise OSError(f"segment {start}-{end}: HTTP {resp.status_code}")
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
            return
        except (DownloadCancelled, DownloadPaused):
            raise  # never retry a cancel/pause
        except Exception:
            if written:
                bump(-written)  # this attempt's bytes will be re-fetched
            if attempt + 1 >= MAX_RETRIES:
                raise
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

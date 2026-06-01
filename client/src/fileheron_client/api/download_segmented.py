"""Multi-connection (segmented) downloader.

A single TLS/TCP stream over a high-latency/lossy path is window-limited and
rarely saturates a fast link. This splits a large download into N byte-range
requests fetched in parallel, each written to its offset in a pre-allocated
``.part`` file, then atomically renamed into place — N streams saturate the
link.

Requires backend >= v1.5.2, which counts a download once (the byte-0 segment)
so the continuation ranges don't each consume the share's download budget.

Falls back to the single-stream ``download_file`` when the server doesn't
advertise ranges, the file is below the threshold, segmenting is disabled
(connections <= 1), or any segment hard-fails after retries.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from .client import ApiClient
from .files import download_file

logger = logging.getLogger("fileheron_client.api.download_segmented")

CHUNK = 64 * 1024
SEGMENT_THRESHOLD = 16 * 1024 * 1024  # below this, single stream isn't worth it
SEGMENT_SIZE = 16 * 1024 * 1024       # bytes per segment (bounds segment count)
MAX_CONNECTIONS = 8
MAX_RETRIES = 3
BACKOFF_SECONDS = (1, 4, 12)


def download_file_segmented(
    api: ApiClient,
    file_id: str,
    *,
    dest: Path,
    connections: int = 4,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    headers = {"Authorization": f"Bearer {api.bearer}"} if api.bearer else {}
    url = f"/api/files/{file_id}/download"

    n = max(1, min(int(connections), MAX_CONNECTIONS))
    total = _probe_total(api, url, headers) if n > 1 else None

    if total is None or total < SEGMENT_THRESHOLD or n == 1:
        # Unsupported / too small / disabled → single stream.
        return download_file(api, file_id, dest=dest, on_progress=on_progress)

    segments = _split(total, SEGMENT_SIZE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    # Pre-allocate so each worker can seek+write its slice independently.
    with open(part, "wb") as f:
        f.truncate(total)

    lock = threading.Lock()
    done_total = 0

    def _bump(delta: int) -> None:
        nonlocal done_total
        if on_progress is None:
            return
        with lock:
            done_total += delta
            on_progress(max(0, done_total), total)

    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = [
                pool.submit(_fetch_segment, api, url, headers, part, s, e, _bump)
                for (s, e) in segments
            ]
            for fut in as_completed(futs):
                fut.result()  # re-raise the first segment failure
    except Exception as exc:
        logger.warning(
            "segmented download failed for %s (%s); falling back to single stream",
            file_id, exc,
        )
        try:
            part.unlink()
        except OSError:
            pass
        return download_file(api, file_id, dest=dest, on_progress=on_progress)

    os.replace(part, dest)  # atomic
    return dest


def _probe_total(api: ApiClient, url: str, headers: dict) -> Optional[int]:
    """One tiny ranged GET: returns the total size iff the server honours
    ranges (HTTP 206 with a parseable Content-Range), else None."""
    try:
        with api._http.stream(
            "GET", url, headers={**headers, "Range": "bytes=0-0"}
        ) as resp:
            cr = resp.headers.get("Content-Range", "")
            resp.read()  # drain the 1 byte so the connection is reusable
            if resp.status_code != 206 or "/" not in cr:
                return None
            tail = cr.rsplit("/", 1)[1].strip()
            return int(tail) if tail.isdigit() else None
    except Exception:
        return None


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
) -> None:
    rng = {**headers, "Range": f"bytes={start}-{end}"}
    for attempt in range(MAX_RETRIES):
        written = 0
        try:
            with api._http.stream("GET", url, headers=rng) as resp:
                if resp.status_code not in (206, 200):
                    resp.read()
                    raise OSError(f"segment {start}-{end}: HTTP {resp.status_code}")
                with open(part, "r+b") as f:
                    f.seek(start)
                    for chunk in resp.iter_bytes(CHUNK):
                        f.write(chunk)
                        written += len(chunk)
                        bump(len(chunk))
            return
        except Exception:
            if written:
                bump(-written)  # this attempt's bytes will be re-fetched
            if attempt + 1 >= MAX_RETRIES:
                raise
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

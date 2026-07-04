"""File download endpoints. ``download_file`` streams the body to a
local path so multi-GB files don't blow out RAM."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from .client import ApiClient, _envelope_from_response

logger = logging.getLogger("fileheron_client.files")

CHUNK = 1024 * 1024  # 1 MiB - fewer progress ticks / less per-chunk overhead


class DownloadCancelled(Exception):
    """Raised when a download is aborted via its cancel Event. Distinct from
    transport errors so the segmented downloader neither retries it nor falls
    back to a single stream - it just unwinds + cleans up."""


class DownloadPaused(Exception):
    """Raised when a download is paused via its pause Event. Unlike
    ``DownloadCancelled`` the partial ``.part`` file + checkpoint are KEPT so
    the transfer can be resumed later. Never retried."""


def download_file(
    api: ApiClient,
    file_id: str,
    *,
    dest: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """Stream the file body into ``dest``. Returns the dest path on
    success. Raises ``ApiError`` on any non-200 (including the
    410/425 in-progress / quarantined / deleted states), or
    ``DownloadCancelled`` if ``cancel`` is set mid-transfer (partial
    ``dest`` is removed)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {api.bearer}"} if api.bearer else {}
    try:
        # follow_redirects: an S3/object-storage backend answers with a 307 to a
        # presigned URL. httpx follows it and drops the bearer cross-origin (the
        # presigned query carries its own auth), so downloads work on S3 too.
        with api._http.stream(
            "GET", f"/api/files/{file_id}/download", headers=headers,
            follow_redirects=True,
        ) as resp:
            if resp.status_code != 200:
                # Drain so the server can release the connection cleanly.
                resp.read()
                raise _envelope_from_response(resp)
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with dest.open("wb") as out:
                for chunk in resp.iter_bytes(CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total or done)
    except DownloadCancelled:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest

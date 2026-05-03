"""File download endpoints. ``download_file`` streams the body to a
local path so multi-GB files don't blow out RAM."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from .client import ApiClient, _envelope_from_response

logger = logging.getLogger("fileheron_client.files")

CHUNK = 64 * 1024  # 64 KiB — same order tusd uses


def download_file(
    api: ApiClient,
    file_id: str,
    *,
    dest: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Stream the file body into ``dest``. Returns the dest path on
    success. Raises ``ApiError`` on any non-200 (including the
    410/425 in-progress / quarantined / deleted states)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {api.bearer}"} if api.bearer else {}
    with api._http.stream(
        "GET", f"/api/files/{file_id}/download", headers=headers
    ) as resp:
        if resp.status_code != 200:
            # Drain so the server can release the connection cleanly.
            resp.read()
            raise _envelope_from_response(resp)
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as out:
            for chunk in resp.iter_bytes(CHUNK):
                out.write(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(done, total or done)
    return dest


def get_download_url(api: ApiClient, file_id: str) -> str:
    """Mint a short-lived signed download URL. Useful when you want a
    URL the user can paste into their browser without exposing the
    bearer token."""
    out = api.request_or_raise(
        "GET", f"/api/files/{file_id}/download-url"
    )
    return out["url"]

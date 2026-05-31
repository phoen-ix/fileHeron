"""Tiny TUS 1.0.0 client.

Why hand-rolled instead of ``tuspy``: tuspy doesn't accommodate the
pre-built ``Upload-Metadata`` header coming from
``/api/uploads/init`` cleanly, the protocol is small, and we want
explicit control over chunk size + retry/resume.

Wire flow:
1. POST to the TUS endpoint URL with ``Upload-Length`` + the
   pre-signed ``Upload-Metadata`` header → server returns 201 with
   the new upload's URL in the ``Location`` header.
2. PATCH chunks (``Content-Type: application/offset+octet-stream``,
   ``Upload-Offset: <bytes-sent-so-far>``).
3. On any chunk failure: HEAD the upload URL → read
   ``Upload-Offset`` → resume PATCH from there. Retries with
   exponential backoff.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger("fileheron_client.tus")

TUS_VERSION = "1.0.0"
DEFAULT_CHUNK = 4 * 1024 * 1024  # 4 MiB
MAX_RETRIES = 3  # total attempts per chunk (initial try + 2 retries), not "3 retries"
BACKOFF_SECONDS = (1, 4, 12)


class TusError(RuntimeError):
    """Wraps the underlying HTTP error so the UI can render it."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _absolute(server_url: str, location_or_path: str) -> str:
    if location_or_path.startswith(("http://", "https://")):
        return location_or_path
    return urljoin(server_url.rstrip("/") + "/", location_or_path.lstrip("/"))


def upload_tus(
    *,
    server_url: str,
    tus_endpoint: str,
    upload_metadata_header: str,
    file_path: Path,
    bearer: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK,
    on_progress: Optional[Callable[[int, int], None]] = None,
    timeout: float = 120.0,
) -> str:
    """Upload ``file_path`` via the TUS resumable protocol. Returns the
    final upload URL (for debugging / log correlation)."""
    # Snapshot the size once and declare it as Upload-Length. If the user
    # edits the file mid-upload (finding C9 — TOCTOU), the streamed bytes
    # will diverge from this length and the server rejects the final chunk;
    # we don't try to recover (re-statting mid-stream would just race
    # again). The caller should treat the file as immutable for the upload.
    size = file_path.stat().st_size
    base_headers: dict[str, str] = {
        "Tus-Resumable": TUS_VERSION,
        "Upload-Metadata": upload_metadata_header,
    }
    if bearer:
        base_headers["Authorization"] = f"Bearer {bearer}"

    create_url = _absolute(server_url, tus_endpoint)

    with httpx.Client(timeout=timeout) as cli:
        # 1. Create.
        create_headers = {**base_headers, "Upload-Length": str(size)}
        resp = cli.post(create_url, headers=create_headers)
        if resp.status_code not in (201, 200):
            raise TusError(
                f"TUS create failed: HTTP {resp.status_code} {resp.text[:200]}",
                status_code=resp.status_code,
            )
        location = resp.headers.get("Location")
        if not location:
            raise TusError("TUS create response missing Location header")
        # _absolute() already handles all three forms (full URL, root-relative
        # /path, bare relative path). The old `if location.startswith("/")`
        # guard left a bare-relative Location (e.g. "uploads/abc") un-resolved,
        # which httpx then rejected mid-upload (finding C2). Resolve always —
        # matches the create_url handling at the top of this function.
        upload_url = _absolute(server_url, location)

        # 2. Stream chunks (with resume on failure).
        offset = 0
        with file_path.open("rb") as fh:
            while offset < size:
                fh.seek(offset)
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                patch_headers = {
                    **base_headers,
                    "Upload-Offset": str(offset),
                    "Content-Type": "application/offset+octet-stream",
                }
                offset = _send_chunk_with_retry(
                    cli=cli,
                    upload_url=upload_url,
                    chunk=chunk,
                    headers=patch_headers,
                    base_headers=base_headers,
                    expected_offset=offset,
                )
                if on_progress is not None:
                    on_progress(offset, size)

    return upload_url


def _send_chunk_with_retry(
    *,
    cli: httpx.Client,
    upload_url: str,
    chunk: bytes,
    headers: dict[str, str],
    base_headers: dict[str, str],
    expected_offset: int,
) -> int:
    """PATCH one chunk with up to MAX_RETRIES + resume. Returns the
    new offset on success."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = cli.patch(upload_url, content=chunk, headers=headers)
        except httpx.HTTPError as e:
            logger.warning(
                "tus PATCH transport error (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                e,
            )
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
            continue

        if resp.status_code == 204:
            return int(resp.headers.get("Upload-Offset", expected_offset + len(chunk)))

        if resp.status_code in (409, 410, 423, 460):
            # Offset mismatch / locked — re-sync via HEAD and retry from
            # the server's reported position.
            head = cli.head(upload_url, headers=base_headers)
            if head.status_code != 200:
                raise TusError(
                    f"TUS resync HEAD failed: HTTP {head.status_code}",
                    status_code=head.status_code,
                )
            actual_offset = int(head.headers.get("Upload-Offset", "0"))
            if actual_offset != expected_offset:
                raise TusError(
                    f"TUS offset drift: server says {actual_offset}, "
                    f"client expected {expected_offset}. Aborting upload."
                )
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
            continue

        # Server-side hard failure — don't retry, surface the body.
        raise TusError(
            f"TUS PATCH failed: HTTP {resp.status_code} {resp.text[:200]}",
            status_code=resp.status_code,
        )

    raise TusError(
        f"TUS PATCH failed after {MAX_RETRIES} retries at offset {expected_offset}"
    )

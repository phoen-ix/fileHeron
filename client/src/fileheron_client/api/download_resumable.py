"""Resumable + pausable download orchestrator.

Wraps the existing single-stream (``files.download_file``) and parallel-range
(``download_segmented``) machinery with a checkpoint so an interrupted or
*paused* download can continue from where it stopped instead of restarting.

- **Pause** (``pause`` Event set): stop, keep ``<dest>.part`` + the
  ``<dest>.fhdownload`` checkpoint, raise ``DownloadPaused``.
- **Cancel** (``cancel`` Event set): stop, discard the partial + checkpoint,
  raise ``DownloadCancelled``.
- **Resume**: re-probe the file, validate it's unchanged (ETag), then fetch
  only the missing bytes (single-stream: ``Range: bytes=<offset>-`` with
  ``If-Range``; segmented: re-fetch the not-yet-complete segments).

The byte transfer reuses ``download_segmented._fetch_segment`` / ``_split`` and
the server's HTTP Range support (Starlette ``FileResponse``). The pre-existing
``download_file`` / ``download_file_segmented`` entry points are unchanged; the
UI calls ``download_file_resumable``.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from . import download_checkpoint as ckpt
from .client import ApiClient, ApiError, _envelope_from_response
from .download_segmented import (
    CHUNK,
    MAX_CONNECTIONS,
    SEGMENT_SIZE,
    SEGMENT_THRESHOLD,
    _fetch_segment,
    _split,
)
from .files import DownloadCancelled, DownloadPaused

logger = logging.getLogger("fileheron_client.api.download_resumable")

ProgressCb = Optional[Callable[[int, int], None]]


def _raise_if_stopped(
    cancel: Optional[threading.Event], pause: Optional[threading.Event]
) -> None:
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled
    if pause is not None and pause.is_set():
        raise DownloadPaused


def _probe(
    api: ApiClient, url: str, headers: dict
) -> Optional[tuple[int, Optional[str]]]:
    """One tiny ranged GET. Returns (total, etag) iff the server honours
    ranges (HTTP 206 with a parseable Content-Range), else None."""
    try:
        with api._http.stream(
            "GET", url, headers={**headers, "Range": "bytes=0-0"}
        ) as resp:
            cr = resp.headers.get("Content-Range", "")
            etag = resp.headers.get("ETag")
            resp.read()  # drain so the connection is reusable
            if resp.status_code != 206 or "/" not in cr:
                return None
            tail = cr.rsplit("/", 1)[1].strip()
            return (int(tail), etag) if tail.isdigit() else None
    except Exception:
        return None


def download_file_resumable(
    api: ApiClient,
    file_id: str,
    *,
    dest: Path,
    connections: int = 4,
    on_progress: ProgressCb = None,
    cancel: Optional[threading.Event] = None,
    pause: Optional[threading.Event] = None,
) -> Path:
    """Download ``file_id`` into ``dest``, resuming from a prior partial if a
    valid checkpoint sits next to ``dest``. Honours ``cancel`` (discard) and
    ``pause`` (keep + resumable). Returns ``dest`` on success."""
    _raise_if_stopped(cancel, pause)
    headers = {"Authorization": f"Bearer {api.bearer}"} if api.bearer else {}
    url = f"/api/files/{file_id}/download"
    dest.parent.mkdir(parents=True, exist_ok=True)

    n = max(1, min(int(connections), MAX_CONNECTIONS))
    probe = _probe(api, url, headers)
    if probe is not None:
        total, etag = probe
        ranges_ok = True
    else:
        total, etag, ranges_ok = None, None, False

    resume: Optional[ckpt.Checkpoint] = None
    cp = ckpt.read(dest)
    part = ckpt.part_path(dest)
    if cp is not None and part.exists():
        if not ranges_ok:
            # We have a partial but couldn't re-probe - refuse to restart from
            # scratch (would throw away the user's bytes) or guess; surface a
            # transient error so a retry re-probes and resumes.
            raise ApiError(
                status_code=503,
                code="RESUME_PROBE_FAILED",
                message="Couldn't reach the server to resume this download; try again.",
            )
        if (
            cp.file_id == file_id
            and cp.total == total
            and (etag is None or cp.etag is None or cp.etag == etag)
        ):
            resume = cp
        else:
            ckpt.discard(dest)  # the remote file changed → can't reuse
    elif cp is not None or part.exists():
        ckpt.discard(dest)  # one sidecar without the other → stale

    if resume is not None:
        use_segmented = resume.mode == "segmented"
    else:
        use_segmented = (
            ranges_ok and total is not None and total >= SEGMENT_THRESHOLD and n > 1
        )

    if use_segmented:
        return _run_segmented(
            api, url, headers, file_id=file_id, dest=dest, total=total or 0,
            etag=etag, n=n, resume=resume, on_progress=on_progress,
            cancel=cancel, pause=pause,
        )
    return _run_single(
        api, url, headers, file_id=file_id, dest=dest, total=total, etag=etag,
        ranges_ok=ranges_ok, resume=resume, on_progress=on_progress,
        cancel=cancel, pause=pause,
    )


def _run_single(
    api: ApiClient, url: str, headers: dict, *, file_id: str, dest: Path,
    total: Optional[int], etag: Optional[str], ranges_ok: bool,
    resume: Optional[ckpt.Checkpoint], on_progress: ProgressCb,
    cancel: Optional[threading.Event], pause: Optional[threading.Event],
) -> Path:
    part = ckpt.part_path(dest)
    offset = 0
    if resume is not None and part.exists():
        offset = part.stat().st_size
        if total and offset >= total:  # already fully on disk
            os.replace(part, dest)
            ckpt.clear(dest)
            if on_progress is not None:
                on_progress(total, total)
            return dest
    else:
        ckpt.discard(dest)  # fresh
        offset = 0

    req_headers = dict(headers)
    if ranges_ok and offset > 0:
        req_headers["Range"] = f"bytes={offset}-"
        if etag:
            req_headers["If-Range"] = etag

    ckpt.write(
        dest,
        ckpt.Checkpoint(file_id=file_id, total=total or 0, etag=etag, mode="single"),
    )
    _raise_if_stopped(cancel, pause)
    try:
        with api._http.stream("GET", url, headers=req_headers) as resp:
            if resp.status_code == 206:
                write_mode = "r+b"
                start_at = offset
                cr = resp.headers.get("Content-Range", "")
                if "/" in cr:
                    tail = cr.rsplit("/", 1)[1].strip()
                    if tail.isdigit():
                        total = int(tail)
            elif resp.status_code == 200:
                # Full body: a fresh download, or an If-Range mismatch / no
                # range support → start over from byte 0.
                write_mode = "wb"
                start_at = 0
                cl = resp.headers.get("Content-Length")
                total = int(cl) if cl and cl.isdigit() else (total or 0)
            else:
                resp.read()
                raise _envelope_from_response(resp)

            done = start_at
            with open(part, write_mode) as out:
                if write_mode == "r+b":
                    out.seek(start_at)
                for chunk in resp.iter_bytes(CHUNK):
                    _raise_if_stopped(cancel, pause)
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total or done)
    except DownloadPaused:
        raise  # keep part + checkpoint
    except DownloadCancelled:
        ckpt.discard(dest)
        raise

    os.replace(part, dest)
    ckpt.clear(dest)
    return dest


def _run_segmented(
    api: ApiClient, url: str, headers: dict, *, file_id: str, dest: Path,
    total: int, etag: Optional[str], n: int, resume: Optional[ckpt.Checkpoint],
    on_progress: ProgressCb, cancel: Optional[threading.Event],
    pause: Optional[threading.Event],
) -> Path:
    part = ckpt.part_path(dest)
    seg_size = (resume.segment_size if resume else SEGMENT_SIZE) or SEGMENT_SIZE
    segments = _split(total, seg_size)
    completed: set[int] = set(resume.completed) if resume else set()

    if resume is not None and part.exists():
        try:
            if part.stat().st_size != total:  # pre-allocation drift → start fresh
                resume, completed = None, set()
        except OSError:
            resume, completed = None, set()

    if resume is None:
        completed = set()
        with open(part, "wb") as f:
            f.truncate(total)

    def _persist() -> None:
        ckpt.write(
            dest,
            ckpt.Checkpoint(
                file_id=file_id, total=total, etag=etag, mode="segmented",
                segment_size=seg_size, completed=sorted(completed),
            ),
        )

    _persist()

    lock = threading.Lock()
    done_total = sum((e - s + 1) for i, (s, e) in enumerate(segments) if i in completed)

    def _bump(delta: int) -> None:
        nonlocal done_total
        if on_progress is None:
            return
        with lock:
            done_total += delta
            on_progress(max(0, done_total), total)

    if on_progress is not None:
        on_progress(done_total, total)

    todo = [(i, s, e) for i, (s, e) in enumerate(segments) if i not in completed]
    _raise_if_stopped(cancel, pause)
    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            futmap = {
                pool.submit(
                    _fetch_segment, api, url, headers, part, s, e, _bump, cancel, pause
                ): i
                for (i, s, e) in todo
            }
            for fut in as_completed(futmap):
                fut.result()  # re-raise the first failure / cancel / pause
                completed.add(futmap[fut])
                _persist()
    except DownloadPaused:
        if pause is not None:
            pause.set()  # nudge straggler segments to stop fast
        _persist()
        raise
    except DownloadCancelled:
        if cancel is not None:
            cancel.set()
        ckpt.discard(dest)
        raise
    except Exception:
        # Hard failure after retries - keep the partial + checkpoint so the
        # user can resume rather than restart from scratch.
        _persist()
        raise

    os.replace(part, dest)
    ckpt.clear(dest)
    return dest

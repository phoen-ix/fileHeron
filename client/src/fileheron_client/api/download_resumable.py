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
the server's HTTP Range support (Starlette ``FileResponse``). The single-stream
``files.download_file`` entry point is unchanged; the UI calls
``download_file_resumable``.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import wait as futures_wait
from pathlib import Path
from typing import Callable, Optional

from .. import motw
from . import download_checkpoint as ckpt
from .client import ApiClient, ApiError, SessionExpiredError, _envelope_from_response
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

# How long a Cancel waits for in-flight segments to release the .part file
# before deleting it. A ceiling for a socket stalled mid-chunk, not the expected
# cost: the stop signal is already set, so a segment normally exits at its next
# 1 MiB boundary and this returns immediately.
_STOP_SETTLE_SEC = 2.0


class _EitherEvent:
    """Read-only OR of two stop signals, one of which may be absent.

    ``_fetch_segment`` only ever calls ``.is_set()`` on the cancel/pause it is
    handed, so this is all it takes to give the orchestrator its own private
    stop signal without setting - and thereby misreporting - the caller's."""

    __slots__ = ("_a", "_b")

    def __init__(
        self, a: Optional[threading.Event], b: threading.Event
    ) -> None:
        self._a, self._b = a, b

    def is_set(self) -> bool:
        return (self._a is not None and self._a.is_set()) or self._b.is_set()


def _raise_if_stopped(
    cancel: Optional[threading.Event], pause: Optional[threading.Event]
) -> None:
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled
    if pause is not None and pause.is_set():
        raise DownloadPaused


def _probe(
    api: ApiClient, url: str, headers: dict, _retried: bool = False
) -> Optional[tuple[int, Optional[str]]]:
    """One tiny ranged GET. Returns (total, etag) iff the server honours
    ranges (HTTP 206 with a parseable Content-Range), else None.

    Asks for exactly one byte, and that is what makes it free: the server
    charges a ranged download on how much is being TAKEN, not on where it
    starts, so a single-byte range is a size probe and anything larger is a
    download (``utils/http_range.is_metadata_probe``).

    This docstring used to justify ``bytes=1-1`` differently - "a start > 0 is
    treated as an uncounted continuation" - and server v2.6.0 removed exactly
    that property, because any client could assert it and take a whole file for
    free. The probe was then charged like a download, so a first transfer cost
    two units and a ``download_limit=1`` share became undownloadable from this
    client while a browser could still fetch it. Server v2.6.1 restored the
    exemption on the honest ground. Do not widen the probe past one byte: the
    slack is what the server measures.

    A file of 1 byte or fewer is not probe-able (the "probe" would be the whole
    file); the server declines, and the single-stream path below handles it -
    correct, since such a file needs no segmentation.
    """
    try:
        with api._http.stream(
            "GET", url, headers={**headers, **api.auth_header(), "Range": "bytes=1-1"},
            follow_redirects=True,
        ) as resp:
            if resp.status_code == 401:
                # The probe runs before every transfer, so refreshing here is
                # what gives the download that follows a live token. Without it
                # an expired session surfaced as `503 RESUME_PROBE_FAILED` -
                # "Couldn't reach the server" - which is a lie about the cause
                # and repeats forever, because nothing else refreshes either.
                resp.read()
                if _retried:
                    return None
                headers.update(api.refresh_bearer_header())
                return _probe(api, url, headers, _retried=True)
            cr = resp.headers.get("Content-Range", "")
            etag = resp.headers.get("ETag")
            resp.read()  # drain so the connection is reusable
            if resp.status_code != 206 or "/" not in cr:
                return None
            tail = cr.rsplit("/", 1)[1].strip()
            return (int(tail), etag) if tail.isdigit() else None
    except SessionExpiredError:
        # Must NOT be swallowed. Returning None here sets ranges_ok=False, and
        # with a checkpoint present the caller then raises 503
        # RESUME_PROBE_FAILED - "Couldn't reach the server to resume this
        # download" - which misreports an expired session as a network fault
        # and repeats forever, since nothing refreshes. The UI listens for this
        # exception specifically and returns the user to the login screen.
        raise
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
    # Read fresh rather than snapshotting: this dict used to be built once and
    # reused for every segment of a multi-hour transfer, so the whole download
    # died the moment the 15-minute access token expired.
    headers = api.auth_header()
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


def _finalize(api: ApiClient, part: Path, dest: Path, total: Optional[int] = None) -> None:
    """Move the completed partial into place and mark it Internet-zone.

    The mark goes on the FINAL name: an alternate data stream belongs to the
    file, and writing it to the .part would work but tells the shell about a
    file the user never sees.

    `total` is the size the server said to expect. Checking it here is the last
    line of defence: nothing else compared the finished file against it, so a
    transfer that ended short - or long, if a peer ignored `Range` and every
    worker wrote a whole copy - was renamed into place and reported as success.
    A wrong file that looks finished is worse than a failed download, so refuse
    the rename and keep the .part for a resume."""
    if total is not None:
        try:
            actual = part.stat().st_size
        except OSError as e:
            raise OSError(f"cannot stat downloaded file: {e}") from e
        if actual != total:
            raise OSError(
                f"downloaded {actual} bytes, expected {total} - refusing to "
                "save a file that does not match the server"
            )
    ckpt.replace_with_retry(part, dest)
    motw.tag_downloaded(dest, host_url=str(api.server_url).rstrip("/"))


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
            # `>=`, so a partial LARGER than the server's size lands here too -
            # exactly the shape an ignored-Range overwrite leaves behind. Pass
            # `total` so _finalize refuses it instead of renaming it into place.
            _finalize(api, part, dest, total)
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

    # The checkpoint is written AFTER the response headers, not before the
    # request: when the initial probe fails, `total` here is None, and a
    # checkpoint recording total=0 can never match the real size on a later
    # resume - so the partial is discarded and every byte already downloaded is
    # thrown away, silently (audit 2026-07-30, client-6). The write moved below,
    # where the response has told us the real total.
    _raise_if_stopped(cancel, pause)
    try:
        # A single stream cannot expire mid-flight: the server authorises the
        # request once and never re-checks while the body is being sent. So this
        # path only needs a VALID token at request time - which `_probe` above
        # has already refreshed if it had gone stale - and reads the header
        # fresh rather than using the snapshot taken at kick-off.
        #
        # follow_redirects for an S3 backend's 307 -> presigned URL.
        with api._http.stream(
            "GET", url, headers={**req_headers, **api.auth_header()},
            follow_redirects=True,
        ) as resp:
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

            # Now the size is known (from Content-Range on a 206 or
            # Content-Length on a 200), so the checkpoint records something a
            # resume can actually match.
            ckpt.write(
                dest,
                ckpt.Checkpoint(
                    file_id=file_id, total=total or 0, etag=etag, mode="single"
                ),
            )

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
        raise  # keep part + checkpoint - that is what makes Resume work
    except DownloadCancelled:
        ckpt.discard(dest)
        raise
    except Exception:
        # A transport/server failure keeps the partial ONLY if it is actually
        # resumable. Without this, a download that died before any bytes landed
        # left a .fhdownload sidecar (and possibly an empty .part) sitting in
        # the user's Downloads folder forever, next to no file - the app has no
        # UI that ever mentions it again (audit 2026-07-30, client-7).
        try:
            if not part.exists() or part.stat().st_size == 0:
                ckpt.discard(dest)
        except OSError:
            ckpt.discard(dest)
        raise

    _finalize(api, part, dest, total)
    ckpt.clear(dest)
    return dest


def _preallocate(part: Path, total: int) -> None:
    """Create ``part`` at its final size so segments can be written at their
    own offsets, and so a resume can check the size for pre-allocation drift.

    Seek-then-write-one-byte, NOT ``truncate(total)``. Python's ``truncate``
    goes through the C runtime's ``_chsize_s`` on Windows, which zero-FILLS the
    new region - it physically writes the bytes. On a platform whose whole
    purpose here is 30 GB files, that means a multi-GB download sits at 0% with
    no network traffic while the disk writes the file once in zeros before the
    first byte arrives, then writes it again for real. Extending by writing a
    single byte at the end sets the size through ``SetFilePointer`` +
    ``WriteFile`` instead: NTFS tracks a valid-data-length and returns zeros
    for the gap without materialising it, and on Linux the gap is a hole. The
    file is the same size and reads the same either way.
    """
    with open(part, "wb") as f:
        if total > 0:
            f.seek(total - 1)
            f.write(b"\0")


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
        _preallocate(part, total)

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

    # NOT `with ThreadPoolExecutor(...)`. Its __exit__ is shutdown(wait=True),
    # which lets every queued segment run and blocks until every in-flight one
    # finishes. On the hard-failure path that is the whole remaining transfer:
    # one segment exhausts its retries, and the orchestrator then waits for the
    # other three to download their full spans - on a 30 GB file, hours - only
    # to throw the result away and raise the error it already had.
    #
    # It also made the two `.set()` calls below unreachable AS nudges: they ran
    # after __exit__ had already drained the pool, so there was no straggler
    # left to see the event. The comment said "nudge straggler segments to stop
    # fast" and could not.
    #
    # `_stop_now` is that nudge, and it is a SEPARATE event rather than the
    # caller's. `cancel`/`pause` are owned by the UI, which reads them back to
    # decide what it is showing - setting one to make a worker exit would tell
    # the user they had paused a download that actually failed. _fetch_segment
    # only ever calls `.is_set()` on what it is given, so _EitherEvent is
    # enough to add a second stop signal without touching the first.
    stop_now = threading.Event()
    seg_pause = _EitherEvent(pause, stop_now)

    pool = ThreadPoolExecutor(max_workers=n)
    futmap: dict = {}

    def _stop() -> None:
        # Order matters. Signal FIRST, so running segments see it at their next
        # 1 MiB chunk, THEN drop whatever is still queued and return without
        # waiting.
        stop_now.set()
        pool.shutdown(wait=False, cancel_futures=True)
        # Sweep the segments that finished but whose future we never consumed:
        # as_completed stops at the first raiser, so their bytes were already on
        # disk and correct while the checkpoint recorded them as outstanding,
        # and the resume re-downloaded every one of them.
        for fut, idx in futmap.items():
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                completed.add(idx)

    try:
        futmap = {
            pool.submit(
                _fetch_segment, api, url, headers, part, s, e, _bump, cancel, seg_pause
            ): i
            for (i, s, e) in todo
        }
        for fut in as_completed(futmap):
            fut.result()  # re-raise the first failure / cancel / pause
            completed.add(futmap[fut])
            _persist()
    except DownloadPaused:
        _stop()
        _persist()
        raise
    except DownloadCancelled:
        _stop()
        # The only path that DELETES the partial, so it is the only one that
        # waits: on Windows a segment still holding the handle blocks the
        # unlink. Bounded and normally instant, because stop_now above makes a
        # straggler exit at its next chunk. unlink_with_retry (~1.55s) remains
        # the backstop, so a timeout costs a best-effort delete, not a failed
        # cancel.
        futures_wait(list(futmap), timeout=_STOP_SETTLE_SEC)
        ckpt.discard(dest)
        raise
    except Exception:
        # Hard failure after retries - keep the partial + checkpoint so the
        # user can resume rather than restart from scratch.
        _stop()
        _persist()
        raise
    finally:
        pool.shutdown(wait=False)

    _finalize(api, part, dest, total)
    ckpt.clear(dest)
    return dest

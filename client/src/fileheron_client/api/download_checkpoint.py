"""Resumable-download checkpoint sidecar.

A download writes its bytes into ``<dest>.part`` and a small JSON
``<dest>.fhdownload`` checkpoint that records enough to resume after an
interruption or a pause: the remote total + validator (ETag) so we can prove
the file hasn't changed, the transfer mode, and - for the segmented path -
which 16 MiB segments are already fully written. Single-stream mode needs no
per-segment bookkeeping (the ``.part`` size IS the contiguous offset).

Both sidecars sit next to the user's chosen destination and are removed on a
successful rename into place or an explicit discard.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

_log = logging.getLogger("fileheron_client.api.download_checkpoint")

CKPT_SUFFIX = ".fhdownload"
PART_SUFFIX = ".part"
VERSION = 1


@dataclass
class Checkpoint:
    file_id: str
    total: int
    etag: Optional[str]
    mode: str  # "single" | "segmented"
    segment_size: int = 0
    completed: list[int] = field(default_factory=list)
    version: int = VERSION


# A file that was open a moment ago is usually closeable a moment later. On
# Windows an on-access antivirus scanner, a search indexer, an Explorer preview
# handler or a thumbnail cache routinely holds a just-written file open for a
# fraction of a second, and DeleteFile / MoveFileEx answer ERROR_SHARING_VIOLATION
# (PermissionError) rather than waiting - there is no POSIX equivalent, where an
# open handle is no obstacle to either. Retrying for a moment is the standard
# response; the alternative is to fail a completed download because a scanner
# happened to be reading it.
_RETRY_DELAYS_SEC = (0.05, 0.1, 0.2, 0.4, 0.8)


def replace_with_retry(src: Path, dest: Path) -> None:
    """``os.replace`` that tolerates a transient Windows sharing violation.

    Raises the last ``OSError`` if the destination stays locked, so a real
    conflict (a genuinely open file, a read-only destination) still surfaces
    instead of being swallowed.
    """
    _retry(lambda: os.replace(src, dest))


def unlink_with_retry(path: Path) -> None:
    """``Path.unlink(missing_ok=True)`` with the same tolerance. Best-effort:
    a file that is still locked at the end is left for the OS to reap rather
    than raising into a cancel path."""
    try:
        _retry(lambda: path.unlink(missing_ok=True))
    except OSError:
        _log.debug("could not remove %s (still locked)", path)


def _retry(op) -> None:
    import time

    for delay in _RETRY_DELAYS_SEC:
        try:
            op()
            return
        except PermissionError:
            time.sleep(delay)
    op()  # last attempt: let the error propagate with a real traceback


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + PART_SUFFIX)


def ckpt_path(dest: Path) -> Path:
    return dest.with_name(dest.name + CKPT_SUFFIX)


def read(dest: Path) -> Optional[Checkpoint]:
    """Return the checkpoint for ``dest`` or None if missing/corrupt."""
    p = ckpt_path(dest)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return Checkpoint(
            file_id=str(raw["file_id"]),
            total=int(raw["total"]),
            etag=raw.get("etag"),
            mode=str(raw["mode"]),
            segment_size=int(raw.get("segment_size", 0)),
            completed=[int(i) for i in raw.get("completed", [])],
            version=int(raw.get("version", VERSION)),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def write(dest: Path, cp: Checkpoint) -> None:
    """Atomically persist the checkpoint next to ``dest`` (best-effort)."""
    p = ckpt_path(dest)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(asdict(cp), indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    except OSError as exc:
        _log.warning("checkpoint write failed for %s: %r", p, exc)


def clear(dest: Path) -> None:
    """Remove the checkpoint sidecar (call after a successful rename). Leaves
    the ``.part`` alone - on success it's already been renamed away."""
    unlink_with_retry(ckpt_path(dest))


def discard(dest: Path) -> None:
    """Drop BOTH the partial bytes and the checkpoint (cancel / stale).

    Retries a locked ``.part``: swallowing the sharing violation outright left
    a scanned-at-the-wrong-moment partial in the user's Downloads folder
    forever, with its registry entry already gone, so nothing in the app ever
    mentioned it again.
    """
    for p in (part_path(dest), ckpt_path(dest)):
        unlink_with_retry(p)

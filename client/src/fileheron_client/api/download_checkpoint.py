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
    try:
        ckpt_path(dest).unlink(missing_ok=True)
    except OSError:
        pass


def discard(dest: Path) -> None:
    """Drop BOTH the partial bytes and the checkpoint (cancel / stale)."""
    for p in (part_path(dest), ckpt_path(dest)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

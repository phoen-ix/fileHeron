"""Persistent index of in-flight / paused / interrupted downloads.

The checkpoint sidecars (``<dest>.part`` + ``<dest>.fhdownload``) hold the
bytes and the resume offsets, but they live next to a user-chosen destination
that the app doesn't otherwise remember. This small JSON registry - stored in
the config dir - maps ``file_id -> {dest, …, status}`` so the share view can
surface a **Resume** button for a partial download even after the app has been
closed and reopened.

Keyed by ``file_id`` (a file is normally saved once); a second save of the
same file to a different path supersedes the first entry. Best-effort and
crash-safe (atomic temp+rename); a corrupt file degrades to an empty registry
rather than crashing the UI.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .config import _config_dir

_log = logging.getLogger("fileheron_client.downloads_registry")

# status: "active" (running) | "paused" (user paused) | "interrupted" (error /
# app closed mid-flight). Only paused/interrupted are offered as resumable.
ACTIVE = "active"
PAUSED = "paused"
INTERRUPTED = "interrupted"
RESUMABLE = (PAUSED, INTERRUPTED)

_lock = threading.Lock()


def _registry_path() -> Path:
    return _config_dir() / "downloads.json"


def _load_unlocked() -> dict[str, dict]:
    p = _registry_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_unlocked(reg: dict[str, dict]) -> None:
    p = _registry_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(reg, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    except OSError as exc:
        _log.warning("downloads registry write failed: %r", exc)


def load() -> dict[str, dict]:
    with _lock:
        return _load_unlocked()


def get(file_id: str) -> Optional[dict]:
    with _lock:
        return _load_unlocked().get(file_id)


def upsert(
    file_id: str,
    *,
    dest: str,
    filename: str,
    total: int,
    bytes_done: int = 0,
    status: str = ACTIVE,
    share_id: Optional[str] = None,
) -> None:
    with _lock:
        reg = _load_unlocked()
        reg[file_id] = {
            "file_id": file_id,
            "dest": dest,
            "filename": filename,
            "total": int(total),
            "bytes_done": int(bytes_done),
            "status": status,
            "share_id": share_id,
            "updated_at": time.time(),
        }
        _save_unlocked(reg)


def set_total(file_id: str, total: int) -> None:
    """Record the transfer size once it is known.

    `upsert` runs BEFORE the first byte, when the size is not known yet, so
    every fresh download was registered with total=0 - and a Resume offered
    after an app restart read that 0 and could show neither a percentage nor a
    progress bar (audit 2026-07-30, client-5). No-op when nothing changes, so
    this is safe to call from the progress tick."""
    if total <= 0:
        return
    with _lock:
        reg = _load_unlocked()
        row = reg.get(file_id)
        if row is None or int(row.get("total") or 0) == int(total):
            return
        row["total"] = int(total)
        row["updated_at"] = time.time()
        _save_unlocked(reg)


def set_status(file_id: str, status: str, *, bytes_done: Optional[int] = None) -> None:
    with _lock:
        reg = _load_unlocked()
        row = reg.get(file_id)
        if row is None:
            return
        row["status"] = status
        if bytes_done is not None:
            row["bytes_done"] = int(bytes_done)
        row["updated_at"] = time.time()
        _save_unlocked(reg)


def remove(file_id: str) -> None:
    with _lock:
        reg = _load_unlocked()
        if reg.pop(file_id, None) is not None:
            _save_unlocked(reg)


def reconcile_on_startup() -> None:
    """Promote any leftover ``active`` entry to ``interrupted`` at launch.

    An ``active`` row that survives an app restart can only be from a session
    that ended mid-download (crash / force-quit), since a clean finish removes
    the row and a pause/error sets paused/interrupted. Promoting it makes the
    share view offer a Resume button instead of silently dropping it."""
    with _lock:
        reg = _load_unlocked()
        changed = False
        for row in reg.values():
            if row.get("status") == ACTIVE:
                row["status"] = INTERRUPTED
                changed = True
        if changed:
            _save_unlocked(reg)

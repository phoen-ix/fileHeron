"""The folder a file was uploaded from (folder uploads, v2.26.0).

`files.original_filename` stays a bare leaf - every flattener downstream relies
on that: the ZIP's `safe_arcname`, the desktop client's `safe_join` and every
Content-Disposition. The folder travels separately in `files.relative_dir`, and
this is the one function that decides what may be stored there or placed in an
archive, so the rule cannot drift between the two.
"""
from __future__ import annotations

import re

# Deeper than any real project tree; a path deeper than this keeps its first
# MAX_DEPTH folders rather than being refused, so the structure survives.
MAX_DEPTH = 32
# Per folder name, the same ceiling common filesystems put on one name.
MAX_SEGMENT = 255

_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_relative_dir(raw: str | None, *, width: int) -> str | None:
    """A cleaned folder path like ``Photos/2026/Summer``, or None (top level).

    Refuses - returns None, so the file lands at the top level rather than
    anywhere unexpected - a path that carries a ``..`` segment (or any other
    all-dots one) or starts with a drive (``C:``). Backslashes are separators (a Windows client may send
    them), and empty and ``.`` segments are dropped, which also turns a leading
    ``/`` into a relative path. Each kept segment loses control characters and
    surrounding whitespace and is clipped to MAX_SEGMENT. At most MAX_DEPTH
    segments are kept, and trailing segments are dropped until the whole path
    fits `width` - a segment is never cut mid-name to make it fit.
    """
    if not raw:
        return None
    text = raw.replace("\\", "/")
    if _DRIVE.match(text.lstrip("/")):
        return None
    segments: list[str] = []
    for part in text.split("/"):
        # Clip BEFORE the checks: clipping after them could cut `..` + 253
        # spaces + `x` down to a bare `..` that no check ever saw.
        seg = "".join(ch for ch in part if ch >= " " and ch != "\x7f")[:MAX_SEGMENT].strip()
        if seg in ("", "."):
            continue
        if set(seg) == {"."}:
            # `..` is the parent; `...` is one too once Windows strips the
            # trailing dots it ignores in names.
            return None
        segments.append(seg)
    segments = segments[:MAX_DEPTH]
    while segments and len("/".join(segments)) > width:
        segments.pop()
    return "/".join(segments) or None

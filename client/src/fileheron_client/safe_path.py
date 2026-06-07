"""Filename hardening for downloads (audit H4).

The server controls each file's ``original_filename``. When bulk-saving ("Save
all to folder") the client must NOT trust that string as a path: a ``../``, an
absolute path, a UNC path, or a Windows reserved device name could otherwise
write OUTSIDE the chosen folder - arbitrary file write, which can escalate to
code execution via an auto-run / Startup location. This module reduces a
server-supplied name to a safe single path segment and de-duplicates collisions.

Kept tkinter-free so it is importable + unit-testable in CI (which has no Tk).
The single-file "Download" path is unaffected: there the user names the file via
the native save dialog.
"""
from __future__ import annotations

from pathlib import Path

_WIN_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_download_leaf(name: str) -> str:
    """Reduce a server-supplied filename to a safe single path segment.

    Strips directory components under BOTH separators (a reply may carry either
    on either OS), drops control/NUL chars and trailing dots/spaces (Windows
    ignores them - a known confusion vector), and rejects ``.``/``..``/empty and
    reserved device names. Always returns a usable leaf (``file`` as fallback).
    """
    leaf = (name or "").replace("\\", "/").split("/")[-1]
    leaf = "".join(ch for ch in leaf if ch >= " ").strip().rstrip(". ")
    if not leaf or leaf in (".", ".."):
        return "file"
    if leaf.split(".", 1)[0].upper() in _WIN_RESERVED:
        leaf = "_" + leaf
    return leaf[:255]


def unique_leaf(leaf: str, used_lower: set[str]) -> str:
    """Return ``leaf`` or a ``name (n).ext`` variant not already in ``used_lower``
    (a set of lower-cased names), so sanitized collisions don't overwrite each
    other. Mutates ``used_lower`` with the chosen name."""
    candidate = leaf
    if candidate.lower() in used_lower:
        if "." in leaf:
            stem, _, ext = leaf.rpartition(".")
            tmpl = f"{stem} ({{n}}).{ext}"
        else:
            tmpl = f"{leaf} ({{n}})"
        n = 1
        while True:
            candidate = tmpl.format(n=n)
            if candidate.lower() not in used_lower:
                break
            n += 1
    used_lower.add(candidate.lower())
    return candidate


def safe_join(base: Path, name: str, used_lower: set[str] | None = None) -> Path:
    """Join a sanitized (and optionally de-duplicated) leaf under ``base`` and
    assert containment. Raises ``ValueError`` if the result would escape
    ``base`` (belt-and-braces after leaf sanitization)."""
    leaf = safe_download_leaf(name)
    if used_lower is not None:
        leaf = unique_leaf(leaf, used_lower)
    dest = base / leaf
    base_r = base.resolve()
    dest_r = dest.resolve()
    if base_r != dest_r.parent and base_r not in dest_r.parents:
        raise ValueError(f"unsafe download path for {name!r}")
    return dest

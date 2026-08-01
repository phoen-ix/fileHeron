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
    # Microsoft documents the SUPERSCRIPT forms as reserved too: Windows maps
    # COM¹ COM² COM³ (and the LPT equivalents) onto the same devices. `COM².log`
    # is an ordinary serial-capture name on Linux, and opening it here would go
    # to the serial port rather than the filesystem.
    *(f"COM{d}" for d in "¹²³"),
    *(f"LPT{d}" for d in "¹²³"),
}

# Characters Windows forbids in a filename. Stripping them is not cosmetic:
#
#   ':'  is the worst of the set. It makes a name DRIVE-RELATIVE or names an
#        NTFS alternate data stream, and neither survives being treated as a
#        leaf. ``Downloads / "C:report.pdf"`` is ``Downloads\report.pdf`` -
#        the drive prefix is silently dropped, so it collides with a genuine
#        ``report.pdf`` in the same share while the de-dup set still believes
#        the two names differ, and both download threads write one file at
#        once. ``Downloads / "D:report.pdf"`` leaves the folder entirely (a
#        different drive's working directory), which safe_join can only answer
#        by raising. ``Downloads / "notes.txt:hidden"`` writes into a stream of
#        ``notes.txt`` that no file manager displays.
#   <>"|?*  are rejected outright by the Win32 layer, so the download dies
#        mid-flight with an OSError the user cannot act on.
#
# The whole point of this module is that the server names these files, so none
# of the above can be dismissed as a malformed-input edge case.
_WIN_FORBIDDEN = '<>:"|?*'

# Stock Windows caps a full path at 260 characters including the terminating
# NUL, so 259 usable. Long-path support exists but is opt-in per machine.
_MAX_PATH = 259
_DEDUP_MARGIN = 6  # room for a " (99)" suffix from unique_leaf
_MIN_STEM = 8


def safe_download_leaf(name: str) -> str:
    """Reduce a server-supplied filename to a safe single path segment.

    Strips directory components under BOTH separators (a reply may carry either
    on either OS), drops control/NUL chars, the characters Windows forbids (see
    ``_WIN_FORBIDDEN``) and trailing dots/spaces (Windows ignores them - a known
    confusion vector), and rejects ``.``/``..``/empty and reserved device names.
    Always returns a usable leaf (``file`` as fallback).

    Sanitises for Windows on every platform, deliberately. This client ships as
    a Windows .exe; a name that is unsafe there has to be rejected wherever the
    tests happen to run, or the suite passes on Linux and the product breaks.
    """
    leaf = (name or "").replace("\\", "/").split("/")[-1]
    leaf = "".join(
        ch for ch in leaf if ch >= " " and ch not in _WIN_FORBIDDEN
    ).strip().rstrip(". ")
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


def shorten_to_fit(base: Path, leaf: str, limit: int = _MAX_PATH) -> str:
    """Trim ``leaf`` so ``base/leaf`` fits ``limit`` characters, keeping the
    extension (which is what decides the icon and the default application).

    The 255 cap in :func:`safe_download_leaf` is a POSIX *leaf* cap; stock
    Windows still enforces a 260-character cap on the WHOLE path unless long
    paths have been enabled, and the folder half of that is the user's choice,
    not ours. Without this, a legitimately long name inside a deep folder dies
    at ``open()`` with an OSError that names neither cause nor remedy.

    Leaves ``leaf`` alone when it already fits, and never trims below a usable
    stem - a base so deep that nothing fits is the caller's problem to report,
    not something to paper over with a one-character filename.
    """
    room = limit - len(str(base)) - 1  # separator
    if len(leaf) <= room:
        return leaf
    stem, dot, ext = leaf.rpartition(".")
    if not dot:
        stem, ext = leaf, ""
    keep = room - len(ext) - len(dot) - _DEDUP_MARGIN
    if keep < _MIN_STEM:
        return leaf  # cannot help; let the write report the real error
    return f"{stem[:keep]}{dot}{ext}"


def safe_join(base: Path, name: str, used_lower: set[str] | None = None) -> Path:
    """Join a sanitized (and optionally de-duplicated) leaf under ``base`` and
    assert containment. Raises ``ValueError`` if the result would escape
    ``base`` (belt-and-braces after leaf sanitization)."""
    leaf = safe_download_leaf(name)
    # Shorten BEFORE de-duplicating: trimming a name afterwards could collide it
    # back into one already handed out, and _DEDUP_MARGIN leaves room for the
    # " (n)" suffix that de-duplication may add.
    leaf = shorten_to_fit(base, leaf)
    if used_lower is not None:
        leaf = unique_leaf(leaf, used_lower)
    dest = base / leaf
    base_r = base.resolve()
    dest_r = dest.resolve()
    if base_r != dest_r.parent and base_r not in dest_r.parents:
        raise ValueError(f"unsafe download path for {name!r}")
    return dest

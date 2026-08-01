"""Mark-of-the-Web for downloaded files (Windows).

Windows decides how cautious to be with a file from its **zone**, which is
recorded in an NTFS alternate data stream named ``Zone.Identifier``. Zone 3 is
"Internet". Every browser writes it on download, and it is what makes
SmartScreen warn before an unrecognised executable runs, what puts Office
documents into Protected View, and what makes the shell's "this file came from
another computer" property appear.

Nothing in this client wrote it, so a file saved here was indistinguishable
from one the user authored locally - and the client offers a one-click **Open**
next to it, which is a ShellExecute of a name the *server* chose. The other
half of that threat model is already taken seriously (see
:mod:`fileheron_client.safe_path`, which treats ``original_filename`` as
hostile); this is the missing half.

Best-effort by design: no-op off Windows, on a filesystem without stream
support (FAT32, most USB sticks, some network shares), or if the write fails
for any other reason. A missing mark leaves the file exactly as it was before
this module existed, so failing loudly would trade a real download for a
defence-in-depth measure.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_log = logging.getLogger("fileheron_client.motw")

URLZONE_INTERNET = 3

# ZoneTransfer is an INI-shaped stream. HostUrl is what the shell shows in the
# file's properties; recording where the bytes actually came from is the point.
_TEMPLATE = "[ZoneTransfer]\r\nZoneId={zone}\r\n"
_TEMPLATE_WITH_HOST = "[ZoneTransfer]\r\nZoneId={zone}\r\nHostUrl={host}\r\n"


def tag_downloaded(path: Path, *, host_url: str | None = None) -> bool:
    """Mark ``path`` as Internet-zone content. Returns whether it was written.

    Call after the bytes are final and under their real name - the stream
    belongs to the file, and a rename carries it along, but writing it to a
    ``.part`` that is later replaced would lose it.
    """
    if not sys.platform.startswith("win"):
        return False
    body = (
        _TEMPLATE_WITH_HOST.format(zone=URLZONE_INTERNET, host=host_url)
        if host_url
        else _TEMPLATE.format(zone=URLZONE_INTERNET)
    )
    try:
        # ':Zone.Identifier' names a stream of the file, not a sibling path -
        # the one legitimate use of the syntax that safe_path strips out of
        # server-supplied names.
        with open(f"{path}:Zone.Identifier", "w", encoding="utf-8") as fh:
            fh.write(body)
        return True
    except OSError as exc:
        _log.debug("could not write Zone.Identifier for %s: %s", path, exc)
        return False

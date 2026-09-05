"""Segment-plan helper (``_split``).

The single-stream cancel test that used to sit here exercised the legacy
``files.download_file``, which no UI path called; the resumable downloader's
own cancel semantics are pinned in ``test_download_resume.py``.
"""
from __future__ import annotations

from fileheron_client.api import download_segmented as seg


def test_split_inclusive_ranges():
    assert seg._split(50, 10) == [(0, 9), (10, 19), (20, 29), (30, 39), (40, 49)]
    assert seg._split(25, 10) == [(0, 9), (10, 19), (20, 24)]
    assert seg._split(5, 10) == [(0, 4)]

"""H4: server-supplied download filenames are reduced to a safe leaf and can't
escape the chosen folder on 'Save all'."""
from __future__ import annotations

from pathlib import Path

import pytest

from fileheron_client.safe_path import safe_download_leaf, safe_join, unique_leaf


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\Windows\\System32\\evil.bat", "evil.bat"),
        ("/abs/path/report.pdf", "report.pdf"),
        ("C:\\Users\\victim\\Startup\\x.bat", "x.bat"),
        ("normal.txt", "normal.txt"),
        ("..", "file"),
        (".", "file"),
        ("", "file"),
        ("trailing. ", "trailing"),
        ("with\x00nul.txt", "withnul.txt"),
        ("archive.tar.gz", "archive.tar.gz"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
    ],
)
def test_safe_download_leaf(raw, expected):
    assert safe_download_leaf(raw) == expected


def test_unique_leaf_dedupes():
    used: set[str] = set()
    assert unique_leaf("a.txt", used) == "a.txt"
    assert unique_leaf("a.txt", used) == "a (1).txt"
    assert unique_leaf("a.txt", used) == "a (2).txt"
    assert unique_leaf("b", used) == "b"
    assert unique_leaf("b", used) == "b (1)"


def test_safe_join_stays_within_base(tmp_path):
    base = Path(tmp_path)
    # A traversal payload lands as a leaf inside base, never outside.
    dest = safe_join(base, "../../../../etc/passwd")
    assert dest.parent == base.resolve() or dest.parent == base
    assert dest.name == "passwd"
    assert str(base.resolve()) in str(dest.resolve())

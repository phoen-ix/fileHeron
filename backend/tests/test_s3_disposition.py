"""Regression: S3 presigned Content-Disposition must RFC 5987-encode the
filename (the naive f-string broke on non-ASCII / quote-bearing names)."""
from __future__ import annotations

from urllib.parse import unquote

from app.services.storage_backend import _content_disposition


def test_content_disposition_encodes_non_ascii_and_quotes():
    cd = _content_disposition("attachment", 'Rechnung "Q1" café.pdf')
    assert cd.startswith("attachment; filename=\"")
    # ASCII fallback carries no raw quote/backslash that would break the header.
    fallback = cd.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in fallback and "\\" not in fallback
    # The real name round-trips through the RFC 5987 filename*.
    star = cd.split("filename*=UTF-8''", 1)[1]
    assert unquote(star) == 'Rechnung "Q1" café.pdf'

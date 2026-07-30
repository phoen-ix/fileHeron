"""Untrusted values must not reach response or mail headers unclamped.

Two Tier 3 findings, both of which turned out to be different defects than
filed - worth recording, because the corrected shape is what the fix targets.

`injection-2`/`publiclink-10` (media type): `files.mime_type` is whatever the
client announced at upload. The preview routes pinned it through
preview.safe_content_type, but the two DOWNLOAD routes passed it verbatim.

`injection-4` (mail subject): filed as header injection. It is not - Python's
EmailMessage raises ValueError on CR/LF in a header value, so nothing is
injected. The real defect is worse in a subtle way: three notification subjects
interpolate the SENDER's display name, `.strip()` only removes surrounding
whitespace, so a user with an embedded newline in their display name silently
killed the notification emails sent to OTHER people.
"""
from __future__ import annotations

import pytest

from app.services.storage_backend import safe_media_type
from app.utils.emailing import _header_safe

HOSTILE_TYPES = [
    "text/plain\r\nX-Injected: yes",
    "text/plain\nSet-Cookie: a=b",
    "text/plain\x00",
    "text/plain; charset=\"</script>\"",
    "not a media type",
    "",
    None,
]


@pytest.mark.parametrize("value", HOSTILE_TYPES)
def test_safe_media_type_never_emits_a_control_character(value):
    out = safe_media_type(value)
    assert "\r" not in out and "\n" not in out and "\x00" not in out
    # Either a clean token/token, or the fallback.
    assert out == "application/octet-stream" or "/" in out


def test_safe_media_type_keeps_ordinary_types():
    assert safe_media_type("application/pdf") == "application/pdf"
    assert safe_media_type("image/png") == "image/png"


def test_safe_media_type_drops_parameters():
    """Parameters carry the quoted-string grammar and nothing here needs them."""
    assert safe_media_type("text/plain; charset=utf-8") == "text/plain"


@pytest.mark.parametrize(
    "value", ["Bob\nEvil", "Bob\r\nBcc: x@y.z", "Bob\x7fX", "Bob\x00X"]
)
def test_header_safe_strips_control_characters(value):
    out = _header_safe(value)
    assert not any(ch < " " or ch == "\x7f" for ch in out)


def test_header_safe_leaves_normal_subjects_alone():
    assert _header_safe("Grüße from file:Heron") == "Grüße from file:Heron"


def test_sanitised_subject_is_accepted_by_emailmessage():
    """The property that matters: after sanitising, the send cannot raise."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = _header_safe("Bob\nEvil sent you files")  # must not raise
    assert "\n" not in msg["Subject"]


def test_emailmessage_would_have_rejected_the_raw_value():
    """Control: proves the sanitisation is load-bearing, not decorative."""
    from email.message import EmailMessage

    with pytest.raises(ValueError):
        EmailMessage()["Subject"] = "Bob\nEvil sent you files"

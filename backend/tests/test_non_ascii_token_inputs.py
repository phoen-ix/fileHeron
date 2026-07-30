"""Non-ASCII input to a token verifier must be rejected, not crash.

`hmac.compare_digest` raises TypeError on non-ASCII str arguments. Every token
verifier compares a computed hex digest against a value straight off the wire,
so one non-ASCII byte turned "invalid token" into an unhandled 500 -
unauthenticated, across several endpoints (audit 2026-07-30). v2.1.0 fixed this
shape for the public-link password only; the rest were missed.
"""
from __future__ import annotations

import pytest

from app.services import download_token as download_token_svc
from app.services import sse_token as sse_token_svc
from app.services import unsubscribe_token as unsub_svc
from app.services.tus_signing import verify_envelope
from app.utils.crypto import constant_time_equals

# Non-ASCII, lone surrogate, and raw high bytes - the three shapes that break
# naive encode()/compare paths.
HOSTILE = ["ünicode", "\ud800", "🔥" * 8, "abc\x80def"]


@pytest.mark.parametrize("value", HOSTILE)
def test_constant_time_equals_never_raises(value):
    assert constant_time_equals("deadbeef", value) is False
    assert constant_time_equals(value, "deadbeef") is False


@pytest.mark.parametrize("value", HOSTILE)
def test_constant_time_equals_still_matches_identical(value):
    """Encoding must not break real equality."""
    assert constant_time_equals(value, value) is True


@pytest.mark.parametrize("value", HOSTILE)
def test_tus_envelope_rejects_non_ascii_signature(value):
    from app.middleware.errors import AppError

    with pytest.raises(AppError) as exc:
        verify_envelope("eyJ2IjogMX0", value)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("value", HOSTILE)
def test_download_token_rejects_non_ascii(value):
    from app.middleware.errors import AppError

    with pytest.raises(AppError):
        download_token_svc.verify("some-file-id", value)


@pytest.mark.parametrize("value", HOSTILE)
def test_sse_token_rejects_non_ascii(value):
    from app.middleware.errors import AppError

    with pytest.raises(AppError):
        sse_token_svc.verify(value)


@pytest.mark.parametrize("value", HOSTILE)
def test_unsubscribe_token_rejects_non_ascii(value):
    from app.middleware.errors import AppError

    with pytest.raises(AppError):
        unsub_svc.verify(value)

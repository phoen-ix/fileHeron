"""Tamper-proof OIDC connect-state cookie (Batch 3). The connect callback is a
top-level IdP browser redirect with no Bearer, so it trusts the user_id inside the
HMAC-signed state cookie - a forged one must be rejected."""
from __future__ import annotations

from app.routers.oidc_connect import _pack, _unpack


def test_connect_state_cookie_roundtrips_and_rejects_tampering():
    packed = _pack("st4te", "prov-1", 42, "n0nce")
    assert _unpack(packed) == ("st4te", "prov-1", 42, "n0nce")

    # Flip the embedded user_id -> the HMAC no longer matches -> rejected.
    forged = packed.replace("::42::", "::99::")
    assert _unpack(forged) == (None, None, None, None)
    # Unsigned / truncated / empty values are rejected too.
    assert _unpack("st4te::prov-1::42::n0nce") == (None, None, None, None)
    assert _unpack(None) == (None, None, None, None)

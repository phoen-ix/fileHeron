"""The HMAC-SHA256 signature the compact signed tokens share.

Three services (`sse_token`, `unsubscribe_token`, `download_token`) and the
public-link unlock cookie each carried their own `_now` + `_sign`: HMAC the
payload under `JWT_SECRET`, base64url it without padding. One primitive now;
each token keeps its OWN payload shape and domain prefix, so the formats on
the wire are byte-identical to before and no outstanding token is invalidated
(audit 2026-11-07).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone


def now_epoch() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: bytes, *, domain: bytes = b"") -> str:
    """base64url(HMAC-SHA256(JWT_SECRET, domain + payload)), unpadded. `domain`
    is the caller's separation prefix so two tokens with the same payload
    shape can never verify as each other."""
    from ..config import settings

    digest = hmac.new(settings.JWT_SECRET.encode("utf-8"), domain + payload, hashlib.sha256).digest()
    return b64url(digest)

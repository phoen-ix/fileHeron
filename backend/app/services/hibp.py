"""HIBP (Have I Been Pwned) password-breach check via k-anonymity API.

We send only the first 5 chars of the SHA-1 hash; the API returns every
suffix that matches. We then locally compare against the full hash.
The API never sees the password (or the full hash).

- `HIBP_ENABLED` env (default true) gates the check entirely. Set to
  false in air-gapped deploys.
- Cache hits/misses per 5-char prefix in Redis for 1 hour. Repeat
  password attempts (e.g., user fat-fingering during reset) hit cache.
- Network/upstream failures **fail open**: log a warning and return
  False (not breached). Refusing password changes when an external
  service is down is worse UX than the (small) risk of a breached
  password slipping through during the outage.
"""
from __future__ import annotations

import hashlib
import logging

import httpx
import redis.asyncio as aioredis

from ..config import settings

logger = logging.getLogger("fileheron.hibp")

PWNED_API_RANGE_URL = "https://api.pwnedpasswords.com/range"
CACHE_KEY_PREFIX = "fh:hibp:"
CACHE_TTL_SEC = 3600  # 1 hour
HTTP_TIMEOUT_SEC = 4.0


def _redis() -> aioredis.Redis:
    return aioredis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True
    )


async def _fetch_range(prefix5: str) -> str | None:
    """Returns the response body (multi-line `<suffix35>:<count>\n…`) or
    None on network/upstream/cache failure.

    Fail-open everywhere: Redis unreachable, HIBP unreachable, HIBP
    non-200 — all return None so the caller treats the password as
    not-breached. The security checklist commitment is that HIBP is a
    best-effort gate, not a hard blocker."""
    cache_key = CACHE_KEY_PREFIX + prefix5
    r = _redis()
    try:
        try:
            cached = await r.get(cache_key)
        except Exception as e:
            # Redis down — skip the cache, go straight to HIBP.
            logger.warning("HIBP cache GET failed for prefix %s: %s", prefix5, e)
            cached = None
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as cli:
                resp = await cli.get(
                    f"{PWNED_API_RANGE_URL}/{prefix5}",
                    headers={
                        "Add-Padding": "true",
                        # The official user-agent recommendation; helps
                        # the project track integrations and they ask
                        # for it explicitly.
                        "User-Agent": "fileHeron-hibp/1.0",
                    },
                )
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("HIBP fetch failed for prefix %s: %s", prefix5, e)
            return None

        if resp.status_code != 200:
            logger.warning(
                "HIBP non-200 for prefix %s: %d", prefix5, resp.status_code
            )
            return None

        body = resp.text
        try:
            await r.set(cache_key, body, ex=CACHE_TTL_SEC)
        except Exception:
            # Cache failures are non-fatal.
            pass
        return body
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def is_password_breached(password: str, db=None) -> bool:
    """True iff the password appears in the HIBP corpus.

    Always returns False if HIBP is disabled or the upstream is
    unreachable (fail-open by design). When `db` is supplied the enable
    flag is read live from the admin-tunable settings registry (kv
    overlay, env default); otherwise the env value is used."""
    if db is not None:
        from . import settings_registry
        enabled = settings_registry.effective(db, settings_registry.K.HIBP_ENABLED)
    else:
        enabled = getattr(settings, "HIBP_ENABLED", True)
    if not enabled:
        return False
    if not password:
        return False

    sha1 = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    body = await _fetch_range(prefix)
    if body is None:
        return False  # fail-open

    # Body is `<35-char-suffix>:<count>\n` lines. Padded responses include
    # zero-count entries; we still detect the real one because the suffix
    # match is exact.
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sfx, count_str = line.split(":", 1)
        except ValueError:
            continue
        if sfx.upper() == suffix:
            count = int(count_str.strip())
            return count > 0
    return False
